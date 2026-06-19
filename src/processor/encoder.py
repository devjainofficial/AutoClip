import logging
import os
import subprocess

logger = logging.getLogger(__name__)

_MIN_OUTPUT_SIZE = 500 * 1024  # 500 KB

# Resolved once at module import so all callers share the same path
_FONTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "assets", "fonts")
)


class EncoderError(Exception):
    """Raised when FFmpeg fails or produces an unusable output file."""


def encode_clip(
    source_path: str,
    start: float,
    end: float,
    crop_filter: str,
    ass_path: str,
    output_path: str,
) -> str:
    """
    Single FFmpeg pass: trim → 9:16 crop → scale to 1080x1920 → burn captions → encode.
    Returns output_path on success. Raises EncoderError on failure.
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    duration = end - start
    ass_filter = f"ass={ass_path}:fontsdir={_FONTS_DIR}"
    # Escape backslashes on Windows so FFmpeg reads the path correctly
    ass_filter = ass_filter.replace("\\", "/")

    vf = f"{crop_filter},scale=1080:1920,{ass_filter}"

    cmd = [
        "ffmpeg",
        "-ss", str(start),
        "-i", source_path,
        "-t", str(duration),
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        "-y",
        output_path,
    ]

    logger.info(f"Encoding clip: {os.path.basename(output_path)} ({start:.1f}s–{end:.1f}s)")

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        stderr = result.stderr[-2000:]  # tail — FFmpeg stderr is verbose
        logger.error(f"FFmpeg failed:\n{stderr}")
        raise EncoderError(f"FFmpeg non-zero exit: {stderr}")

    if not os.path.exists(output_path):
        raise EncoderError(f"FFmpeg exited 0 but output file missing: {output_path}")

    size = os.path.getsize(output_path)
    if size < _MIN_OUTPUT_SIZE:
        raise EncoderError(
            f"Output file suspiciously small ({size} bytes): {output_path}"
        )

    logger.info(f"Clip encoded: {output_path} ({size // 1024} KB)")
    return output_path
