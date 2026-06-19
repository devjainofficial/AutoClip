import asyncio
import logging
import os
import shutil
import sys

import uvicorn
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse

from config import load_config, ConfigError
from src.monitor.websub import subscribe, parse_push_notification
from src.state.db import DB, DBError
from src.notifier.telegram import TelegramNotifier
from src.downloader.ytdlp import download, DownloadError
from src.transcriber.whisper_engine import transcribe, TranscribeError
from src.selector.llm_selector import select_clips, NoClipsError
from src.processor.crop import get_crop_filter
from src.processor.captions import build_ass
from src.processor.encoder import encode_clip, EncoderError
from src.publisher.youtube import upload_short, QuotaExceededError, PublishError

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

try:
    config = load_config()
except ConfigError as e:
    logger.critical(str(e))
    sys.exit(1)

logging.getLogger().setLevel(config.log_level.upper())

db = DB(config)
notify = TelegramNotifier(config.telegram_bot_token, config.telegram_chat_id)

# ---------------------------------------------------------------------------
# FastAPI + job queue
# ---------------------------------------------------------------------------

app = FastAPI(title="AutoClip", docs_url=None, redoc_url=None)
queue: asyncio.Queue = asyncio.Queue()


@app.get("/websub")
async def websub_verify(
    hub_mode: str = "",
    hub_topic: str = "",
    hub_challenge: str = "",
    hub_lease_seconds: int = 0,
):
    """YouTube WebSub hub calls this GET to confirm the subscription is live."""
    if hub_mode == "subscribe" and hub_challenge:
        logger.info(f"[websub] Subscription verified for topic: {hub_topic}")
        return PlainTextResponse(hub_challenge)
    raise HTTPException(status_code=400, detail="Invalid WebSub verification request")


@app.post("/trigger")
async def trigger(request: Request):
    """
    Cloudflare Worker forwards YouTube push notifications here.
    Validates the shared secret, parses the Atom XML, and enqueues the video ID.
    """
    secret = request.headers.get("X-Webhook-Secret", "")
    if secret != config.vm_webhook_secret:
        logger.warning("[trigger] Rejected request — invalid X-Webhook-Secret")
        raise HTTPException(status_code=401, detail="Unauthorised")

    body = await request.body()
    video_id = parse_push_notification(body.decode())

    if not video_id:
        return {"status": "ignored"}

    if db.video_exists(video_id):
        logger.info(f"[{video_id}] Already processed — skipping")
        return {"status": "duplicate", "video_id": video_id}

    await queue.put(video_id)
    logger.info(f"[{video_id}] Enqueued")
    return {"status": "accepted", "video_id": video_id}


@app.get("/health")
async def health():
    return {"status": "ok", "queue_size": queue.qsize()}


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

