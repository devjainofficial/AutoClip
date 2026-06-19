import json
import logging
import os

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

from config import Config

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


class PublishError(Exception):
    """Raised on a non-quota YouTube upload failure."""


class QuotaExceededError(PublishError):
    """Raised when YouTube returns quotaExceeded (HTTP 403)."""


def upload_short(
    clip_path: str,
    title: str,
    description: str,
    config: Config,
) -> str:
    """
    Upload clip_path as a YouTube Short via the Data API v3.
    Returns the uploaded video's YouTube ID on success.
    Raises QuotaExceededError or PublishError on failure.
    """
    creds = _load_credentials(config)
    youtube = build("youtube", "v3", credentials=creds)

    body = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "tags": ["shorts", "highlights", "clips"],
            "categoryId": "22",  # People & Blogs
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(clip_path, mimetype="video/mp4", resumable=True)

    logger.info(f"Uploading Short: {title[:60]!r} from {clip_path}")
    try:
        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )
        response = None
        while response is None:
            _, response = request.next_chunk()

        short_id: str = response["id"]
        logger.info(f"Short published: https://youtube.com/shorts/{short_id}")
        return short_id

    except HttpError as e:
        if e.resp.status == 403:
            try:
                reason = json.loads(e.content)["error"]["errors"][0]["reason"]
            except Exception:
                reason = ""
            if reason == "quotaExceeded":
                raise QuotaExceededError("YouTube API daily quota exceeded") from e
        raise PublishError(f"YouTube upload failed (HTTP {e.resp.status}): {e.content}") from e


# ---------------------------------------------------------------------------
# Credential management
# ---------------------------------------------------------------------------

def _load_credentials(config: Config) -> Credentials:
    """
    Load OAuth2 credentials from the token cache file.
    Refreshes the token if expired and writes it back.
    """
    token_file = config.youtube_oauth_token_file

    if os.path.exists(token_file):
        with open(token_file, "r") as f:
            token_data = json.load(f)
        creds = Credentials(
            token=token_data.get("token"),
            refresh_token=token_data.get("refresh_token") or config.youtube_refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=config.youtube_client_id,
            client_secret=config.youtube_client_secret,
            scopes=_SCOPES,
        )
    else:
        creds = Credentials(
            token=None,
            refresh_token=config.youtube_refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=config.youtube_client_id,
            client_secret=config.youtube_client_secret,
            scopes=_SCOPES,
        )

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            logger.info("Refreshing YouTube OAuth token")
            creds.refresh(Request())
            _save_credentials(creds, token_file)
        else:
            raise PublishError(
                "YouTube credentials are invalid and cannot be refreshed. "
                "Re-run the OAuth flow to get a new refresh token."
            )

    return creds


def _save_credentials(creds: Credentials, token_file: str) -> None:
    os.makedirs(os.path.dirname(token_file) or ".", exist_ok=True)
    with open(token_file, "w") as f:
        json.dump(
            {
                "token": creds.token,
                "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "scopes": list(creds.scopes or []),
            },
            f,
            indent=2,
        )
