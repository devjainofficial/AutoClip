import logging
from typing import Optional

from supabase import Client, create_client

from config import Config

logger = logging.getLogger(__name__)


class DBError(Exception):
    """Raised when a Supabase operation fails."""


class DB:
    """Thin wrapper around Supabase for all pipeline state reads and writes."""

    def __init__(self, config: Config) -> None:
        self._client: Client = create_client(
            config.supabase_url,
            config.supabase_service_key,
        )

    # ------------------------------------------------------------------
    # Videos
    # ------------------------------------------------------------------

    def video_exists(self, youtube_video_id: str) -> bool:
        """
        Return True if this video ID has already been seen by the pipeline.
        This is always the first call in the pipeline — deduplication guard.
        """
        try:
            result = (
                self._client.table("videos")
                .select("id")
                .eq("youtube_video_id", youtube_video_id)
                .limit(1)
                .execute()
            )
            return len(result.data) > 0
        except Exception as e:
            raise DBError(
                f"[{youtube_video_id}] video_exists check failed: {e}"
            ) from e

    def create_video(
        self,
        youtube_video_id: str,
        title: Optional[str] = None,
        channel_id: Optional[str] = None,
        duration_seconds: Optional[int] = None,
    ) -> str:
        """
        Insert a new row into the videos table with status 'pending'.
        Returns the UUID of the created row.
        """
        try:
            payload: dict = {
                "youtube_video_id": youtube_video_id,
                "status": "pending",
            }
            if title is not None:
                payload["title"] = title
            if channel_id is not None:
                payload["channel_id"] = channel_id
            if duration_seconds is not None:
                payload["duration_seconds"] = duration_seconds

            result = self._client.table("videos").insert(payload).execute()
            video_id: str = result.data[0]["id"]
            logger.info(f"[{youtube_video_id}] Created video row: {video_id}")
            return video_id
        except Exception as e:
            raise DBError(
                f"[{youtube_video_id}] create_video failed: {e}"
            ) from e

    def update_video_status(
        self,
        video_id: str,
        status: str,
        error: Optional[str] = None,
    ) -> None:
        """
        Update the status column for a video row.
        Optionally record an error message when status='error'.
        """
        try:
            payload: dict = {"status": status}
            if error is not None:
                payload["error_message"] = error[:2000]  # guard against oversized errors
            self._client.table("videos").update(payload).eq("id", video_id).execute()
            logger.info(f"[{video_id}] Video status → {status}")
        except Exception as e:
            raise DBError(
                f"[{video_id}] update_video_status({status!r}) failed: {e}"
            ) from e

    # ------------------------------------------------------------------
    # Clips
    # ------------------------------------------------------------------

    def create_clip(
        self,
        video_id: str,
        clip_index: int,
        title: str,
        start: float,
        end: float,
    ) -> str:
        """
        Insert a new clip row linked to a video.
        Returns the UUID of the created clip row.
        """
        try:
            duration = round(end - start, 3)
            result = (
                self._client.table("clips")
                .insert(
                    {
                        "video_id": video_id,
                        "clip_index": clip_index,
                        "title": title,
                        "start_seconds": start,
                        "end_seconds": end,
                        "duration_seconds": duration,
                        "status": "pending",
                    }
                )
                .execute()
            )
            clip_id: str = result.data[0]["id"]
            logger.info(
                f"[{video_id}] Created clip row {clip_index}: {clip_id} "
                f"({start:.1f}s–{end:.1f}s)"
            )
            return clip_id
        except Exception as e:
            raise DBError(
                f"[{video_id}] create_clip(index={clip_index}) failed: {e}"
            ) from e

    def update_clip_status(self, clip_id: str, status: str) -> None:
        """Update the status column for a clip row."""
        try:
            self._client.table("clips").update({"status": status}).eq("id", clip_id).execute()
            logger.info(f"[clip:{clip_id}] Clip status → {status}")
        except Exception as e:
            raise DBError(
                f"[clip:{clip_id}] update_clip_status({status!r}) failed: {e}"
            ) from e

    # ------------------------------------------------------------------
    # Publish log
    # ------------------------------------------------------------------

    def log_publish(
        self,
        clip_id: str,
        youtube_short_id: Optional[str],
        status: str,
        response: Optional[dict] = None,
        error: Optional[str] = None,
    ) -> None:
        """
        Write a row to publish_log recording the outcome of a YouTube upload attempt.
        status should be 'success' or 'failed'.
        """
        try:
            payload: dict = {
                "clip_id": clip_id,
                "platform": "youtube",
                "status": status,
            }
            if youtube_short_id:
                payload["youtube_short_id"] = youtube_short_id
            if response is not None:
                payload["response_payload"] = response
            if error is not None:
                payload["error_message"] = error[:2000]
            if status == "success":
                # Let Supabase default published_at to now() via trigger or we set it explicitly
                from datetime import datetime, timezone
                payload["published_at"] = datetime.now(timezone.utc).isoformat()

            self._client.table("publish_log").insert(payload).execute()
            logger.info(
                f"[clip:{clip_id}] Publish log → {status}"
                + (f" short={youtube_short_id}" if youtube_short_id else "")
            )
        except Exception as e:
            raise DBError(
                f"[clip:{clip_id}] log_publish failed: {e}"
            ) from e

    # ------------------------------------------------------------------
    # Draft clips (used by publish.py CLI)
    # ------------------------------------------------------------------

    def get_draft_clips(self, youtube_video_id: str) -> list[dict]:
        """
        Return all clips with status='draft' for the given YouTube video ID.
        Used by the manual publish CLI (publish.py).
        Returns a list of dicts with keys: id, clip_index, title, duration_seconds.
        """
        try:
            # Resolve youtube_video_id → internal video UUID first
            video_result = (
                self._client.table("videos")
                .select("id")
                .eq("youtube_video_id", youtube_video_id)
                .limit(1)
                .execute()
            )
            if not video_result.data:
                logger.warning(
                    f"[{youtube_video_id}] get_draft_clips: video not found in DB"
                )
                return []

            video_uuid = video_result.data[0]["id"]

            clips_result = (
                self._client.table("clips")
                .select("id, clip_index, title, duration_seconds, start_seconds, end_seconds")
                .eq("video_id", video_uuid)
                .eq("status", "draft")
                .order("clip_index")
                .execute()
            )
            return clips_result.data or []
        except Exception as e:
            raise DBError(
                f"[{youtube_video_id}] get_draft_clips failed: {e}"
            ) from e
