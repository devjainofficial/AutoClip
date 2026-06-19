import asyncio
import logging
import sys

import uvicorn
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse

from config import load_config, ConfigError
from src.monitor.websub import subscribe, parse_push_notification
from src.state.db import DB, DBError
from src.notifier.telegram import TelegramNotifier

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
# Pipeline worker (stub — filled in at Slice 10)
# ---------------------------------------------------------------------------

async def run_pipeline(youtube_video_id: str) -> None:
    """Full pipeline: download → transcribe → select → process → publish."""
    logger.info(f"[{youtube_video_id}] Pipeline start (stub — slices 5-10 pending)")
    # Slices 5-9 will fill this in. For now just mark it in Supabase.
    video_id = db.create_video(youtube_video_id)
    db.update_video_status(video_id, "pending")
    notify.send(f"📥 <b>New video queued</b>\nID: {youtube_video_id}\n(Pipeline stub — not yet processing)")


async def pipeline_worker() -> None:
    """Consumes video IDs from the queue and runs the pipeline sequentially."""
    logger.info("[worker] Pipeline worker started")
    while True:
        youtube_video_id = await queue.get()
        try:
            await run_pipeline(youtube_video_id)
        except Exception as e:
            logger.error(f"[{youtube_video_id}] Pipeline error: {e}", exc_info=True)
        finally:
            queue.task_done()


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    logger.info("[startup] AutoClip starting up")

    # Re-subscribe WebSub in case the lease expired while the server was down
    ok = subscribe(
        config.youtube_channel_id,
        config.websub_callback_url,
        config.websub_hub,
    )
    if not ok:
        logger.warning("[startup] WebSub subscription failed — pushes will not arrive until re-subscribed")

    # Start background pipeline worker
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
