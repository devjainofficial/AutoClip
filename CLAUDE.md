# CLAUDE.md — AutoClip: Self-Hosted YouTube-to-Shorts Automation Pipeline

> Rename `AutoClip` to whatever brand name you decide on. Find-replace across all files.

---

## What This Is

A fully self-hosted, near-zero-cost Python daemon that:

1. Detects new long-form video uploads on a YouTube channel via WebSub push
2. Downloads the video using yt-dlp
3. Transcribes it locally using Whisper
4. Sends the transcript to a cheap LLM to identify the best 3-5 highlight clips
5. Extracts those clips using FFmpeg
6. Burns animated word-highlight captions onto each clip
7. Crops to 9:16 vertical format
8. Auto-publishes directly to YouTube Shorts via the YouTube Data API
9. Logs all activity to Supabase

**Platform:** YouTube only (Shorts).
**Source:** yt-dlp (downloads from YouTube, processes, re-uploads as Shorts).
**Total infrastructure cost: $0/year** on Oracle Cloud Always Free.
**LLM API cost: ~$0.06/month** at 4-5 videos/week using GPT-4o-mini.

---

## End Product Behaviour (From Creator's Perspective)

The creator uploads a long-form video to YouTube as normal. Within 15-20 minutes:
- The pipeline detects the new upload via WebSub push notification
- yt-dlp downloads the video to the Oracle VM
- Whisper transcribes it locally
- LLM picks the best 3-5 moments with timestamps
- FFmpeg extracts, crops to 9:16, and burns captions onto each clip
- Each clip is uploaded as a YouTube Short on the same or a linked channel
- Supabase logs the result — video ID, clips generated, publish status, any errors

**Zero manual steps after initial setup.** The daemon runs 24/7 on Oracle VM as a systemd service.

If `PUBLISH_MODE=draft`, clips are saved locally and skipped for auto-publish. The creator reviews the files in `tmp/output/`, then runs:
```
python publish.py --video-id <youtube_video_id>
```

---

## ToS Acknowledgement

yt-dlp violates YouTube ToS Section 4B for downloading video content.
The practical risk for a solo creator processing their own channel is low — account warning at worst, not legal action.
This is a known and accepted tradeoff in this build.
Do not use this pipeline to download and re-upload content you do not own.

---

## Architecture

```
YouTube Channel
     |
     | (new video published)
     v
YouTube WebSub Hub
     |
     | (Atom push notification)
     v
Cloudflare Worker  <-- validates X-Hub-Signature
     |
     | (forwards to Oracle VM)
     v
FastAPI webhook server (port 8000)
     |
     v
Pipeline Orchestrator
     |
     |-- yt-dlp download --> tmp/source/<video_id>.mp4
     |-- Whisper transcription --> tmp/captions/<video_id>.json + .srt
     |-- LLM clip selection --> clip timestamps list
     |-- FFmpeg: extract + 9:16 crop + burn captions --> tmp/output/<video_id>/<clip_n>.mp4
     |-- YouTube Data API upload --> published as Shorts
     |-- Supabase log --> videos + clips + publish_log tables
     |-- Cleanup --> delete tmp files
```

**No cloud storage required.** All processing happens on the Oracle VM's local disk (200 GB free storage included in Oracle Always Free). Tmp files are deleted after successful upload.

---

## Tech Stack (Pinned)

| Layer | Tool | Notes |
|---|---|---|
| Runtime | Python 3.11+ | System install on Oracle VM |
| Video download | yt-dlp | Latest via pip, updated regularly |
| Transcription | openai-whisper | Local, `medium` model default |
| Caption timestamps | stable-ts | Word-level timestamps from Whisper |
| Video processing | FFmpeg 6.x | System install via apt |
| LLM clip selection | openai | GPT-4o-mini via API |
| Database | supabase-py | Supabase Python client |
| YouTube publish | google-api-python-client | v2, OAuth2 |
| Webhook receiver | Cloudflare Worker | Plain JavaScript, free tier |
| Job scheduling | APScheduler | Internal cron tasks |
| HTTP server | FastAPI + uvicorn | Webhook receiver on port 8000 |
| Env management | python-dotenv | |

**Do not use:**
- moviepy (slow, heavy — call FFmpeg subprocess directly)
- celery or redis (overkill for a single-creator pipeline)
- Docker (adds complexity with no benefit on a dedicated VM)
- Any paid transcription API (Whisper runs locally for free)
- boto3 or any cloud storage SDK (not needed, all storage is local)

---

## Environment Variables

