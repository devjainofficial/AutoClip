import logging

import requests

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str) -> None:
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.enabled = bool(token and chat_id)

    def send(self, message: str) -> None:
        """
        Send a plain text message to the configured chat.
        Silently logs and returns on any failure — never raises.
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
