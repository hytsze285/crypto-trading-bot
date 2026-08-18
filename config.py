import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

OKX_API_KEY = os.getenv("OKX_API_KEY", "")
OKX_SECRET_KEY = os.getenv("OKX_SECRET_KEY", "")
OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE", "")

USE_SIMULATED_TRADING = os.getenv("USE_SIMULATED_TRADING", "true").lower() == "true"

if USE_SIMULATED_TRADING:
    OKX_REST_URL = "https://openapi-sandbox.okx.com"
    OKX_PUBLIC_WS = "wss://wspap.okx.com:8443/ws/v5/public"
    OKX_PRIVATE_WS = "wss://wspap.okx.com:8443/ws/v5/private"
else:
    OKX_REST_URL = "https://openapi.okx.com"
    OKX_PUBLIC_WS = "wss://ws.okx.com:8443/ws/v5/public"
    OKX_PRIVATE_WS = "wss://ws.okx.com:8443/ws/v5/private"

TRADING_PAIR = os.getenv("TRADING_PAIR", "ROBO-USDT")
QUOTE_ASSET = "USDT"
INITIAL_EQUITY = float(os.getenv("INITIAL_EQUITY", "1000"))

TREND_FAST_MA = int(os.getenv("TREND_FAST_MA", "20"))
TREND_SLOW_MA = int(os.getenv("TREND_SLOW_MA", "60"))
BREAKOUT_LOOKBACK = int(os.getenv("BREAKOUT_LOOKBACK", "20"))
VOLUME_LOOKBACK = int(os.getenv("VOLUME_LOOKBACK", "20"))
VOLUME_MULTIPLIER = float(os.getenv("VOLUME_MULTIPLIER", "1.5"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.012"))
TAKE_PROFIT_1_PCT = float(os.getenv("TAKE_PROFIT_1_PCT", "0.02"))
TAKE_PROFIT_2_PCT = float(os.getenv("TAKE_PROFIT_2_PCT", "0.04"))
MAX_HOLDING_MINUTES = int(os.getenv("MAX_HOLDING_MINUTES", "30"))
RISK_PER_TRADE_PCT = float(os.getenv("RISK_PER_TRADE_PCT", "0.005"))
MAX_POSITION_NOTIONAL_PCT = float(os.getenv("MAX_POSITION_NOTIONAL_PCT", "0.03"))
MIN_ORDER_NOTIONAL = float(os.getenv("MIN_ORDER_NOTIONAL", "5.0"))
MAX_DAILY_LOSS_PCT = float(os.getenv("MAX_DAILY_LOSS_PCT", "0.02"))
MAX_CONSECUTIVE_LOSSES = int(os.getenv("MAX_CONSECUTIVE_LOSSES", "3"))
MAX_TRADES_PER_DAY = int(os.getenv("MAX_TRADES_PER_DAY", "5"))
COOLDOWN_MINUTES = int(os.getenv("COOLDOWN_MINUTES", "10"))

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "bot.log"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
STATE_FILE = Path("bot_state.json")

RUN_MODE = os.getenv("RUN_MODE", "monitor")
MARKET_DATA_BUFFER_SIZE = 500
WEBSOCKET_RECONNECT_INTERVAL = 5
WEBSOCKET_PING_INTERVAL = 20