```env
# ---- YouTube Source ----
YOUTUBE_API_KEY=                           # YouTube Data API v3 key (for WebSub + metadata)
YOUTUBE_CHANNEL_ID=                        # Target channel to monitor (UCxxxxxxxx)

# ---- YouTube Publishing (OAuth2) ----
YOUTUBE_CLIENT_ID=
YOUTUBE_CLIENT_SECRET=
YOUTUBE_REFRESH_TOKEN=                     # Long-lived refresh token for Shorts upload
YOUTUBE_OAUTH_TOKEN_FILE=tokens/yt.json    # Path to OAuth token cache file

# ---- YouTube Shorts Target ----
# If publishing to the SAME channel the long-form is on, leave SHORTS_CHANNEL_ID blank.
# If publishing to a SEPARATE Shorts channel, set its channel ID here.
SHORTS_CHANNEL_ID=

# ---- LLM ----
OPENAI_API_KEY=
LLM_MODEL=gpt-4o-mini
LLM_MAX_CLIPS=5                            # Max clips to extract per video
LLM_MIN_DURATION=30                        # Seconds, minimum clip length
LLM_MAX_DURATION=90                        # Seconds, maximum clip length

# ---- Whisper ----
WHISPER_MODEL=medium                       # tiny | base | small | medium | large
WHISPER_DEVICE=cpu                         # cpu | cuda

# ---- Supabase ----
SUPABASE_URL=
SUPABASE_SERVICE_KEY=                      # Service role key, not anon key

# ---- Pipeline Config ----
PUBLISH_MODE=auto                          # auto | draft
CAPTION_STYLE=highlight                    # minimal | bold | highlight
WORKING_DIR=/home/ubuntu/autoclip/tmp
LOG_LEVEL=INFO

# ---- Telegram Notifications ----
TELEGRAM_BOT_TOKEN=                        # Bot token from @BotFather
TELEGRAM_CHAT_ID=                          # Your personal chat ID or a group chat ID

# ---- WebSub ----
WEBSUB_HUB=https://pubsubhubbub.appspot.com
WEBSUB_CALLBACK_URL=https://your-worker.workers.dev/websub

# ---- Cloudflare Worker ----
VM_WEBHOOK_SECRET=                         # Shared secret between Worker and VM
VM_WEBHOOK_URL=http://<oracle-vm-public-ip>:8000/trigger
```

---

## Directory Structure

```
autoclip/
├── CLAUDE.md
├── requirements.txt
├── .env
├── .env.example
├── main.py                          # Entry point — starts FastAPI + pipeline daemon
├── config.py                        # Loads and validates all env vars into Config dataclass
├── publish.py                       # Manual publish CLI (for PUBLISH_MODE=draft)
│
├── src/
│   ├── monitor/
│   │   ├── __init__.py
│   │   └── websub.py                # WebSub subscription + renewal logic
│   │
│   ├── downloader/
│   │   ├── __init__.py
│   │   └── ytdlp.py                 # yt-dlp wrapper, downloads to tmp/source/
│   │
│   ├── transcriber/
│   │   ├── __init__.py
│   │   └── whisper_engine.py        # Whisper + stable-ts, outputs SRT + word timestamps
│   │
│   ├── selector/
│   │   ├── __init__.py
│   │   └── llm_selector.py          # GPT-4o-mini clip selection, rule-based fallback
│   │
│   ├── processor/
│   │   ├── __init__.py
│   │   ├── crop.py                  # 9:16 crop params (centre-crop v1)
│   │   ├── captions.py              # SRT to ASS conversion, style presets
│   │   └── encoder.py               # FFmpeg pipeline orchestrator
│   │
│   ├── notifier/
│   │   ├── __init__.py
│   │   └── telegram.py              # Telegram Bot API notifications
│   │
│   ├── publisher/
│   │   ├── __init__.py
│   │   └── youtube.py               # YouTube Shorts upload via Data API v3
│   │
│   └── state/
│       ├── __init__.py
│       └── db.py                    # Supabase read/write wrapper
│
├── workers/
│   └── websub_worker.js             # Cloudflare Worker source — deploy separately
│
├── scripts/
│   ├── setup.sh                     # One-shot Oracle VM setup
│   ├── websub_renew.py              # Re-subscribe WebSub (called by GitHub Actions)
│   └── test_clip.py                 # Manual test: run full pipeline on a video URL
│
├── tokens/
│   └── yt.json                      # YouTube OAuth token cache (gitignored)
│
├── tmp/                             # Working directory, gitignored, cleaned after each job
│   ├── source/                      # Downloaded long-form videos
│   ├── captions/                    # Whisper SRT + JSON transcript files
│   ├── clips/                       # Raw extracted clip segments
│   └── output/                      # Final 9:16 encoded clips, ready to upload
│
├── .github/
│   └── workflows/
│       └── websub_renew.yml         # GitHub Actions cron for WebSub renewal
│
└── tests/
    ├── test_transcriber.py
    ├── test_selector.py
    ├── test_processor.py
    └── test_publisher.py
```

---

## Supabase Schema

Run this SQL in the Supabase SQL editor before starting the build.

```sql
-- Tracks every long-form video the pipeline has seen
create table videos (
  id uuid primary key default gen_random_uuid(),
  youtube_video_id text unique not null,
  title text,
  channel_id text,
  published_at timestamptz,
  duration_seconds integer,
  status text default 'pending',
  -- pending | downloading | transcribing | selecting | processing | publishing | done | error
  error_message text,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

-- One row per generated clip
create table clips (
  id uuid primary key default gen_random_uuid(),
  video_id uuid references videos(id) on delete cascade,
  clip_index integer,                -- 1-5, order within parent video
  title text,                        -- LLM-suggested clip title
  start_seconds numeric,
  end_seconds numeric,
  duration_seconds numeric,
  status text default 'pending',
  -- pending | processed | published | draft | error
  created_at timestamptz default now()
);

-- One row per YouTube upload attempt
create table publish_log (
  id uuid primary key default gen_random_uuid(),
  clip_id uuid references clips(id),
  platform text default 'youtube',
  youtube_short_id text,             -- The uploaded Short's video ID
  status text,                       -- success | failed
  response_payload jsonb,
  published_at timestamptz,
  error_message text
);

-- Useful view for monitoring
create view pipeline_summary as
select
  v.youtube_video_id,
  v.title,
  v.status as video_status,
  v.created_at,
  count(c.id) as clips_generated,
  count(p.id) filter (where p.status = 'success') as clips_published,
  v.error_message
from videos v
left join clips c on c.video_id = v.id
left join publish_log p on p.clip_id = c.id
group by v.id
order by v.created_at desc;
```

