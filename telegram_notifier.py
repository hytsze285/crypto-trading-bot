import logging

import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_ENABLED

logger = logging.getLogger("crypto_bot.telegram")


def send_telegram_message(message: str) -> bool:
    if not TELEGRAM_ENABLED:
        return False
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram disabled or missing bot token/chat id")
        return False

    text = (message or "").strip()
    if not text:
        return False

    if len(text) > 4096:
        text = text[:4000] + "\n...[truncated]"

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("Telegram notify failed: %s", exc)
        return False


# 向后兼容旧调用
def notify(message: str) -> bool:
    return send_telegram_message(message)
