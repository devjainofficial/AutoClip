import logging
import os
import re

logger = logging.getLogger(__name__)

_FONTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "assets", "fonts")

_ASS_HEADER = """\
[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{style_line}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

_STYLES = {
    "minimal": (
        "Style: Default,Montserrat Bold,18,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
        "0,0,0,0,100,100,0,0,1,0,0,2,10,10,10,1"
    ),
    "bold": (
        "Style: Default,Montserrat ExtraBold,20,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        "0,0,0,0,100,100,0,0,3,2,0,2,10,10,60,1"
    ),
    "highlight": (
        "Style: Default,Montserrat ExtraBold,22,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
        "0,0,0,0,100,100,0,0,1,2,0,2,10,10,400,1"
    ),
}


def build_ass(srt_path: str, style: str, output_path: str) -> str:
    """
    Convert a word-level SRT from stable-ts into an ASS subtitle file.
    Applies the requested style preset (minimal | bold | highlight).
    Returns the path of the generated ASS file.
    """
    if style not in _STYLES:
        raise ValueError(f"Unknown caption style: {style!r}. Choose: minimal, bold, highlight")

    words = _parse_srt(srt_path)
    style_line = _STYLES[style]
    header = _ASS_HEADER.format(style_line=style_line)

    if style == "highlight":
        dialogue_lines = _build_highlight_dialogues(words)
    elif style == "bold":
        dialogue_lines = _build_bold_dialogues(words)
    else:
        dialogue_lines = _build_minimal_dialogues(words)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(header)
        f.write("\n".join(dialogue_lines))

    logger.debug(f"ASS written: {output_path} ({len(dialogue_lines)} events, style={style})")
    return output_path


# ---------------------------------------------------------------------------
# SRT parser
# ---------------------------------------------------------------------------

def _parse_srt(srt_path: str) -> list[dict]:
    """Parse stable-ts word-level SRT into list of {word, start, end}."""
    with open(srt_path, "r", encoding="utf-8") as f:
        content = f.read()

    words = []
    blocks = re.split(r"\n\s*\n", content.strip())
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        # Line 0: index; Line 1: timestamps; Line 2+: text
        ts_match = re.match(
            r"(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})",
            lines[1],
        )
        if not ts_match:
            continue
        start = _ts_to_sec(ts_match.group(1))
        end = _ts_to_sec(ts_match.group(2))
        text = " ".join(lines[2:]).strip()
        if text:
            words.append({"word": text, "start": start, "end": end})
    return words


def _ts_to_sec(ts: str) -> float:
    ts = ts.replace(",", ".")
    h, m, rest = ts.split(":")
    s, ms = rest.split(".")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def _sec_to_ass(s: float) -> str:
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s % 60
    return f"{h}:{m:02d}:{sec:05.2f}"


# ---------------------------------------------------------------------------
# Style renderers
# ---------------------------------------------------------------------------

def _build_minimal_dialogues(words: list[dict]) -> list[str]:
    # Group into ~5-word phrases for readability
    groups = _group_words(words, max_words=5)
    lines = []
    for group in groups:
        start = _sec_to_ass(group[0]["start"])
        end = _sec_to_ass(group[-1]["end"])
        text = " ".join(w["word"] for w in group)
        lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")
    return lines


def _build_bold_dialogues(words: list[dict]) -> list[str]:
    groups = _group_words(words, max_words=5)
    lines = []
    for group in groups:
        start = _sec_to_ass(group[0]["start"])
        end = _sec_to_ass(group[-1]["end"])
        text = " ".join(w["word"].upper() for w in group)
        lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")
    return lines


def _build_highlight_dialogues(words: list[dict]) -> list[str]:
    """
    One Dialogue event per word.
    Active word is gold/yellow; surrounding context is white.
    Groups words into phrases; highlights each word in turn within its phrase.
    """
    WHITE = r"{\c&HFFFFFF&}"
    GOLD = r"{\c&H00D7FF&}"  # ASS BGR for #FFD700

    groups = _group_words(words, max_words=6)
    lines = []

    for group in groups:
        phrase_words = [w["word"] for w in group]
        for i, word_obj in enumerate(group):
            start = _sec_to_ass(word_obj["start"])
            end = _sec_to_ass(word_obj["end"])

            parts = []
            for j, w in enumerate(phrase_words):
                if j == i:
                    parts.append(f"{GOLD}{w}{WHITE}")
                else:
                    parts.append(w)

            text = " ".join(parts)
            lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")

    return lines


def _group_words(words: list[dict], max_words: int = 5) -> list[list[dict]]:
    """Split flat word list into groups of at most max_words."""
    groups = []
    for i in range(0, len(words), max_words):
        groups.append(words[i : i + max_words])
    return groups
