import os
from pathlib import Path
from dotenv import load_dotenv

# Always prefer values from .env over stale shell variables.
load_dotenv(override=True)

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _get_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value.strip())


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return float(value.strip())


def _get_str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip()


# OKX credentials
OKX_API_KEY = _get_str("OKX_API_KEY")
OKX_SECRET_KEY = _get_str("OKX_SECRET_KEY")
OKX_PASSPHRASE = _get_str("OKX_PASSPHRASE")

# Environment / endpoints
USE_SIMULATED_TRADING = _get_bool("USE_SIMULATED_TRADING", False)
OKX_REST_URL = _get_str("OKX_REST_URL", "https://us.okx.com")

if USE_SIMULATED_TRADING:
    OKX_PUBLIC_WS = _get_str(
        "OKX_PUBLIC_WS",
        "wss://wsuspap.okx.com:8443/ws/v5/public",
    )
else:
    OKX_PUBLIC_WS = _get_str(
        "OKX_PUBLIC_WS",
        "wss://wsus.okx.com:8443/ws/v5/public",
    )

# Bot runtime
TRADING_PAIR = _get_str("TRADING_PAIR", "ROBO-USDT")
ALLOWED_INST_ID = _get_str("ALLOWED_INST_ID", TRADING_PAIR)
RUN_MODE = _get_str("RUN_MODE", "monitor")
ENABLE_LIVE_TRADING = _get_bool("ENABLE_LIVE_TRADING", False)

INITIAL_EQUITY = _get_float("INITIAL_EQUITY", 1000.0)
OKX_HTTP_TIMEOUT = _get_int("OKX_HTTP_TIMEOUT", 10)
OKX_ORDER_EXP_WINDOW_MS = _get_int("OKX_ORDER_EXP_WINDOW_MS", 3000)

# Strategy
TREND_FAST_MA = _get_int("TREND_FAST_MA", 20)
TREND_SLOW_MA = _get_int("TREND_SLOW_MA", 60)
BREAKOUT_LOOKBACK = _get_int("BREAKOUT_LOOKBACK", 20)
VOLUME_LOOKBACK = _get_int("VOLUME_LOOKBACK", 20)
VOLUME_MULTIPLIER = _get_float("VOLUME_MULTIPLIER", 1.5)

STOP_LOSS_PCT = _get_float("STOP_LOSS_PCT", 0.012)
TAKE_PROFIT_1_PCT = _get_float("TAKE_PROFIT_1_PCT", 0.02)
TAKE_PROFIT_2_PCT = _get_float("TAKE_PROFIT_2_PCT", 0.04)
MAX_HOLDING_MINUTES = _get_int("MAX_HOLDING_MINUTES", 30)

RISK_PER_TRADE_PCT = _get_float("RISK_PER_TRADE_PCT", 0.005)
MAX_POSITION_NOTIONAL_PCT = _get_float("MAX_POSITION_NOTIONAL_PCT", 0.03)
MIN_ORDER_NOTIONAL = _get_float("MIN_ORDER_NOTIONAL", 5.0)

MAX_DAILY_LOSS_PCT = _get_float("MAX_DAILY_LOSS_PCT", 0.02)
MAX_CONSECUTIVE_LOSSES = _get_int("MAX_CONSECUTIVE_LOSSES", 3)
MAX_TRADES_PER_DAY = _get_int("MAX_TRADES_PER_DAY", 5)
COOLDOWN_MINUTES = _get_int("COOLDOWN_MINUTES", 10)

# Runtime buffers / reconnect
MARKET_DATA_BUFFER_SIZE = _get_int("MARKET_DATA_BUFFER_SIZE", 2000)
WEBSOCKET_PING_INTERVAL = _get_int("WEBSOCKET_PING_INTERVAL", 20)
WEBSOCKET_RECONNECT_INTERVAL = _get_int("WEBSOCKET_RECONNECT_INTERVAL", 5)

# Files / logging
STATE_FILE = _get_str("STATE_FILE", str(BASE_DIR / "state.json"))
LOG_LEVEL = _get_str("LOG_LEVEL", "INFO")
LOG_FILE = _get_str("LOG_FILE", str(LOG_DIR / "bot.log"))

# Telegram notifications
TELEGRAM_ENABLED = _get_bool("TELEGRAM_ENABLED", False)
TELEGRAM_BOT_TOKEN = _get_str("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = _get_str("TELEGRAM_CHAT_ID", "")
TELEGRAM_NOTIFY_STARTUP = _get_bool("TELEGRAM_NOTIFY_STARTUP", True)
TELEGRAM_NOTIFY_SIGNALS = _get_bool("TELEGRAM_NOTIFY_SIGNALS", True)
TELEGRAM_NOTIFY_ERRORS = _get_bool("TELEGRAM_NOTIFY_ERRORS", True)