---

## Module Specifications

### `config.py`

Load all env vars on startup. Validate required vars are present. Raise a clear, descriptive error at boot if anything is missing — fail fast rather than crashing mid-pipeline.

```python
from dataclasses import dataclass, field

@dataclass
class Config:
    # YouTube
    youtube_api_key: str
    youtube_channel_id: str
    youtube_client_id: str
    youtube_client_secret: str
    youtube_refresh_token: str
    youtube_oauth_token_file: str
    shorts_channel_id: str           # Empty string means same channel as source

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
    publish_mode: str                # 'auto' | 'draft'
    caption_style: str               # 'minimal' | 'bold' | 'highlight'
    working_dir: str

    # WebSub
    websub_hub: str
    websub_callback_url: str
    vm_webhook_secret: str
```

Validate:
- `publish_mode` is one of `['auto', 'draft']`
- `caption_style` is one of `['minimal', 'bold', 'highlight']`
- `llm_min_duration < llm_max_duration`
- `llm_max_duration <= 90` (YouTube Shorts hard limit is 60s for old-style Shorts, but 3 minutes is now supported — cap at 90s to stay safe)
- `working_dir` exists and is writable, create it if it does not exist

---

### `src/monitor/websub.py`

Manages the YouTube WebSub subscription.

```python
def subscribe(channel_id: str, callback_url: str, hub_url: str) -> bool:
    """
    POST to hub_url to subscribe to the channel's Atom feed.
    Feed URL format: https://www.youtube.com/xml/feeds/videos.xml?channel_id={channel_id}
    Lease: 864000 seconds (10 days max allowed by YouTube's hub).
    Returns True on 202 response from hub.
    """

def parse_push_notification(xml_body: str) -> str | None:
    """
    Parses the Atom XML payload sent by the YouTube hub.
    Returns the YouTube video ID (yt:videoId element) or None if not a new video event.
    """
```

The WebSub subscription verification (GET challenge) is handled in the FastAPI route in `main.py`, not here. Keep this module focused on subscription management and XML parsing only.

---

### `src/downloader/ytdlp.py`

```python
def download(video_id: str, output_dir: str) -> str:
    """
    Downloads a YouTube video to output_dir using yt-dlp subprocess.
    Returns the local file path of the downloaded MP4.
    Raises DownloadError on failure.
    """
```

yt-dlp subprocess arguments:
```
yt-dlp
  --format "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]"
  --merge-output-format mp4
  --no-playlist
  --output "{output_dir}/{video_id}.%(ext)s"
  --no-warnings
  https://www.youtube.com/watch?v={video_id}
```

On failure (non-zero exit code), capture stderr and raise `DownloadError(stderr)`. Do not retry automatically — the orchestrator handles retry logic.

After a successful download, verify the output file exists and is larger than 1 MB. If the file is missing or too small, treat it as a failed download.

---

### `src/transcriber/whisper_engine.py`

```python
def transcribe(video_path: str, output_dir: str, model_name: str = 'medium') -> dict:
    """
    Runs Whisper on video_path using stable-ts for word-level timestamps.
    Caches results — if transcript JSON already exists in output_dir for this
    video, loads and returns that instead of re-running Whisper.

    Returns:
    {
      'full_text': str,
      'segments': [{'start': float, 'end': float, 'text': str}],
      'word_timestamps': [{'word': str, 'start': float, 'end': float}],
      'srt_path': str,       # absolute path to the generated word-level SRT
      'duration': float      # total video duration in seconds
    }
    """
```

Implementation:

```python
import stable_whisper

model = stable_whisper.load_model(model_name)
result = model.transcribe(video_path, word_timestamps=True)
result.to_srt_vtt(srt_path, word_level=True)
```

Cache: write `<video_id>_transcript.json` and `<video_id>.srt` to `output_dir`. Check for both files before running Whisper. If they exist, deserialise and return the cached result. This prevents re-transcribing if the pipeline crashes mid-way and restarts.

If Whisper raises an out-of-memory error, automatically retry with `small` model and log a warning. Do not crash.

---

### `src/selector/llm_selector.py`

```python
def select_clips(transcript: dict, config: Config) -> list[dict]:
    """
    Sends transcript to GPT-4o-mini. Returns a list of clip dicts.
    Falls back to rule-based selection on LLM failure.

    Returns:
    [
      {
        'title': str,       # max 100 chars, YouTube-ready
        'start': float,     # seconds
        'end': float,       # seconds
        'hook': str         # one-line summary of why this clip is engaging
      },
      ...
    ]
    """
```

**System prompt:**

```
You are a short-form video editor specialising in extracting viral clips from long-form YouTube content.

Given a video transcript with timestamps, identify the best {max_clips} moments for YouTube Shorts.

Selection criteria:
- Self-contained: clip makes sense without surrounding context
- Strong opening hook in the first 3 seconds
- High energy, surprising insight, emotional peak, or highly useful standalone tip
- Between {min_duration} and {max_duration} seconds long
- Must not start or end mid-sentence

Respond ONLY with a valid JSON array. No explanation. No preamble. No markdown fences.
Format:
[{"title": "...", "start": 0.0, "end": 0.0, "hook": "..."}]
```

Pass the full transcript as a user message. Use `temperature=0.3` for consistent JSON output.

Parse the response. If JSON parsing fails, retry once with this addition to the system prompt:
`"IMPORTANT: Your previous response was not valid JSON. Return ONLY the JSON array, nothing else."`

If the second attempt also fails, fall back to rule-based selection.

**Rule-based fallback:**

