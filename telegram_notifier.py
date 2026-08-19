import hashlib
import logging
import time
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
        min_interval_seconds: int = 3,
        duplicate_suppression_seconds: int = 300,
    ) -> None:
        self.enabled = bool(enabled and bot_token and chat_id)
        self.bot_token = bot_token
        self.chat_id = str(chat_id) if chat_id is not None else ""
        self.timeout = timeout

        self.min_interval_seconds = min_interval_seconds
        self.duplicate_suppression_seconds = duplicate_suppression_seconds

        self._last_send_ts = 0.0
        self._recent_messages: dict[str, float] = {}

    def _hash_text(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _cleanup_recent(self, now: float) -> None:
        cutoff = now - self.duplicate_suppression_seconds
        expired = [k for k, ts in self._recent_messages.items() if ts < cutoff]
        for k in expired:
            self._recent_messages.pop(k, None)

    def send(self, text: str) -> bool:
        if not self.enabled:
            return False

        text = (text or "").strip()
        if not text:
            return False

        if len(text) > 4096:
            text = text[:4000] + "\n...[truncated]"

        now = time.time()
        self._cleanup_recent(now)

        msg_hash = self._hash_text(text)
        last_same_ts = self._recent_messages.get(msg_hash)

        if last_same_ts is not None and (now - last_same_ts) < self.duplicate_suppression_seconds:
            logger.info("Telegram duplicate suppressed")
            return False

        if (now - self._last_send_ts) < self.min_interval_seconds:
            logger.info("Telegram rate-limited")
            return False

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

            self._last_send_ts = now
            self._recent_messages[msg_hash] = now
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
