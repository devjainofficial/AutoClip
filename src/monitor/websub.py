import logging
import xml.etree.ElementTree as ET
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_FEED_URL = "https://www.youtube.com/xml/feeds/videos.xml?channel_id={channel_id}"
_LEASE_SECONDS = 864000  # 10 days — max allowed by YouTube's hub


def subscribe(channel_id: str, callback_url: str, hub_url: str) -> bool:
    """
    POST to hub_url to subscribe to the channel's Atom feed.
    Feed URL: https://www.youtube.com/xml/feeds/videos.xml?channel_id={channel_id}
    Lease: 864000 seconds (10 days — max allowed by YouTube's hub).
    Returns True on 202 response from hub.
    """
    feed_url = _FEED_URL.format(channel_id=channel_id)
    payload = {
        "hub.mode": "subscribe",
        "hub.topic": feed_url,
        "hub.callback": callback_url,
        "hub.lease_seconds": _LEASE_SECONDS,
    }
    try:
        response = requests.post(hub_url, data=payload, timeout=10)
        if response.status_code == 202:
            logger.info(
                f"[websub] Subscribed to channel {channel_id}. "
                f"Lease: {_LEASE_SECONDS}s. Callback: {callback_url}"
            )
            return True
        else:
            logger.error(
                f"[websub] Subscription failed: HTTP {response.status_code} — {response.text}"
            )
            return False
    except requests.RequestException as e:
        logger.error(f"[websub] Subscription request error: {e}")
        return False


def parse_push_notification(xml_body: str) -> Optional[str]:
    """
    Parse the Atom XML payload sent by the YouTube hub on a new video publish.
    Returns the YouTube video ID (yt:videoId element), or None if not a new-video event.
    """
    try:
        root = ET.fromstring(xml_body)
    except ET.ParseError as e:
        logger.warning(f"[websub] Failed to parse push XML: {e}")
        return None

    # Atom + YouTube namespace
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
    }

    # A deletion event has <at:deleted-entry> — not a new video
    deleted = root.find(".//{http://purl.org/atompub/tombstones/1.0}deleted-entry")
    if deleted is not None:
        logger.debug("[websub] Received deletion event — ignoring")
        return None

    video_id_el = root.find(".//yt:videoId", ns)
    if video_id_el is None or not video_id_el.text:
        logger.warning("[websub] Push notification contained no yt:videoId")
        return None

    video_id = video_id_el.text.strip()
    logger.info(f"[websub] New video detected: {video_id}")
    return video_id