Use FFmpeg silence detection to find non-silent segments:
```bash
ffmpeg -i input.mp4 -af "silencedetect=n=-35dB:d=0.5" -f null - 2>&1
```

Parse the stderr output to find speech segments. Filter to segments between `LLM_MIN_DURATION` and `LLM_MAX_DURATION` seconds. Score by length (prefer segments closest to 60 seconds). Return the top N. Assign generic titles: `"Highlight {n}"`.

After getting clips from either path, validate each clip:
- `start >= 0`
- `end <= transcript['duration']`
- `end - start >= config.llm_min_duration`
- `end - start <= config.llm_max_duration`

Drop any clip that fails validation. If zero clips remain after validation, raise `NoClipsError`.

---

### `src/processor/crop.py`

```python
def get_crop_filter(video_path: str) -> str:
    """
    Returns FFmpeg crop filter string for 9:16 centre crop.
    Uses ffprobe to get source dimensions.

    Example return: "crop=607:1080:656:0"
    For a 1920x1080 source:
      crop_width = 1080 * (9/16) = 607.5 -> floor to 606 (must be even)
      x_offset = (1920 - 606) / 2 = 657 -> floor to 656 (must be even)
    """
```

Always ensure crop_width and x_offset are even integers. FFmpeg will error on odd values.

Face tracking (MediaPipe) is NOT implemented in v1. The function signature stays as above. Add a `# TODO v2: MediaPipe face tracking` comment so it is easy to locate later.

---

### `src/processor/captions.py`

```python
def build_ass(srt_path: str, style: str, output_path: str) -> str:
    """
    Converts word-level SRT from stable-ts into ASS subtitle format.
    Applies the requested style preset.
    Returns path to the generated ASS file.
    """
```

**Three style presets:**

`minimal`:
- Font: Montserrat Bold, 18pt
- White text, no background
- Position: bottom centre (Alignment=2)
- No word highlighting
- Use standard ASS `[V4+ Styles]` section

`bold`:
- Font: Montserrat ExtraBold, 20pt
- All caps (apply `.upper()` to all dialogue text)
- White text on semi-transparent black box (`\3c&H000000&\3a&H40&`)
- Position: lower third (Alignment=2, MarginV=60)

