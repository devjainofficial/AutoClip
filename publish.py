"""
Manual publish CLI for PUBLISH_MODE=draft.

Usage:
    python publish.py --video-id <youtube_video_id>

Lists all draft clips for the given video and prompts for confirmation
before uploading each one to YouTube Shorts.
"""
import argparse
import os
import sys

from config import load_config, ConfigError
from src.state.db import DB
from src.publisher.youtube import upload_short, PublishError, QuotaExceededError


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Publish draft clips for a processed video."
    )
    parser.add_argument("--video-id", required=True, help="YouTube video ID")
    args = parser.parse_args()

    try:
        config = load_config()
    except ConfigError as e:
        print(f"Config error: {e}")
        sys.exit(1)

    db = DB(config)
    clips = db.get_draft_clips(args.video_id)

    if not clips:
        print(f"No draft clips found for video ID: {args.video_id}")
        sys.exit(0)

    print(f"\nDraft clips for {args.video_id}:")
    for clip in clips:
        output_path = os.path.join(
            config.working_dir, "output", args.video_id, f"clip_{clip['clip_index']}.mp4"
        )
        exists = "✓" if os.path.exists(output_path) else "✗ FILE MISSING"
        print(f"  [{clip['clip_index']}] {clip['title']} ({clip['duration_seconds']:.0f}s) {exists}")
        print(f"       {output_path}")

    confirm = input("\nPublish all? (y/n): ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        sys.exit(0)

    published = 0
    for clip in clips:
        output_path = os.path.join(
            config.working_dir, "output", args.video_id, f"clip_{clip['clip_index']}.mp4"
        )
        if not os.path.exists(output_path):
            print(f"  [SKIP] Clip {clip['clip_index']} — file not found: {output_path}")
            continue

        description = (
            f"Original video: https://youtube.com/watch?v={args.video_id}\n\n"
            f"#Shorts #Highlights"
        )

        try:
            short_id = upload_short(output_path, clip["title"], description, config)
            db.log_publish(clip["id"], short_id, "success")
            db.update_clip_status(clip["id"], "published")
            published += 1
            print(f"  [{clip['clip_index']}] Published → https://youtube.com/shorts/{short_id}")
        except QuotaExceededError:
            print("  [QUOTA] YouTube daily quota exceeded. Stopping.")
            break
        except PublishError as e:
            db.log_publish(clip["id"], None, "failed", error=str(e))
            print(f"  [ERROR] Clip {clip['clip_index']} failed: {e}")

    print(f"\nDone. {published}/{len(clips)} clips published.")


if __name__ == "__main__":
    main()
