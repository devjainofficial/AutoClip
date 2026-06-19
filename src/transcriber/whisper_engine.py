import json
import logging
import os

logger = logging.getLogger(__name__)


class TranscribeError(Exception):
    """Raised when transcription fails on all model attempts."""


def transcribe(video_path: str, output_dir: str, model_name: str = "medium") -> dict:
    """
    Transcribe video_path using stable-ts (Whisper with word-level timestamps).
    Results are cached — if transcript JSON already exists, loads and returns it.

    Returns:
        {
            'full_text': str,
            'segments': [{'start': float, 'end': float, 'text': str}],
            'word_timestamps': [{'word': str, 'start': float, 'end': float}],
            'srt_path': str,
            'duration': float,
        }
    """
    import stable_whisper

    os.makedirs(output_dir, exist_ok=True)

    video_id = os.path.splitext(os.path.basename(video_path))[0]
    json_path = os.path.join(output_dir, f"{video_id}_transcript.json")
    srt_path = os.path.join(output_dir, f"{video_id}.srt")

    # Return cached result if both files exist
    if os.path.exists(json_path) and os.path.exists(srt_path):
        logger.info(f"[{video_id}] Transcript cache hit — skipping Whisper")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _run(model_name: str) -> dict:
        logger.info(f"[{video_id}] Loading Whisper model: {model_name}")
        model = stable_whisper.load_model(model_name)
        logger.info(f"[{video_id}] Transcribing...")
        result = model.transcribe(video_path, word_timestamps=True)
        result.to_srt_vtt(srt_path, word_level=True)
        return result

    try:
        result = _run(model_name)
    except (RuntimeError, Exception) as e:
        if "out of memory" in str(e).lower() or "cuda" in str(e).lower():
            logger.warning(f"[{video_id}] OOM with model '{model_name}', retrying with 'small'")
            try:
                result = _run("small")
            except Exception as e2:
                raise TranscribeError(f"[{video_id}] Transcription failed on both '{model_name}' and 'small': {e2}") from e2
        else:
            raise TranscribeError(f"[{video_id}] Transcription failed: {e}") from e

    # Build the canonical output dict
    segments = [
        {"start": float(seg.start), "end": float(seg.end), "text": seg.text.strip()}
        for seg in result.segments
    ]

    word_timestamps = []
    for seg in result.segments:
        for word in (seg.words or []):
            word_timestamps.append({
                "word": word.word.strip(),
                "start": float(word.start),
                "end": float(word.end),
            })

    duration = float(result.segments[-1].end) if result.segments else 0.0

    output = {
        "full_text": " ".join(s["text"] for s in segments),
        "segments": segments,
        "word_timestamps": word_timestamps,
        "srt_path": os.path.abspath(srt_path),
        "duration": duration,
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    logger.info(f"[{video_id}] Transcription done — {len(segments)} segments, {duration:.1f}s")
    return output
