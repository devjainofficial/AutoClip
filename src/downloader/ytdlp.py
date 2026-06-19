import logging
import os
import subprocess

logger = logging.getLogger(__name__)

_MIN_FILE_SIZE = 1 * 1024 * 1024  # 1 MB


class DownloadError(Exception):
    """Raised when yt-dlp fails or produces an unusable file."""


def download(video_id: str, output_dir: str) -> str:
    """
    Download a YouTube video to output_dir using yt-dlp.
    Returns the absolute path of the downloaded MP4.
    Raises DownloadError on any failure — does not retry.
    """
    os.makedirs(output_dir, exist_ok=True)
    output_template = os.path.join(output_dir, f"{video_id}.%(ext)s")
    url = f"https://www.youtube.com/watch?v={video_id}"

    cmd = [
        "yt-dlp",
        "--format", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]",
        "--merge-output-format", "mp4",
        "--no-playlist",
        "--output", output_template,
        "--no-warnings",
        url,
    ]

    logger.info(f"[{video_id}] Starting download")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        logger.error(f"[{video_id}] yt-dlp failed:\n{stderr}")
        raise DownloadError(stderr)

    output_path = os.path.join(output_dir, f"{video_id}.mp4")

    if not os.path.exists(output_path):
        # yt-dlp may have written a different extension — scan for the file
        for fname in os.listdir(output_dir):
            if fname.startswith(video_id):
                output_path = os.path.join(output_dir, fname)
                break
        else:
            raise DownloadError(f"[{video_id}] Output file not found after download")

    size = os.path.getsize(output_path)
    if size < _MIN_FILE_SIZE:
        raise DownloadError(
            f"[{video_id}] Downloaded file is too small ({size} bytes) — likely corrupt"
        )

    logger.info(f"[{video_id}] Download complete: {output_path} ({size // (1024*1024)} MB)")
    return output_path