async def run_pipeline(youtube_video_id: str) -> None:
    """
    Full pipeline: download → transcribe → select → process → publish.
    Each stage updates Supabase status before starting.
    """
    video_id = db.create_video(youtube_video_id)

    try:
        # ── Step 1: Download ────────────────────────────────────────────
        db.update_video_status(video_id, "downloading")
        notify.send(
            f"📥 <b>New video detected</b>\n"
            f"ID: {youtube_video_id}\n"
            f"Pipeline started."
        )

        source_dir = os.path.join(config.working_dir, "source")
        source_path = await asyncio.to_thread(download, youtube_video_id, source_dir)

        # ── Step 2: Transcribe ──────────────────────────────────────────
        db.update_video_status(video_id, "transcribing")
        captions_dir = os.path.join(config.working_dir, "captions")
        transcript = await asyncio.to_thread(
            transcribe, source_path, captions_dir, config.whisper_model
        )
        db.update_video_status(video_id, "transcribing")  # update duration now we have it
        db._client.table("videos").update(
            {"duration_seconds": int(transcript["duration"])}
        ).eq("id", video_id).execute()

        # ── Step 3: Select clips ────────────────────────────────────────
        db.update_video_status(video_id, "selecting")
        clips = await asyncio.to_thread(select_clips, transcript, config)
        notify.send(
            f"✂️ <b>{len(clips)} clips selected</b>\n"
            f"ID: {youtube_video_id}"
        )

        # ── Step 4: Process each clip ───────────────────────────────────
        db.update_video_status(video_id, "processing")
        output_base = os.path.join(config.working_dir, "output", youtube_video_id)
        os.makedirs(output_base, exist_ok=True)

        crop_filter = await asyncio.to_thread(get_crop_filter, source_path)
        encoded_clips = []

        for i, clip in enumerate(clips):
            clip_id = db.create_clip(
                video_id, i + 1, clip["title"], clip["start"], clip["end"]
            )
            ass_path = os.path.join(
                config.working_dir, "captions", f"{youtube_video_id}_clip{i+1}.ass"
            )
            output_path = os.path.join(output_base, f"clip_{i+1}.mp4")

            try:
                await asyncio.to_thread(
                    build_ass, transcript["srt_path"], config.caption_style, ass_path
                )
                await asyncio.to_thread(
                    encode_clip, source_path, clip["start"], clip["end"],
                    crop_filter, ass_path, output_path
                )
                db.update_clip_status(clip_id, "processed")
                encoded_clips.append((clip_id, clip, output_path))
            except EncoderError as e:
                logger.error(f"[{youtube_video_id}] Clip {i+1} encode failed: {e}")
                db.update_clip_status(clip_id, "error")

        # ── Step 5: Publish ─────────────────────────────────────────────
        published_count = 0
        draft_count = 0

        if config.publish_mode == "auto":
            db.update_video_status(video_id, "publishing")
            for idx, (clip_id, clip, output_path) in enumerate(encoded_clips):
                description = (
                    f"Original video: https://youtube.com/watch?v={youtube_video_id}\n\n"
                    f"#Shorts #Highlights"
                )
                try:
                    short_id = await asyncio.to_thread(
                        upload_short, output_path, clip["title"], description, config
                    )
                    db.log_publish(clip_id, short_id, "success")
                    db.update_clip_status(clip_id, "published")
                    published_count += 1
                    notify.send(
                        f"✅ <b>Short published</b> ({published_count}/{len(encoded_clips)})\n"
                        f"{clip['title']}\n"
                        f"https://youtube.com/shorts/{short_id}"
                    )
                except QuotaExceededError:
                    # Mark this clip and all remaining as draft
                    db.update_clip_status(clip_id, "draft")
                    draft_count += 1
                    for remaining_clip_id, _, _ in encoded_clips[idx + 1:]:
                        db.update_clip_status(remaining_clip_id, "draft")
                        draft_count += 1
                    notify.send(
                        f"⚠️ <b>YouTube quota exceeded</b>\n"
                        f"{published_count} clips published.\n"
                        f"{draft_count} clips saved as drafts."
                    )
                    break
                except PublishError as e:
                    db.log_publish(clip_id, None, "failed", error=str(e))
                    db.update_clip_status(clip_id, "error")
        else:
            for clip_id, _, _ in encoded_clips:
                db.update_clip_status(clip_id, "draft")
                draft_count += 1

        db.update_video_status(video_id, "done")

        if config.publish_mode == "auto":
            notify.send(
                f"🎉 <b>Done!</b> {published_count} Shorts published from ID: {youtube_video_id}"
            )
        else:
            notify.send(
                f"📋 <b>Done (draft mode)</b>\n"
                f"{draft_count} clips ready.\n"
                f"Run: python publish.py --video-id {youtube_video_id}"
            )

    except DownloadError as e:
        db.update_video_status(video_id, "error", str(e))
        notify.send(
            f"⚠️ <b>Download failed</b>\n"
            f"ID: {youtube_video_id}\n"
            f"<code>{str(e)[:200]}</code>"
        )
        raise

    except (TranscribeError, NoClipsError, Exception) as e:
        db.update_video_status(video_id, "error", str(e))
        notify.send(
            f"❌ <b>Pipeline error</b>\n"
            f"ID: {youtube_video_id}\n"
            f"<code>{str(e)[:300]}</code>"
        )
        raise

    finally:
        _cleanup_tmp(youtube_video_id)


def _cleanup_tmp(youtube_video_id: str) -> None:
    """Delete working files after processing. Keep output/ only in draft mode."""
    base = config.working_dir
    for subdir in ("source", "captions", "clips"):
        for ext in (youtube_video_id + ".mp4", youtube_video_id + ".srt",
                    youtube_video_id + "_transcript.json"):
            path = os.path.join(base, subdir, ext)
            if os.path.exists(path):
                os.remove(path)
        # Remove any clip-specific ASS files
        captions_path = os.path.join(base, "captions")
        if os.path.exists(captions_path):
            for fname in os.listdir(captions_path):
                if fname.startswith(youtube_video_id):
                    os.remove(os.path.join(captions_path, fname))

    if config.publish_mode == "auto":
        output_dir = os.path.join(base, "output", youtube_video_id)
        if os.path.exists(output_dir):
            shutil.rmtree(output_dir)


# ---------------------------------------------------------------------------
# Pipeline worker
# ---------------------------------------------------------------------------

async def pipeline_worker() -> None:
    """Consumes video IDs from the queue and runs the pipeline sequentially."""
    logger.info("[worker] Pipeline worker started")
    while True:
        youtube_video_id = await queue.get()
        try:
            await run_pipeline(youtube_video_id)
        except Exception as e:
            logger.error(f"[{youtube_video_id}] Pipeline failed: {e}", exc_info=True)
        finally:
            queue.task_done()


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    logger.info("[startup] AutoClip starting up")
    ok = subscribe(config.youtube_channel_id, config.websub_callback_url, config.websub_hub)
    if not ok:
        logger.warning("[startup] WebSub subscription failed — pushes may not arrive")
    asyncio.create_task(pipeline_worker())
    logger.info("[startup] Ready — listening on port 8000")


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level=config.log_level.lower(),
    )
