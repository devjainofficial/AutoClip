"""
Manual end-to-end test: runs the full pipeline on a given YouTube URL.

Usage:
    python scripts/test_clip.py --url https://www.youtube.com/watch?v=VIDEO_ID
    python scripts/test_clip.py --video-id VIDEO_ID
"""
import argparse
import asyncio
import logging
import re
import sys
import os

# Ensure project root is on path when run from scripts/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import load_config, ConfigError
from src.downloader.ytdlp import download
from src.transcriber.whisper_engine import transcribe
from src.selector.llm_selector import select_clips
from src.processor.crop import get_crop_filter
from src.processor.captions import build_ass
from src.processor.encoder import encode_clip

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def extract_video_id(url_or_id: str) -> str:
    match = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", url_or_id)
    return match.group(1) if match else url_or_id


async def main() -> None:
    parser = argparse.ArgumentParser(description="Test the AutoClip pipeline on a single video.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", help="Full YouTube URL")
    group.add_argument("--video-id", help="YouTube video ID")
    args = parser.parse_args()

    video_id = extract_video_id(args.url or args.video_id)
    logger.info(f"[{video_id}] Starting test pipeline")

    try:
        config = load_config()
    except ConfigError as e:
        logger.error(f"Config error: {e}")
        sys.exit(1)

    # Download
    source_dir = os.path.join(config.working_dir, "source")
    logger.info(f"[{video_id}] Downloading...")
    source_path = download(video_id, source_dir)
    logger.info(f"[{video_id}] Downloaded: {source_path}")

    # Transcribe
    captions_dir = os.path.join(config.working_dir, "captions")
    logger.info(f"[{video_id}] Transcribing (this takes a while)...")
    transcript = transcribe(source_path, captions_dir, config.whisper_model)
    logger.info(f"[{video_id}] Transcribed: {len(transcript['segments'])} segments, {transcript['duration']:.1f}s")

    # Select clips
    logger.info(f"[{video_id}] Selecting clips via LLM...")
    clips = select_clips(transcript, config)
    logger.info(f"[{video_id}] Selected {len(clips)} clips:")
    for i, clip in enumerate(clips):
        logger.info(f"  [{i+1}] {clip['title']} ({clip['start']:.1f}s–{clip['end']:.1f}s)")

    # Process clips
    crop_filter = get_crop_filter(source_path)
    output_base = os.path.join(config.working_dir, "output", video_id)
    os.makedirs(output_base, exist_ok=True)

    for i, clip in enumerate(clips):
        ass_path = os.path.join(captions_dir, f"{video_id}_clip{i+1}.ass")
        output_path = os.path.join(output_base, f"clip_{i+1}.mp4")

        build_ass(transcript["srt_path"], config.caption_style, ass_path)
        encode_clip(source_path, clip["start"], clip["end"], crop_filter, ass_path, output_path)
        logger.info(f"[{video_id}] Clip {i+1} → {output_path}")

    logger.info(f"\n[{video_id}] Test complete. Output files in: {output_base}")
    logger.info("To publish: python publish.py --video-id " + video_id)


if __name__ == "__main__":
    asyncio.run(main())
