import hashlib
import logging
import threading
import time
from typing import Optional

from config import (
    TELEGRAM_NOTIFY_ERRORS,
    TELEGRAM_NOTIFY_SIGNALS,
    TELEGRAM_NOTIFY_STARTUP,
)
from notification_formatter import (
    format_error_message,
    format_execution_message,
    format_signal_message,
    format_startup_check_failed,
    format_startup_check_passed,
    format_startup_message,
    format_subscription_message,
    format_ws_connecting_message,
)
from telegram_notifier import send_telegram_message

logger = logging.getLogger("crypto_bot.notification")

DEDUP_WINDOW_SECONDS = 300
MIN_SEND_INTERVAL_SECONDS = 2

CATEGORY_RATE_LIMITS = {
    "startup": 60,
    "subscription": 60,
    "hold": 900,
    "signal": 5,
    "execution": 2,
    "error": 120,
    "ws_reconnect": 300,
    "heartbeat": 1800,
    "market_data_timeout": 300,
}

_lock = threading.Lock()
_recent_messages: dict[str, float] = {}
_last_send_ts: float = 0.0
_last_category_ts: dict[str, float] = {}
_last_signal_state: dict[str, str] = {}


def _now() -> float:
    return time.time()


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _prune_old_entries(now_ts: float) -> None:
    expired = [
        key for key, ts in _recent_messages.items()
        if now_ts - ts > DEDUP_WINDOW_SECONDS
    ]
    for key in expired:
        _recent_messages.pop(key, None)


def _should_send(message: str, category: Optional[str]) -> tuple[bool, str]:
    global _last_send_ts

    now_ts = _now()
    msg_hash = _hash_text(message)

    with _lock:
        _prune_old_entries(now_ts)

        last_same_ts = _recent_messages.get(msg_hash)
        if last_same_ts is not None and now_ts - last_same_ts < DEDUP_WINDOW_SECONDS:
            return False, "duplicate_message"

        if now_ts - _last_send_ts < MIN_SEND_INTERVAL_SECONDS:
            return False, "global_rate_limit"

        if category:
            limit = CATEGORY_RATE_LIMITS.get(category)
            last_cat_ts = _last_category_ts.get(category)
            if (
                limit is not None
                and last_cat_ts is not None
                and now_ts - last_cat_ts < limit
            ):
                return False, f"category_rate_limit:{category}"

        _recent_messages[msg_hash] = now_ts
        _last_send_ts = now_ts
        if category:
            _last_category_ts[category] = now_ts

    return True, "ok"


def _send(message: str, enabled: bool, category: Optional[str]) -> bool:
    if not enabled:
        return False

    should_send, reason = _should_send(message, category)
    if not should_send:
        logger.info("Notification suppressed: %s", reason)
        return False

    return send_telegram_message(message)


def notify_startup(pair: str, run_mode: str, equity: float | None = None) -> bool:
    return _send(
        format_startup_message(pair, run_mode, equity),
        TELEGRAM_NOTIFY_STARTUP,
        "startup",
    )


def notify_ws_connecting(endpoint: str) -> bool:
    return _send(
        format_ws_connecting_message(endpoint),
        TELEGRAM_NOTIFY_STARTUP,
        "startup",
    )


def notify_subscription(pair: str, channel: str = "trades") -> bool:
    return _send(
        format_subscription_message(pair, channel),
        TELEGRAM_NOTIFY_STARTUP,
        "subscription",
    )


def notify_startup_check_passed(pair: str, result: dict) -> bool:
    return _send(
        format_startup_check_passed(pair, result),
        TELEGRAM_NOTIFY_STARTUP,
        "startup",
    )


def notify_startup_check_failed(pair: str, error_text: str) -> bool:
    return _send(
        format_startup_check_failed(pair, error_text),
        TELEGRAM_NOTIFY_ERRORS,
        "error",
    )


def notify_signal(
    pair: str,
    signal: str,
    reason: str,
    meta: dict | None = None,
    phase: str | None = None,
) -> bool:
    normalized_signal = str(signal or "").upper()
    state_key = f"{pair}:{phase or 'general'}"
    state_value = f"{normalized_signal}|{reason}|{meta}"

    with _lock:
        previous = _last_signal_state.get(state_key)
        if previous == state_value:
            logger.info("Signal state unchanged, skip notify: %s", state_key)
            return False
        _last_signal_state[state_key] = state_value

    category = "hold" if normalized_signal == "HOLD" else "signal"
    return _send(
        format_signal_message(pair, signal, reason, meta, phase),
        TELEGRAM_NOTIFY_SIGNALS,
        category,
    )


def notify_execution(
    run_mode: str,
    side: str,
    pair: str,
    quantity: float,
    price: float,
    reason: str | None = None,
    stop_loss: float | None = None,
    take_profit_1: float | None = None,
    take_profit_2: float | None = None,
    result: str | None = None,
) -> bool:
    return _send(
        format_execution_message(
            run_mode=run_mode,
            side=side,
            pair=pair,
            quantity=quantity,
            price=price,
            reason=reason,
            stop_loss=stop_loss,
            take_profit_1=take_profit_1,
            take_profit_2=take_profit_2,
            result=result,
        ),
        TELEGRAM_NOTIFY_SIGNALS,
        "execution",
    )


def notify_error(
    module: str,
    error_text: str,
    action: str | None = None,
    pair: str | None = None,
    category: str = "error",
) -> bool:
    return _send(
        format_error_message(module, error_text, action=action, pair=pair),
        TELEGRAM_NOTIFY_ERRORS,
        category,
    )


def notify_heartbeat(
    pair: str,
    run_mode: str,
    last_price: float | None,
    last_trade_time: str | None,
) -> bool:
    price_text = f"{last_price:.8f}" if last_price is not None else "未知"
    trade_time_text = last_trade_time or "未知"
    message = (
        "💓 Bot 运行正常\n\n"
        f"交易对: {pair}\n"
        f"模式: {run_mode}\n"
        f"最近价格: {price_text}\n"
        f"最近行情时间: {trade_time_text}"
    )
    return _send(message, TELEGRAM_NOTIFY_STARTUP, "heartbeat")


def notify_market_data_timeout(
    pair: str,
    timeout_seconds: int,
    last_trade_time: str | None,
) -> bool:
    trade_time_text = last_trade_time or "未知"
    message = (
        "🚨 行情数据超时\n\n"
        f"交易对: {pair}\n"
        f"超时阈值: {timeout_seconds} 秒\n"
        f"最近行情时间: {trade_time_text}\n"
        "处理: 将主动重连 WebSocket"
    )
    return _send(message, TELEGRAM_NOTIFY_ERRORS, "market_data_timeout")
