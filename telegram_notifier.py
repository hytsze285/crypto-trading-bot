import logging
from typing import Optional

import requests

from config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TELEGRAM_ENABLED,
)

logger = logging.getLogger("crypto_bot.telegram")


class TelegramNotifier:
    def __init__(
        self,
        enabled: bool = TELEGRAM_ENABLED,
        bot_token: str = TELEGRAM_BOT_TOKEN,
        chat_id: str = TELEGRAM_CHAT_ID,
        timeout: int = 10,
    ) -> None:
        self.enabled = bool(enabled and bot_token and chat_id)
        self.bot_token = bot_token
        self.chat_id = str(chat_id) if chat_id is not None else ""
        self.timeout = timeout

    def send(self, text: str) -> bool:
        if not self.enabled:
            return False

        text = (text or "").strip()
        if not text:
            return False

        if len(text) > 4096:
            text = text[:4000] + "\n...[truncated]"

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }

        try:
            resp = requests.post(url, json=payload, timeout=self.timeout)
            try:
                data = resp.json()
            except Exception:
                data = {"raw_text": resp.text}

            if resp.status_code >= 400:
                logger.error("Telegram HTTP %s response: %s", resp.status_code, data)
                return False

            if not data.get("ok", False):
                logger.warning("Telegram send failed: %s", data)
                return False
            return True
        except Exception:
            logger.exception("Telegram send exception")
            return False


_notifier: Optional[TelegramNotifier] = None


def get_notifier() -> TelegramNotifier:
    global _notifier
    if _notifier is None:
        _notifier = TelegramNotifier()
    return _notifier


def notify(text: str) -> bool:
    return get_notifier().send(text)
