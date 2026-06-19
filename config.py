import logging
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


@dataclass
class Config:
    # YouTube source
    youtube_api_key: str
    youtube_channel_id: str

    # YouTube publishing (OAuth2)
    youtube_client_id: str
    youtube_client_secret: str
    youtube_refresh_token: str
    youtube_oauth_token_file: str
    shorts_channel_id: str  # empty string means same channel as source

    # LLM
    openai_api_key: str
    llm_model: str
    llm_max_clips: int
    llm_min_duration: int
    llm_max_duration: int

    # Whisper
    whisper_model: str
    whisper_device: str

    # Supabase
    supabase_url: str
    supabase_service_key: str

    # Pipeline
    publish_mode: str   # 'auto' | 'draft'
    caption_style: str  # 'minimal' | 'bold' | 'highlight'
    working_dir: str
    log_level: str

    # Telegram
    telegram_bot_token: str
    telegram_chat_id: str

    # WebSub
    websub_hub: str
    websub_callback_url: str
    vm_webhook_secret: str
    vm_webhook_url: str


def _require(name: str) -> str:
    """Return the env var value or raise a descriptive ConfigError."""
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"Required environment variable '{name}' is missing or empty.")
    return value


def _optional(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"Environment variable '{name}' must be an integer, got: {raw!r}")


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


def load_config() -> Config:
    """
    Load and validate all environment variables into a Config dataclass.
    Raises ConfigError immediately on any missing or invalid value.
    """
    errors: list[str] = []

    def safe_require(name: str) -> str:
        try:
            return _require(name)
        except ConfigError as e:
            errors.append(str(e))
            return ""

    youtube_api_key = safe_require("YOUTUBE_API_KEY")
    youtube_channel_id = safe_require("YOUTUBE_CHANNEL_ID")
    youtube_client_id = safe_require("YOUTUBE_CLIENT_ID")
    youtube_client_secret = safe_require("YOUTUBE_CLIENT_SECRET")
    youtube_refresh_token = safe_require("YOUTUBE_REFRESH_TOKEN")
    youtube_oauth_token_file = _optional("YOUTUBE_OAUTH_TOKEN_FILE", "tokens/yt.json")
    shorts_channel_id = _optional("SHORTS_CHANNEL_ID", "")

    openai_api_key = safe_require("OPENAI_API_KEY")
    llm_model = _optional("LLM_MODEL", "gpt-4o-mini")
    llm_max_clips = _int("LLM_MAX_CLIPS", 5)
    llm_min_duration = _int("LLM_MIN_DURATION", 30)
    llm_max_duration = _int("LLM_MAX_DURATION", 90)

    whisper_model = _optional("WHISPER_MODEL", "medium")
    whisper_device = _optional("WHISPER_DEVICE", "cpu")

    supabase_url = safe_require("SUPABASE_URL")
    supabase_service_key = safe_require("SUPABASE_SERVICE_KEY")

    publish_mode = _optional("PUBLISH_MODE", "auto")
    caption_style = _optional("CAPTION_STYLE", "highlight")
    working_dir = _optional("WORKING_DIR", "/home/ubuntu/autoclip/tmp")
    log_level = _optional("LOG_LEVEL", "INFO")

    telegram_bot_token = _optional("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id = _optional("TELEGRAM_CHAT_ID", "")

    websub_hub = _optional("WEBSUB_HUB", "https://pubsubhubbub.appspot.com")
    websub_callback_url = safe_require("WEBSUB_CALLBACK_URL")
    vm_webhook_secret = safe_require("VM_WEBHOOK_SECRET")
    vm_webhook_url = safe_require("VM_WEBHOOK_URL")

    if errors:
        raise ConfigError(
            "AutoClip failed to start — fix the following configuration errors:\n"
            + "\n".join(f"  • {e}" for e in errors)
        )

    # Semantic validation
    if publish_mode not in ("auto", "draft"):
        raise ConfigError(
            f"PUBLISH_MODE must be 'auto' or 'draft', got: {publish_mode!r}"
        )

    if caption_style not in ("minimal", "bold", "highlight"):
        raise ConfigError(
            f"CAPTION_STYLE must be 'minimal', 'bold', or 'highlight', got: {caption_style!r}"
        )

    if llm_min_duration >= llm_max_duration:
        raise ConfigError(
            f"LLM_MIN_DURATION ({llm_min_duration}) must be less than "
            f"LLM_MAX_DURATION ({llm_max_duration})."
        )

    if llm_max_duration > 90:
        raise ConfigError(
            f"LLM_MAX_DURATION must be <= 90 seconds (YouTube Shorts limit), "
            f"got: {llm_max_duration}"
        )

    # Ensure working_dir exists and is writable
    os.makedirs(working_dir, exist_ok=True)
    for subdir in ("source", "captions", "clips", "output"):
        os.makedirs(os.path.join(working_dir, subdir), exist_ok=True)

    if not os.access(working_dir, os.W_OK):
        raise ConfigError(f"WORKING_DIR is not writable: {working_dir}")

    # Ensure token directory exists
    token_dir = os.path.dirname(youtube_oauth_token_file)
    if token_dir:
        os.makedirs(token_dir, exist_ok=True)

    return Config(
        youtube_api_key=youtube_api_key,
        youtube_channel_id=youtube_channel_id,
        youtube_client_id=youtube_client_id,
        youtube_client_secret=youtube_client_secret,
        youtube_refresh_token=youtube_refresh_token,
        youtube_oauth_token_file=youtube_oauth_token_file,
        shorts_channel_id=shorts_channel_id,
        openai_api_key=openai_api_key,
        llm_model=llm_model,
        llm_max_clips=llm_max_clips,
        llm_min_duration=llm_min_duration,
        llm_max_duration=llm_max_duration,
        whisper_model=whisper_model,
        whisper_device=whisper_device,
        supabase_url=supabase_url,
        supabase_service_key=supabase_service_key,
        publish_mode=publish_mode,
        caption_style=caption_style,
        working_dir=working_dir,
        log_level=log_level,
        telegram_bot_token=telegram_bot_token,
        telegram_chat_id=telegram_chat_id,
        websub_hub=websub_hub,
        websub_callback_url=websub_callback_url,
        vm_webhook_secret=vm_webhook_secret,
        vm_webhook_url=vm_webhook_url,
    )