`highlight` (default):
- Font: Montserrat ExtraBold, 22pt
- Position: centre-bottom (~60% down frame, Alignment=2, MarginV=400 for 1920px height)
- Non-active words: white (`\c&HFFFFFF&`)
- Active word: yellow (`\c&H00D7FF&` in ASS BGR hex = #FFD700 gold)
- Implementation: one ASS `Dialogue` event per word, with colour override tags
- Black outline (Outline=2, OutlineColour=&H00000000)

For the `highlight` style, parse the word-level SRT (which has one line per word with its start/end time) and generate one Dialogue line per word. Each line uses `{\c&HFFFFFF&}full sentence {\c&H00D7FF&}current_word{\c&HFFFFFF&} rest of sentence` so the active word appears highlighted while surrounding context is visible in white.

Montserrat is not available by default on Ubuntu. Download it as part of `scripts/setup.sh`:
```bash
mkdir -p /home/ubuntu/autoclip/assets/fonts
wget -q -O /tmp/Montserrat.zip "https://fonts.google.com/download?family=Montserrat"
unzip -o /tmp/Montserrat.zip -d /home/ubuntu/autoclip/assets/fonts/
```

Pass the font directory to FFmpeg using the `ass` filter's `fontsdir` option:
```
ass={ass_path}:fontsdir=/home/ubuntu/autoclip/assets/fonts
```

---

### `src/processor/encoder.py`

```python
def encode_clip(
    source_path: str,
    start: float,
    end: float,
    crop_filter: str,
    ass_path: str,
    output_path: str
) -> str:
    """
    Single FFmpeg command: trim + crop + caption burn + encode.
    Returns output_path on success. Raises EncoderError on failure.
    """
```

FFmpeg command:

```bash
ffmpeg \
  -ss {start} \
  -i {source_path} \
  -t {duration} \
  -vf "{crop_filter},scale=1080:1920,ass={ass_path}:fontsdir={fonts_dir}" \
  -c:v libx264 \
  -preset fast \
  -crf 23 \
  -c:a aac \
  -b:a 128k \
  -movflags +faststart \
  -y \
  {output_path}
```

Note: `-ss` before `-i` (input seeking) is faster than post-input seeking for long files. Use `-t` (duration) not `-to` (end time) since `-ss` changes the timestamp reference.

Output spec:
- Resolution: 1080x1920
- Video: H.264, CRF 23, fast preset
- Audio: AAC 128kbps stereo
- Container: MP4 with faststart flag

Check that `output_path` exists and is larger than 500 KB after encoding. If not, raise `EncoderError`.

---

### `src/notifier/telegram.py`

Lightweight Telegram notification wrapper using plain `requests`. No external bot library needed.

```python
import requests
import logging

logger = logging.getLogger(__name__)

class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.enabled = bool(token and chat_id)

    def send(self, message: str) -> None:
        """
        Sends a plain text message. Silently logs and returns on failure.
        Never raises — notification failure must never break the pipeline.
        """
        if not self.enabled:
            return
        try:
            requests.post(
                f"{self.base_url}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=5,
            )
        except Exception as e:
            logger.warning(f"Telegram notification failed: {e}")
```

**Notification messages (call these from the orchestrator at each stage):**

```python
# New video detected
notify.send(
    f"📥 <b>New video detected</b>\n"
    f"{title}\n"
    f"Duration: {duration_str}\n"
    f"Pipeline started."
)

# Clip selection done
notify.send(
    f"✂️ <b>{len(clips)} clips selected</b> from\n{title}"
)

# Each clip published
notify.send(
    f"✅ <b>Short published</b> ({clip_index}/{total_clips})\n"
    f"{clip_title}\n"
    f"https://youtube.com/shorts/{short_id}"
)

# All done
notify.send(
    f"🎉 <b>Done!</b> {published_count} Shorts published from\n{title}"
)

# Pipeline error
notify.send(
    f"❌ <b>Pipeline error</b>\n"
    f"Video: {youtube_video_id}\n"
    f"Stage: {last_known_status}\n"
    f"<code>{error_message[:300]}</code>"
)

# YouTube quota exceeded
notify.send(
    f"⚠️ <b>YouTube quota exceeded</b>\n"
    f"{published_count} clips published.\n"
    f"{draft_count} clips saved as drafts."
)

# yt-dlp download failed
notify.send(
    f"⚠️ <b>Download failed</b>\n"
    f"Video ID: {youtube_video_id}\n"
    f"<code>{error[:200]}</code>"
)
```

Instantiate `TelegramNotifier` once in `main.py` at startup and pass it into `run_pipeline()`. If `TELEGRAM_BOT_TOKEN` or `TELEGRAM_CHAT_ID` are empty, the notifier disables itself silently — the pipeline runs without notifications. Do not make Telegram required.

**One-time setup to get your chat ID:**
1. Create a bot via @BotFather, copy the token
2. Send any message to your bot
3. Call `https://api.telegram.org/bot{token}/getUpdates`
4. Copy the `chat.id` value from the response

---

### `src/publisher/youtube.py`

```python
def upload_short(
    clip_path: str,
    title: str,
    description: str,
    config: Config
) -> str:
    """
    Uploads a clip as a YouTube Short via the Data API v3.
    Returns the uploaded video's YouTube ID on success.
    Raises PublishError on failure.
    """
```

Use `googleapiclient.discovery.build('youtube', 'v3', credentials=creds)`.

Load credentials from `config.youtube_oauth_token_file` using `google.oauth2.credentials.Credentials`. If the token is expired, refresh it using `google.auth.transport.requests.Request()` and write the updated token back to the file.

Upload parameters:
```python
body = {
    'snippet': {
        'title': title[:100],          # YouTube title limit
        'description': description,
        'tags': ['shorts', 'highlights', 'clips'],
        'categoryId': '22',            # People & Blogs — change if gaming (categoryId: '20')
    },
    'status': {
        'privacyStatus': 'public',
        'selfDeclaredMadeForKids': False,
    }
}
```

Use resumable upload (`MediaFileUpload` with `resumable=True`). This handles large files and network interruptions.

YouTube auto-detects 1080x1920 MP4 under 3 minutes as a Short. No special API flag needed.

Auto-generate `description`:
```
Original video: https://youtube.com/watch?v={source_video_id}

#Shorts #Highlights
```

Quota cost: 1,600 units per upload. At 5 clips per video, 5 videos/week = 25 uploads/week (~4/day). Daily quota is 10,000 units. Well within limits.

On `HttpError` 403 with `reason: quotaExceeded`, do not retry. Raise `QuotaExceededError` so the orchestrator can stop publishing and leave remaining clips in `draft` status. Log clearly.

---

### `src/state/db.py`

```python
class DB:
    def __init__(self, config: Config)

    # Videos
    def video_exists(self, youtube_video_id: str) -> bool
    def create_video(self, youtube_video_id: str, title: str = None) -> str  # returns uuid
    def update_video_status(self, video_id: str, status: str, error: str = None) -> None

    # Clips
    def create_clip(
        self, video_id: str, clip_index: int,
        title: str, start: float, end: float
    ) -> str  # returns uuid
    def update_clip_status(self, clip_id: str, status: str) -> None

    # Publish log
    def log_publish(
        self, clip_id: str, youtube_short_id: str,
        status: str, response: dict = None, error: str = None
    ) -> None

    # Pending clips (for PUBLISH_MODE=draft manual publish)
    def get_draft_clips(self, video_id: str) -> list[dict]
```

`video_exists()` is the deduplication guard. Always the first call in the pipeline. If it returns `True`, skip entirely.

All methods should wrap Supabase calls in try/except and raise a descriptive `DBError` on failure rather than letting raw exceptions surface to the orchestrator.

---

## `main.py` — Orchestrator

Two things run simultaneously:

**1. FastAPI server** (handles incoming WebSub pushes)

```python
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
import asyncio

app = FastAPI()
queue: asyncio.Queue = asyncio.Queue()

@app.get("/websub")
async def websub_verify(
    hub_mode: str,
    hub_topic: str,
    hub_challenge: str,
    hub_lease_seconds: int = 0
):
    # YouTube hub sends a GET to verify the subscription
    # Return hub_challenge as plain text to confirm
    return PlainTextResponse(hub_challenge)

@app.post("/trigger")
async def trigger(request: Request):
    # Validate X-Webhook-Secret matches VM_WEBHOOK_SECRET
    secret = request.headers.get("X-Webhook-Secret")
    if secret != config.vm_webhook_secret:
        return {"error": "unauthorised"}, 401

    body = await request.body()
    video_id = parse_push_notification(body.decode())

    if video_id:
        await queue.put(video_id)
        return {"status": "accepted", "video_id": video_id}

    return {"status": "ignored"}
```

**2. Pipeline worker** (consumes the queue)

```python
async def pipeline_worker():
    while True:
        video_id = await queue.get()
        try:
            await run_pipeline(video_id)
        except Exception as e:
            logger.error(f"Pipeline failed for {video_id}: {e}")
        finally:
            queue.task_done()

async def run_pipeline(youtube_video_id: str, notify: TelegramNotifier):
    db = DB(config)

    if db.video_exists(youtube_video_id):
        logger.info(f"[{youtube_video_id}] Already processed — skipping")
        return

    video_id = db.create_video(youtube_video_id)

    try:
        # Step 1: Download
        db.update_video_status(video_id, 'downloading')
        notify.send(f"📥 <b>New video detected</b>\nID: {youtube_video_id}\nPipeline started.")
        source_path = download(youtube_video_id, config.working_dir + '/source')

        # Step 2: Transcribe
        db.update_video_status(video_id, 'transcribing')
        transcript = transcribe(source_path, config.working_dir + '/captions')

        # Step 3: Select clips
        db.update_video_status(video_id, 'selecting')
        clips = select_clips(transcript, config)
        notify.send(f"✂️ <b>{len(clips)} clips selected</b>\nID: {youtube_video_id}")

        # Step 4: Process each clip
        db.update_video_status(video_id, 'processing')
        encoded_clips = []
        for i, clip in enumerate(clips):
            clip_id = db.create_clip(video_id, i + 1, clip['title'], clip['start'], clip['end'])
            crop_filter = get_crop_filter(source_path)
            ass_path = build_ass(transcript['srt_path'], config.caption_style, ...)
            output_path = f"{config.working_dir}/output/{youtube_video_id}/clip_{i+1}.mp4"
            encode_clip(source_path, clip['start'], clip['end'], crop_filter, ass_path, output_path)
            db.update_clip_status(clip_id, 'processed')
            encoded_clips.append((clip_id, clip, output_path))

        # Step 5: Publish
        published_count = 0
        draft_count = 0

        if config.publish_mode == 'auto':
            db.update_video_status(video_id, 'publishing')
            for clip_id, clip, output_path in encoded_clips:
                try:
                    short_id = upload_short(output_path, clip['title'], ..., config)
                    db.log_publish(clip_id, short_id, 'success')
                    db.update_clip_status(clip_id, 'published')
                    published_count += 1
                    notify.send(
                        f"✅ <b>Short published</b> ({published_count}/{len(encoded_clips)})\n"
                        f"{clip['title']}\n"
                        f"https://youtube.com/shorts/{short_id}"
                    )
                except QuotaExceededError:
                    db.update_clip_status(clip_id, 'draft')
                    draft_count += 1
                    for remaining_clip_id, _, _ in encoded_clips[encoded_clips.index((clip_id, clip, output_path))+1:]:
                        db.update_clip_status(remaining_clip_id, 'draft')
                        draft_count += 1
                    notify.send(
                        f"⚠️ <b>YouTube quota exceeded</b>\n"
                        f"{published_count} clips published.\n"
                        f"{draft_count} clips saved as drafts."
                    )
                    break
                except PublishError as e:
                    db.log_publish(clip_id, None, 'failed', error=str(e))
                    db.update_clip_status(clip_id, 'error')
        else:
            for clip_id, _, _ in encoded_clips:
                db.update_clip_status(clip_id, 'draft')
                draft_count += 1

        db.update_video_status(video_id, 'done')

        if config.publish_mode == 'auto':
            notify.send(f"🎉 <b>Done!</b> {published_count} Shorts published from ID: {youtube_video_id}")
        else:
            notify.send(f"📋 <b>Done (draft mode)</b>\n{draft_count} clips ready.\nRun: python publish.py --video-id {youtube_video_id}")

    except DownloadError as e:
        db.update_video_status(video_id, 'error', str(e))
        notify.send(f"⚠️ <b>Download failed</b>\nID: {youtube_video_id}\n<code>{str(e)[:200]}</code>")
        raise

    except Exception as e:
        db.update_video_status(video_id, 'error', str(e))
        notify.send(
            f"❌ <b>Pipeline error</b>\n"
            f"ID: {youtube_video_id}\n"
            f"<code>{str(e)[:300]}</code>"
        )
        raise

    finally:
        cleanup_tmp(youtube_video_id, config.working_dir)
```

**Startup sequence:**

```python
async def main():
    # 1. Load and validate config
    # 2. Initialise TelegramNotifier (disabled automatically if token/chat_id not set)
    # 3. Ensure Whisper model is downloaded
    # 4. Re-subscribe WebSub on startup (in case lease expired while daemon was down)
    # 5. Start pipeline_worker as background task
    # 6. Start uvicorn on port 8000
```

---

## `publish.py` — Manual CLI (for PUBLISH_MODE=draft)

```
Usage: python publish.py --video-id <youtube_video_id>

Lists all draft clips for the given video and prompts for confirmation before uploading.
```

```python
import argparse

parser = argparse.ArgumentParser()
parser.add_argument('--video-id', required=True)
args = parser.parse_args()

db = DB(config)
clips = db.get_draft_clips(args.video_id)

if not clips:
    print("No draft clips found.")
    exit(0)

print(f"\nDraft clips for {args.video_id}:")
for clip in clips:
    print(f"  [{clip['clip_index']}] {clip['title']} ({clip['duration']}s)")
    print(f"       {config.working_dir}/output/{args.video_id}/clip_{clip['clip_index']}.mp4")

confirm = input("\nPublish all? (y/n): ")
if confirm.lower() != 'y':
    print("Aborted.")
    exit(0)

for clip in clips:
    path = f"{config.working_dir}/output/{args.video_id}/clip_{clip['clip_index']}.mp4"
    short_id = upload_short(path, clip['title'], ..., config)
    db.log_publish(clip['id'], short_id, 'success')
    db.update_clip_status(clip['id'], 'published')
    print(f"  Published clip {clip['clip_index']}: https://youtube.com/shorts/{short_id}")
```

---

## Cloudflare Worker (`workers/websub_worker.js`)

```javascript
export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // WebSub verification — YouTube hub sends a GET to confirm subscription
    if (request.method === 'GET' && url.pathname === '/websub') {
      const challenge = url.searchParams.get('hub.challenge');
      if (challenge) {
        return new Response(challenge, {
          status: 200,
          headers: { 'Content-Type': 'text/plain' }
        });
      }
    }

    // WebSub push — YouTube hub sends POST when a new video is published
    if (request.method === 'POST' && url.pathname === '/websub') {
      const body = await request.text();

      // Validate X-Hub-Signature (HMAC-SHA1 of body using WEBSUB_SECRET)
      const sig = request.headers.get('X-Hub-Signature') || '';
      const [algo, receivedHex] = sig.split('=');
      if (algo !== 'sha1') {
        return new Response('Bad signature algorithm', { status: 400 });
      }

      const encoder = new TextEncoder();
      const key = await crypto.subtle.importKey(
        'raw',
        encoder.encode(env.WEBSUB_SECRET),
        { name: 'HMAC', hash: 'SHA-1' },
        false,
        ['sign']
      );
      const signature = await crypto.subtle.sign('HMAC', key, encoder.encode(body));
      const computedHex = Array.from(new Uint8Array(signature))
        .map(b => b.toString(16).padStart(2, '0')).join('');

      if (computedHex !== receivedHex) {
        return new Response('Invalid signature', { status: 403 });
      }

      // Forward to Oracle VM
      try {
        await fetch(env.VM_WEBHOOK_URL, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/xml',
            'X-Webhook-Secret': env.VM_WEBHOOK_SECRET,
          },
          body: body,
        });
      } catch (err) {
        // Log but return 200 to the hub — do not cause re-delivery loops
        console.error('Failed to forward to VM:', err);
      }

      return new Response('OK', { status: 200 });
    }

    return new Response('Not Found', { status: 404 });
  }
};
```

**Deploy:**
```bash
cd workers
wrangler deploy websub_worker.js
wrangler secret put WEBSUB_SECRET
wrangler secret put VM_WEBHOOK_URL
wrangler secret put VM_WEBHOOK_SECRET
```

---

## GitHub Actions

### `.github/workflows/websub_renew.yml`

```yaml
name: Renew WebSub Subscription
on:
  schedule:
    - cron: '0 8 */9 * *'    # Every 9 days at 8am UTC
  workflow_dispatch:           # Allow manual trigger

jobs:
  renew:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install requests python-dotenv
      - run: python scripts/websub_renew.py
        env:
          YOUTUBE_CHANNEL_ID: ${{ secrets.YOUTUBE_CHANNEL_ID }}
          WEBSUB_CALLBACK_URL: ${{ secrets.WEBSUB_CALLBACK_URL }}
```

### `scripts/websub_renew.py`

```python
import os, requests

channel_id = os.environ['YOUTUBE_CHANNEL_ID']
callback_url = os.environ['WEBSUB_CALLBACK_URL']
hub_url = 'https://pubsubhubbub.appspot.com/subscribe'
feed_url = f'https://www.youtube.com/xml/feeds/videos.xml?channel_id={channel_id}'

response = requests.post(hub_url, data={
    'hub.mode': 'subscribe',
    'hub.topic': feed_url,
    'hub.callback': callback_url,
    'hub.lease_seconds': 864000,
})

if response.status_code == 202:
    print("WebSub subscription renewed successfully.")
else:
    print(f"Failed: {response.status_code} {response.text}")
    exit(1)
```

---

## Oracle VM Deployment

### `scripts/setup.sh`

```bash
#!/bin/bash
set -e

echo "=== AutoClip Setup ==="

# System deps
sudo apt update && sudo apt install -y ffmpeg python3-pip python3-venv git unzip wget

# Project directory
mkdir -p /home/ubuntu/autoclip/{tmp/source,tmp/captions,tmp/clips,tmp/output,tokens,assets/fonts}
cd /home/ubuntu/autoclip

# Python virtual env
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Download Whisper medium model (~1.5 GB, one-time)
echo "Downloading Whisper medium model..."
python3 -c "import whisper; whisper.load_model('medium')"

# Download Montserrat font
echo "Downloading Montserrat font..."
wget -q -O /tmp/Montserrat.zip "https://fonts.google.com/download?family=Montserrat"
unzip -o /tmp/Montserrat.zip -d /home/ubuntu/autoclip/assets/fonts/

# Systemd service
sudo tee /etc/systemd/system/autoclip.service > /dev/null <<EOF
[Unit]
Description=AutoClip Pipeline Daemon
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/autoclip
ExecStart=/home/ubuntu/autoclip/venv/bin/python main.py
Restart=always
RestartSec=15
EnvironmentFile=/home/ubuntu/autoclip/.env
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable autoclip
sudo systemctl start autoclip

echo "=== Setup complete. Check status with: sudo journalctl -u autoclip -f ==="
```

### Oracle VM firewall (one-time, in Oracle Console)

Add an ingress rule to the VM's subnet security list:
- Source: `0.0.0.0/0`
- Protocol: TCP
- Destination port range: `8000`

Then apply iptables locally on the VM:
```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 8000 -j ACCEPT
sudo netfilter-persistent save
```

---

## Error Handling Rules

All of these are mandatory. Do not skip any.

1. **Deduplication first.** `db.video_exists()` is the absolute first operation. If it returns `True`, log and return immediately. Never process the same video twice.

2. **Status before every step.** Call `db.update_video_status()` before starting each pipeline stage. If the daemon crashes, Supabase shows exactly where it stopped.

3. **yt-dlp failure.** Capture stderr. Set video status to `error` with the stderr as the message. Do not retry automatically. Move on. The daemon must not crash.

4. **Whisper OOM.** Catch `RuntimeError` and `torch.cuda.OutOfMemoryError`. Retry once with `small` model. Log a warning. If small model also fails, set status to `error`.

5. **LLM JSON failure.** Retry once with stricter prompt. If second attempt fails, use rule-based fallback. Never block the pipeline on LLM failures.

6. **YouTube quota exceeded.** On `HttpError 403 quotaExceeded`, stop publishing immediately. Set remaining unprocessed clips to `draft`. Log the quota error. Do not retry today.

7. **FFmpeg failure.** Capture stderr. Raise `EncoderError` with the FFmpeg stderr included. The orchestrator catches this, sets clip status to `error`, and continues with next clip.

8. **Cleanup in finally.** Always delete `tmp/source/<video_id>.mp4`, `tmp/captions/<video_id>.*`, and `tmp/clips/<video_id>/` after processing, whether the job succeeded or failed. Only keep `tmp/output/<video_id>/` if `PUBLISH_MODE=draft`.

9. **Structured logging.** Every log line must include the video ID. Format: `[video_id] message`. Use Python `logging` module with `INFO` level by default. Set `LOG_LEVEL=DEBUG` in `.env` for verbose output.

---

## `requirements.txt`

```
openai-whisper
stable-ts
yt-dlp
openai
supabase
google-api-python-client
google-auth-oauthlib
google-auth-httplib2
fastapi
uvicorn[standard]
APScheduler
python-dotenv
requests
```

---

## Build Order (Vertical Slices — Do Not Skip Ahead)

| Slice | What to build | Done when |
|---|---|---|
| 1 | `config.py`, `.env.example`, Supabase schema | Config loads without error, schema exists in Supabase |
| 2 | `src/state/db.py` | All DB methods work against real Supabase |
| 3 | Cloudflare Worker + `scripts/websub_renew.py` | Subscribe to a test channel, verify Worker receives push and forwards it |
| 4 | FastAPI `/websub` + `/trigger` in `main.py` | Worker push arrives at VM, video ID lands in Supabase with status `pending` |
| 5 | `src/downloader/ytdlp.py` | `scripts/test_clip.py --url <url>` downloads MP4 to tmp/source |
| 6 | `src/transcriber/whisper_engine.py` | SRT file with word timestamps generated correctly from a test MP4 |
| 7 | `src/selector/llm_selector.py` | LLM returns valid JSON clips; rule-based fallback also works |
| 8 | `src/processor/crop.py` + `captions.py` + `encoder.py` | 1080x1920 MP4 with captions produced correctly |
| 9 | `src/publisher/youtube.py` | Test clip appears on YouTube Shorts |
| 10 | Wire orchestrator in `main.py` + `pipeline_worker` | Full end-to-end run from WebSub push to published Short |
| 11 | `publish.py` CLI | Draft mode works: clips saved locally, manual publish uploads them |
| 12 | Error handling audit, `scripts/setup.sh`, systemd service, GitHub Actions | Daemon survives a simulated crash and restarts cleanly |

---

## Smoke Test Checklist

Run through every item before calling the build done.

- [ ] New video published on source channel triggers WebSub push within 5 minutes
- [ ] Cloudflare Worker validates signature and forwards to Oracle VM
- [ ] `/trigger` endpoint receives push, extracts video ID, enqueues it
- [ ] Supabase `videos` table shows new row with status `pending`
- [ ] yt-dlp downloads video to `tmp/source/`, file is >1 MB
- [ ] Whisper produces `.srt` with word-level timestamps
- [ ] LLM returns 3-5 clips with timestamps inside video duration
- [ ] FFmpeg produces 1080x1920 MP4 with visible captions for each clip
- [ ] YouTube Shorts upload succeeds, Short appears on channel
- [ ] Supabase `clips` and `publish_log` updated correctly
- [ ] Tmp files cleaned up after successful run
- [ ] Re-triggering the same video ID does nothing (deduplication works)
- [ ] yt-dlp failure sets status to `error` without crashing the daemon
- [ ] YouTube quota error leaves remaining clips as `draft` without crashing
- [ ] Telegram message received when new video is detected
- [ ] Telegram message received for each published Short with correct YouTube link
- [ ] Telegram error message received on a simulated pipeline failure
- [ ] Pipeline continues normally when TELEGRAM_BOT_TOKEN is left empty
- [ ] `publish.py --video-id <id>` publishes draft clips correctly

---

## Cost Summary

| Service | Usage | Cost |
|---|---|---|
| Oracle Cloud Always Free ARM VM | 4 vCPU, 24 GB RAM, 200 GB storage | $0 |
| Cloudflare Workers | WebSub receiver, well under 100K req/day | $0 |
| YouTube Data API v3 | Monitoring + uploads, well under 10K units/day | $0 |
| Supabase | Pipeline state DB, well under 500 MB | $0 |
| GitHub Actions | Two cron jobs, well under 2K min/month | $0 |
| Whisper medium (local) | Runs on Oracle VM | $0 |
| GPT-4o-mini | ~$0.003 per video, 20 videos/month = $0.06/month | ~$0.72/year |
| **Total** | | **< $1/year** |
