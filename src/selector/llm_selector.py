import json
import logging
import re
import subprocess

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a short-form video editor specialising in extracting viral clips from long-form YouTube content.

Given a video transcript with timestamps, identify the best {max_clips} moments for YouTube Shorts.

Selection criteria:
- Self-contained: clip makes sense without surrounding context
- Strong opening hook in the first 3 seconds
- High energy, surprising insight, emotional peak, or highly useful standalone tip
- Between {min_duration} and {max_duration} seconds long
- Must not start or end mid-sentence

Respond ONLY with a valid JSON array. No explanation. No preamble. No markdown fences.
Format:
[{{"title": "...", "start": 0.0, "end": 0.0, "hook": "..."}}]"""

_RETRY_SUFFIX = "\n\nIMPORTANT: Your previous response was not valid JSON. Return ONLY the JSON array, nothing else."


class NoClipsError(Exception):
    """Raised when no valid clips remain after validation."""


def select_clips(transcript: dict, config) -> list[dict]:
    """
    Send the transcript to GPT-4o-mini and return a list of validated clip dicts.
    Falls back to rule-based silence detection if LLM fails twice.

    Returns:
        [{'title': str, 'start': float, 'end': float, 'hook': str}, ...]
    """
    from openai import OpenAI

    client = OpenAI(api_key=config.openai_api_key)
    system_prompt = _SYSTEM_PROMPT.format(
        max_clips=config.llm_max_clips,
        min_duration=config.llm_min_duration,
        max_duration=config.llm_max_duration,
    )
    user_message = _build_transcript_message(transcript)

    clips = None
    for attempt in range(2):
        prompt = system_prompt if attempt == 0 else system_prompt + _RETRY_SUFFIX
        try:
            response = client.chat.completions.create(
                model=config.llm_model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.3,
            )
            raw = response.choices[0].message.content.strip()
            clips = _parse_clips_json(raw)
            logger.info(f"LLM returned {len(clips)} clips (attempt {attempt + 1})")
            break
        except Exception as e:
            logger.warning(f"LLM attempt {attempt + 1} failed: {e}")

    if clips is None:
        logger.warning("LLM failed — using rule-based fallback")
        clips = _rule_based_fallback(transcript, config)

    validated = _validate_clips(clips, transcript["duration"], config)
    if not validated:
        raise NoClipsError("No valid clips after validation")

    return validated[: config.llm_max_clips]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_transcript_message(transcript: dict) -> str:
    lines = []
    for seg in transcript["segments"]:
        lines.append(f"[{seg['start']:.1f}s - {seg['end']:.1f}s] {seg['text']}")
    return "\n".join(lines)


def _parse_clips_json(raw: str) -> list[dict]:
    # Strip markdown fences if the model added them despite instructions
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw)
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("Expected a JSON array")
    return data


def _validate_clips(clips: list[dict], duration: float, config) -> list[dict]:
    valid = []
    for clip in clips:
        try:
            start = float(clip["start"])
            end = float(clip["end"])
            length = end - start
            if start < 0:
                logger.debug(f"Clip dropped: start < 0 ({start})")
                continue
            if end > duration:
                logger.debug(f"Clip dropped: end {end} > duration {duration}")
                continue
            if length < config.llm_min_duration:
                logger.debug(f"Clip dropped: too short ({length:.1f}s)")
                continue
            if length > config.llm_max_duration:
                logger.debug(f"Clip dropped: too long ({length:.1f}s)")
                continue
            valid.append({
                "title": str(clip.get("title", f"Highlight {len(valid)+1}"))[:100],
                "start": start,
                "end": end,
                "hook": str(clip.get("hook", "")),
            })
        except (KeyError, TypeError, ValueError) as e:
            logger.debug(f"Clip dropped due to parse error: {e}")
    return valid


def _rule_based_fallback(transcript: dict, config) -> list[dict]:
    """
    Use FFmpeg silence detection to find non-silent speech segments,
    then score and return the top N by proximity to 60 seconds.
    """
    import os

    srt_path = transcript.get("srt_path", "")
    # Derive video path from srt_path: .../captions/<id>.srt -> .../source/<id>.mp4
    if srt_path:
        captions_dir = os.path.dirname(srt_path)
        source_dir = os.path.join(os.path.dirname(captions_dir), "source")
        video_id = os.path.splitext(os.path.basename(srt_path))[0]
        video_path = os.path.join(source_dir, f"{video_id}.mp4")
    else:
        logger.warning("Rule-based fallback: no srt_path, using segments directly")
        return _fallback_from_segments(transcript, config)

    if not os.path.exists(video_path):
        return _fallback_from_segments(transcript, config)

    cmd = [
        "ffmpeg", "-i", video_path,
        "-af", "silencedetect=n=-35dB:d=0.5",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    output = result.stderr

    silence_ends = [float(m) for m in re.findall(r"silence_end: ([\d.]+)", output)]
    silence_starts = [float(m) for m in re.findall(r"silence_start: ([\d.]+)", output)]

    # Build speech segments between silences
    speech_segments = []
    prev_end = 0.0
    for s_start in silence_starts:
        if s_start - prev_end >= config.llm_min_duration:
            speech_segments.append((prev_end, s_start))
        # Find the next silence_end after this silence_start
        next_ends = [e for e in silence_ends if e > s_start]
        prev_end = next_ends[0] if next_ends else s_start

    if prev_end < transcript["duration"] - config.llm_min_duration:
        speech_segments.append((prev_end, transcript["duration"]))

    # Filter by duration and score by closeness to 60s
    candidates = [
        (s, e) for s, e in speech_segments
        if config.llm_min_duration <= (e - s) <= config.llm_max_duration
    ]
    candidates.sort(key=lambda x: abs((x[1] - x[0]) - 60))

    return [
        {"title": f"Highlight {i+1}", "start": s, "end": e, "hook": ""}
        for i, (s, e) in enumerate(candidates[: config.llm_max_clips])
    ]


def _fallback_from_segments(transcript: dict, config) -> list[dict]:
    """Last-resort fallback: slice transcript segments into fixed windows."""
    segments = transcript["segments"]
    clips = []
    i = 0
    while i < len(segments) and len(clips) < config.llm_max_clips:
        start = segments[i]["start"]
        end = start
        j = i
        while j < len(segments) and (end - start) < config.llm_min_duration:
            end = segments[j]["end"]
            j += 1
        if config.llm_min_duration <= (end - start) <= config.llm_max_duration:
            clips.append({"title": f"Highlight {len(clips)+1}", "start": start, "end": end, "hook": ""})
        i = j
    return clips
