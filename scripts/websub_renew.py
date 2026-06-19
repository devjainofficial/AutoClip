"""
Renews the WebSub subscription for the monitored YouTube channel.
Called by GitHub Actions every 9 days, or run manually:
    python scripts/websub_renew.py
"""
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

HUB_URL = "https://pubsubhubbub.appspot.com/subscribe"


def main() -> None:
    channel_id = os.environ.get("YOUTUBE_CHANNEL_ID", "").strip()
    callback_url = os.environ.get("WEBSUB_CALLBACK_URL", "").strip()

    if not channel_id or not callback_url:
        print("ERROR: YOUTUBE_CHANNEL_ID and WEBSUB_CALLBACK_URL must be set.")
        sys.exit(1)

    feed_url = f"https://www.youtube.com/xml/feeds/videos.xml?channel_id={channel_id}"

    response = requests.post(
        HUB_URL,
        data={
            "hub.mode": "subscribe",
            "hub.topic": feed_url,
            "hub.callback": callback_url,
            "hub.lease_seconds": 864000,
        },
        timeout=10,
    )

    if response.status_code == 202:
        print(f"WebSub subscription renewed for channel {channel_id}.")
        print(f"Callback: {callback_url}")
    else:
        print(f"FAILED: HTTP {response.status_code}")
        print(response.text)
        sys.exit(1)


if __name__ == "__main__":
    main()
