import json
import logging
import math
import subprocess

logger = logging.getLogger(__name__)


def get_crop_filter(video_path: str) -> str:
    """
    Return an FFmpeg crop filter string for a 9:16 centre crop.
    Uses ffprobe to determine source dimensions.

    Example for 1920x1080 source: "crop=606:1080:657:0"
    All values are forced to even integers (FFmpeg requires this).

    # TODO v2: MediaPipe face tracking to anchor crop on speaker's face.
    """
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-select_streams", "v:0",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr.strip()}")

    info = json.loads(result.stdout)
    stream = info["streams"][0]
    src_w = int(stream["width"])
    src_h = int(stream["height"])

    # Target 9:16 within the source height
    crop_w = src_h * 9 / 16
    crop_w = _even(math.floor(crop_w))

    x_offset = (src_w - crop_w) / 2
    x_offset = _even(math.floor(x_offset))

    logger.debug(f"Crop filter: crop={crop_w}:{src_h}:{x_offset}:0 (source {src_w}x{src_h})")
    return f"crop={crop_w}:{src_h}:{x_offset}:0"


def _even(n: int) -> int:
    return n if n % 2 == 0 else n - 1
