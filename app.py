import asyncio
import json
import logging
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Deque, List, Optional

import websockets

from config import (
    ALLOWED_INST_ID,
    COOLDOWN_MINUTES,
    ENABLE_LIVE_TRADING,
    INITIAL_EQUITY,
    LOG_FILE,
    LOG_LEVEL,
    MARKET_DATA_BUFFER_SIZE,
    MAX_CONSECUTIVE_LOSSES,
    MAX_DAILY_LOSS_PCT,
    MAX_HOLDING_MINUTES,
    MAX_POSITION_NOTIONAL_PCT,
    MAX_TRADES_PER_DAY,
    MIN_ORDER_NOTIONAL,
    OKX_API_KEY,
    OKX_PASSPHRASE,
    OKX_PUBLIC_WS,
    OKX_SECRET_KEY,
    OKX_HTTP_TIMEOUT,
    OKX_ORDER_EXP_WINDOW_MS,
    RUN_MODE,
    STATE_FILE,
    STOP_LOSS_PCT,
    TAKE_PROFIT_1_PCT,
    TAKE_PROFIT_2_PCT,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TELEGRAM_ENABLED,
    TRADING_PAIR,
    TREND_FAST_MA,
    TREND_SLOW_MA,
    BREAKOUT_LOOKBACK,
    USE_SIMULATED_TRADING,
    VOLUME_LOOKBACK,
    VOLUME_MULTIPLIER,
    WEBSOCKET_PING_INTERVAL,
    WEBSOCKET_RECONNECT_INTERVAL,
    RISK_PER_TRADE_PCT,
)
from exchange_api import OKXApiError, OKXClient, OKXSafetyError
from notification_service import (
    notify_error,
    notify_execution,
    notify_heartbeat,
    notify_market_data_timeout,
    notify_signal,
    notify_startup,
    notify_startup_check_failed,
    notify_startup_check_passed,
    notify_subscription,
    notify_ws_connecting,
)
from strategy import Candle, StrategyConfig, StrategyEngine


HEARTBEAT_INTERVAL_SECONDS = 1800
MARKET_DATA_TIMEOUT_SECONDS = 120
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 5


def validate_startup_config() -> None:
    errors: list[str] = []

    valid_modes = {"monitor", "simulated_trade", "live_trade"}
    if RUN_MODE not in valid_modes:
        errors.append(f"RUN_MODE 非法: {RUN_MODE}，必须是 {sorted(valid_modes)}")

    if not TRADING_PAIR:
        errors.append("TRADING_PAIR 不能为空")

    if TELEGRAM_ENABLED:
        if not TELEGRAM_BOT_TOKEN:
            errors.append("TELEGRAM_ENABLED=true 时，TELEGRAM_BOT_TOKEN 不能为空")
        if not TELEGRAM_CHAT_ID:
            errors.append("TELEGRAM_ENABLED=true 时，TELEGRAM_CHAT_ID 不能为空")

    if RUN_MODE == "live_trade":
        if not ENABLE_LIVE_TRADING:
            errors.append("RUN_MODE=live_trade 时，ENABLE_LIVE_TRADING 必须为 true")
        if not OKX_API_KEY:
            errors.append("RUN_MODE=live_trade 时，OKX_API_KEY 不能为空")
        if not OKX_SECRET_KEY:
            errors.append("RUN_MODE=live_trade 时，OKX_SECRET_KEY 不能为空")
        if not OKX_PASSPHRASE:
            errors.append("RUN_MODE=live_trade 时，OKX_PASSPHRASE 不能为空")

    if ENABLE_LIVE_TRADING and RUN_MODE != "live_trade":
        errors.append("ENABLE_LIVE_TRADING=true 但 RUN_MODE 不是 live_trade，配置存在冲突")

    if INITIAL_EQUITY <= 0:
        errors.append("INITIAL_EQUITY 必须大于 0")

    if MARKET_DATA_BUFFER_SIZE <= 0:
        errors.append("MARKET_DATA_BUFFER_SIZE 必须大于 0")

    if WEBSOCKET_PING_INTERVAL <= 0:
        errors.append("WEBSOCKET_PING_INTERVAL 必须大于 0")

    if WEBSOCKET_RECONNECT_INTERVAL <= 0:
        errors.append("WEBSOCKET_RECONNECT_INTERVAL 必须大于 0")

    if TREND_FAST_MA <= 0 or TREND_SLOW_MA <= 0:
        errors.append("TREND_FAST_MA / TREND_SLOW_MA 必须大于 0")

    if TREND_FAST_MA >= TREND_SLOW_MA:
        errors.append("TREND_FAST_MA 应小于 TREND_SLOW_MA")

    if BREAKOUT_LOOKBACK <= 0:
        errors.append("BREAKOUT_LOOKBACK 必须大于 0")

    if VOLUME_LOOKBACK <= 0:
        errors.append("VOLUME_LOOKBACK 必须大于 0")

    if VOLUME_MULTIPLIER <= 0:
        errors.append("VOLUME_MULTIPLIER 必须大于 0")

    if STOP_LOSS_PCT <= 0:
        errors.append("STOP_LOSS_PCT 必须大于 0")

    if TAKE_PROFIT_1_PCT <= 0 or TAKE_PROFIT_2_PCT <= 0:
        errors.append("TAKE_PROFIT_1_PCT / TAKE_PROFIT_2_PCT 必须大于 0")

    if TAKE_PROFIT_1_PCT >= TAKE_PROFIT_2_PCT:
        errors.append("TAKE_PROFIT_1_PCT 应小于 TAKE_PROFIT_2_PCT")

    if MAX_HOLDING_MINUTES <= 0:
        errors.append("MAX_HOLDING_MINUTES 必须大于 0")

    if RISK_PER_TRADE_PCT <= 0:
        errors.append("RISK_PER_TRADE_PCT 必须大于 0")

    if MAX_POSITION_NOTIONAL_PCT <= 0:
        errors.append("MAX_POSITION_NOTIONAL_PCT 必须大于 0")

    if MIN_ORDER_NOTIONAL <= 0:
        errors.append("MIN_ORDER_NOTIONAL 必须大于 0")

    if MAX_DAILY_LOSS_PCT <= 0:
        errors.append("MAX_DAILY_LOSS_PCT 必须大于 0")

    if MAX_CONSECUTIVE_LOSSES <= 0:
        errors.append("MAX_CONSECUTIVE_LOSSES 必须大于 0")

    if MAX_TRADES_PER_DAY <= 0:
        errors.append("MAX_TRADES_PER_DAY 必须大于 0")

    if COOLDOWN_MINUTES < 0:
        errors.append("COOLDOWN_MINUTES 不能小于 0")

    if OKX_HTTP_TIMEOUT <= 0:
        errors.append("OKX_HTTP_TIMEOUT 必须大于 0")

    if OKX_ORDER_EXP_WINDOW_MS <= 0:
        errors.append("OKX_ORDER_EXP_WINDOW_MS 必须大于 0")

    if not OKX_PUBLIC_WS:
        errors.append("OKX_PUBLIC_WS 不能为空")

    if not ALLOWED_INST_ID:
        errors.append("ALLOWED_INST_ID 不能为空")

    if errors:
        raise ValueError("启动配置校验失败:\n- " + "\n- ".join(errors))


def setup_logger() -> logging.Logger:
    logger = logging.getLogger("crypto_bot")
    logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))

    if logger.handlers:
        return logger

    log_path = Path(LOG_FILE)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


logger = setup_logger()


class CandleBuilder:
    def __init__(self, timeframe_seconds: int):
        self.timeframe_seconds = timeframe_seconds
        self.current: Optional[Candle] = None

    def _bucket_start(self, ts: datetime) -> datetime:
        epoch = int(ts.timestamp())
        bucket = epoch - (epoch % self.timeframe_seconds)
        return datetime.fromtimestamp(bucket, tz=timezone.utc)

    def update(self, ts: datetime, price: float, volume: float) -> Optional[Candle]:
        bucket = self._bucket_start(ts)

        if self.current is None:
            self.current = Candle(
                ts=bucket,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=volume,
            )
            return None

        if bucket == self.current.ts:
            self.current.high = max(self.current.high, price)
            self.current.low = min(self.current.low, price)
            self.current.close = price
            self.current.volume += volume
            return None

        closed = self.current
        self.current = Candle(
            ts=bucket,
            open=price,
            high=price,
            low=price,
            close=price,
            volume=volume,
        )
        return closed


class MarketState:
    def __init__(self):
        self.candles_1m: Deque[Candle] = deque(maxlen=MARKET_DATA_BUFFER_SIZE)
        self.candles_5m: Deque[Candle] = deque(maxlen=MARKET_DATA_BUFFER_SIZE)
        self.last_price: Optional[float] = None
        self.last_ts: Optional[datetime] = None

        self.builder_1m = CandleBuilder(60)
        self.builder_5m = CandleBuilder(300)

    def on_trade(self, ts: datetime, price: float, volume: float) -> List[str]:
        self.last_price = price
        self.last_ts = ts
        events: List[str] = []

        closed_1m = self.builder_1m.update(ts, price, volume)
        if closed_1m:
            self.candles_1m.append(closed_1m)
            events.append("candle_1m_closed")

        closed_5m = self.builder_5m.update(ts, price, volume)
        if closed_5m:
            self.candles_5m.append(closed_5m)
            events.append("candle_5m_closed")

        return events


class BotStateStore:
    @staticmethod
    def save(engine: StrategyEngine) -> None:
        snapshot = engine.snapshot()
        Path(STATE_FILE).write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def load() -> Optional[dict]:
        if not Path(STATE_FILE).exists():
            return None
        try:
            return json.loads(Path(STATE_FILE).read_text(encoding="utf-8"))
        except Exception:
            return None


class TradingBot:
    def __init__(self):
        cfg = StrategyConfig(
            trend_fast_ma=TREND_FAST_MA,
            trend_slow_ma=TREND_SLOW_MA,
            breakout_lookback=BREAKOUT_LOOKBACK,
            volume_lookback=VOLUME_LOOKBACK,
            volume_multiplier=VOLUME_MULTIPLIER,
            stop_loss_pct=STOP_LOSS_PCT,
            take_profit_1_pct=TAKE_PROFIT_1_PCT,
            take_profit_2_pct=TAKE_PROFIT_2_PCT,
            max_holding_minutes=MAX_HOLDING_MINUTES,
            risk_per_trade_pct=RISK_PER_TRADE_PCT,
            max_position_notional_pct=MAX_POSITION_NOTIONAL_PCT,
            min_order_notional=MIN_ORDER_NOTIONAL,
            max_daily_loss_pct=MAX_DAILY_LOSS_PCT,
            max_consecutive_losses=MAX_CONSECUTIVE_LOSSES,
            max_trades_per_day=MAX_TRADES_PER_DAY,
            cooldown_minutes_after_sell=COOLDOWN_MINUTES,
        )
        self.engine = StrategyEngine(cfg)
        self.market = MarketState()
        self.equity = INITIAL_EQUITY
        self.order_lock = asyncio.Lock()
        self.okx = OKXClient(
            simulated=USE_SIMULATED_TRADING,
            allowed_inst_id=ALLOWED_INST_ID,
            enable_live_trading=ENABLE_LIVE_TRADING,
            timeout=OKX_HTTP_TIMEOUT,
        )

    def last_trade_time_str(self) -> str | None:
        if self.market.last_ts is None:
            return None
        return self.market.last_ts.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    async def heartbeat_loop(self):
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            notify_heartbeat(
                pair=TRADING_PAIR,
                run_mode=RUN_MODE,
                last_price=self.market.last_price,
                last_trade_time=self.last_trade_time_str(),
            )

    async def startup_check(self):
        try:
            result = await asyncio.to_thread(
                self.okx.startup_self_check,
                TRADING_PAIR,
            )
            logger.info("OKX startup self-check passed: %s", result)
            notify_startup_check_passed(TRADING_PAIR, result)
        except Exception as exc:
            logger.exception("OKX startup self-check failed: %s", exc)
            notify_startup_check_failed(TRADING_PAIR, str(exc))
            raise

    async def handle_closed_1m_candle(self):
        candles_1m = list(self.market.candles_1m)
        candles_5m = list(self.market.candles_5m)
        now = datetime.now(timezone.utc)

        if not self.engine.position.has_position:
            signal = self.engine.evaluate_entry(
                candles_1m=candles_1m,
                candles_5m=candles_5m,
                equity=self.equity,
                now=now,
            )
            logger.info("ENTRY signal=%s reason=%s meta=%s", signal.signal, signal.reason, signal.meta)
            notify_signal(TRADING_PAIR, signal.signal, signal.reason, signal.meta, phase="entry")

            if signal.signal == "BUY":
                await self.execute_buy(signal)
        else:
            signal = self.engine.evaluate_exit(
                candles_1m=candles_1m,
                now=now,
            )
            logger.info("EXIT signal=%s reason=%s meta=%s", signal.signal, signal.reason, signal.meta)
            notify_signal(TRADING_PAIR, signal.signal, signal.reason, signal.meta, phase="exit")

            if signal.signal == "SELL":
                await self.execute_sell(signal)

        BotStateStore.save(self.engine)

    async def execute_buy(self, signal):
        if RUN_MODE == "monitor":
            logger.warning(
                "[MONITOR] BUY %s qty=%.8f price=%.8f stop=%.8f tp1=%.8f tp2=%.8f",
                TRADING_PAIR,
                signal.quantity,
                signal.price or 0.0,
                signal.stop_loss or 0.0,
                signal.take_profit_1 or 0.0,
                signal.take_profit_2 or 0.0,
            )
            self.engine.on_buy_filled(signal, datetime.now(timezone.utc))
            notify_execution(
                RUN_MODE,
                "BUY",
                TRADING_PAIR,
                signal.quantity,
                signal.price or 0.0,
                stop_loss=signal.stop_loss or 0.0,
                take_profit_1=signal.take_profit_1 or 0.0,
                take_profit_2=signal.take_profit_2 or 0.0,
            )
            return

        if RUN_MODE == "simulated_trade":
            logger.warning(
                "[SIMULATED] BUY %s qty=%.8f price=%.8f",
                TRADING_PAIR,
                signal.quantity,
                signal.price or 0.0,
            )
            self.engine.on_buy_filled(signal, datetime.now(timezone.utc))
            notify_execution(
                RUN_MODE,
                "BUY",
                TRADING_PAIR,
                signal.quantity,
                signal.price or 0.0,
            )
            return

        if RUN_MODE == "live_trade":
            async with self.order_lock:
                try:
                    result = await asyncio.to_thread(
                        self.okx.safe_place_then_verify_market_buy,
                        TRADING_PAIR,
                        Decimal(str(signal.quantity)),
                        OKX_ORDER_EXP_WINDOW_MS,
                        "robo-bot",
                    )
                    logger.warning(
                        "[LIVE] BUY SUCCESS %s qty=%.8f price=%.8f result=%s",
                        TRADING_PAIR,
                        signal.quantity,
                        signal.price or 0.0,
                        result,
                    )
                    self.engine.on_buy_filled(signal, datetime.now(timezone.utc))
                    notify_execution(
                        RUN_MODE,
                        "BUY",
                        TRADING_PAIR,
                        signal.quantity,
                        signal.price or 0.0,
                        result=str(result),
                    )
                    return
                except (OKXApiError, OKXSafetyError) as exc:
                    logger.error("[LIVE] BUY FAILED %s", exc)
                    notify_error("实盘买入", str(exc), pair=TRADING_PAIR)
                    return
                except Exception as exc:
                    logger.exception("[LIVE] BUY UNKNOWN ERROR %s", exc)
                    notify_error("实盘买入", str(exc), pair=TRADING_PAIR)
                    return

        logger.error("未知 RUN_MODE=%s", RUN_MODE)

    async def execute_sell(self, signal):
        sell_price = signal.price or self.market.last_price
        if sell_price is None:
            logger.error("卖出失败：没有可用价格")
            return

        if RUN_MODE == "monitor":
            logger.warning(
                "[MONITOR] SELL %s qty=%.8f price=%.8f reason=%s",
                TRADING_PAIR,
                signal.quantity,
                sell_price,
                signal.reason,
            )
            self.engine.on_sell_filled(
                sell_price=sell_price,
                sell_qty=signal.quantity,
                reason=signal.reason,
                fill_time=datetime.now(timezone.utc),
            )
            notify_execution(
                RUN_MODE,
                "SELL",
                TRADING_PAIR,
                signal.quantity,
                sell_price,
                reason=signal.reason,
            )
            return

        if RUN_MODE == "simulated_trade":
            logger.warning(
                "[SIMULATED] SELL %s qty=%.8f price=%.8f reason=%s",
                TRADING_PAIR,
                signal.quantity,
                sell_price,
                signal.reason,
            )
            self.engine.on_sell_filled(
                sell_price=sell_price,
                sell_qty=signal.quantity,
                reason=signal.reason,
                fill_time=datetime.now(timezone.utc),
            )
            notify_execution(
                RUN_MODE,
                "SELL",
                TRADING_PAIR,
                signal.quantity,
                sell_price,
                reason=signal.reason,
            )
            return

        if RUN_MODE == "live_trade":
            async with self.order_lock:
                try:
                    result = await asyncio.to_thread(
                        self.okx.place_spot_market_sell_by_base_size,
                        TRADING_PAIR,
                        Decimal(str(signal.quantity)),
                        OKX_ORDER_EXP_WINDOW_MS,
                        "robo-bot",
                    )
                    logger.warning(
                        "[LIVE] SELL SUCCESS %s qty=%.8f price=%.8f reason=%s result=%s",
                        TRADING_PAIR,
                        signal.quantity,
                        sell_price,
                        signal.reason,
                        result,
                    )
                    self.engine.on_sell_filled(
                        sell_price=sell_price,
                        sell_qty=signal.quantity,
                        reason=signal.reason,
                        fill_time=datetime.now(timezone.utc),
                    )
                    notify_execution(
                        RUN_MODE,
                        "SELL",
                        TRADING_PAIR,
                        signal.quantity,
                        sell_price,
                        reason=signal.reason,
                        result=str(result),
                    )
                    return
                except (OKXApiError, OKXSafetyError) as exc:
                    logger.error("[LIVE] SELL FAILED %s", exc)
                    notify_error("实盘卖出", str(exc), pair=TRADING_PAIR)
                    return
                except Exception as exc:
                    logger.exception("[LIVE] SELL UNKNOWN ERROR %s", exc)
                    notify_error("实盘卖出", str(exc), pair=TRADING_PAIR)
                    return

        logger.error("未知 RUN_MODE=%s", RUN_MODE)

    async def consume_public_ws(self):
        sub_msg = {
            "op": "subscribe",
            "args": [
                {
                    "channel": "trades",
                    "instId": TRADING_PAIR,
                }
            ],
        }

        while True:
            try:
                logger.info("连接 OKX Public WS: %s", OKX_PUBLIC_WS)
                notify_ws_connecting(OKX_PUBLIC_WS)

                async with websockets.connect(
                    OKX_PUBLIC_WS,
                    ping_interval=WEBSOCKET_PING_INTERVAL,
                    ping_timeout=WEBSOCKET_PING_INTERVAL,
                    close_timeout=10,
                    max_size=2**20,
                ) as ws:
                    await ws.send(json.dumps(sub_msg))
                    logger.info("已订阅 %s trades", TRADING_PAIR)
                    notify_subscription(TRADING_PAIR, "trades")

                    async for raw in ws:
                        now_utc = datetime.now(timezone.utc)

                        if self.market.last_ts is not None:
                            idle_seconds = (now_utc - self.market.last_ts).total_seconds()
                            if idle_seconds > MARKET_DATA_TIMEOUT_SECONDS:
                                logger.warning(
                                    "行情数据超时: idle_seconds=%.1f pair=%s",
                                    idle_seconds,
                                    TRADING_PAIR,
                                )
                                notify_market_data_timeout(
                                    pair=TRADING_PAIR,
                                    timeout_seconds=MARKET_DATA_TIMEOUT_SECONDS,
                                    last_trade_time=self.last_trade_time_str(),
                                )
                                await ws.close()
                                break

                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            logger.warning("收到非JSON消息: %s", raw)
                            continue

                        if "event" in msg:
                            logger.info("WS event: %s", msg)
                            continue

                        if "data" not in msg:
                            continue

                        for item in msg["data"]:
                            try:
                                price = float(item["px"])
                                size = float(item.get("sz", 0.0))
                                ts_ms = int(item["ts"])
                                ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
                            except (KeyError, ValueError, TypeError) as exc:
                                logger.warning("解析 trade 数据失败: %s | item=%s", exc, item)
                                continue

                            events = self.market.on_trade(ts, price, size)
                            logger.info(
                                "TRADE pair=%s time=%s price=%.8f size=%.8f",
                                TRADING_PAIR,
                                ts.isoformat(),
                                price,
                                size,
                            )

                            if "candle_1m_closed" in events:
                                last_1m = self.market.candles_1m[-1]
                                logger.info(
                                    "1m CLOSED ts=%s o=%.8f h=%.8f l=%.8f c=%.8f v=%.8f",
                                    last_1m.ts.isoformat(),
                                    last_1m.open,
                                    last_1m.high,
                                    last_1m.low,
                                    last_1m.close,
                                    last_1m.volume,
                                )
                                await self.handle_closed_1m_candle()

                            if "candle_5m_closed" in events:
                                last_5m = self.market.candles_5m[-1]
                                logger.info(
                                    "5m CLOSED ts=%s o=%.8f h=%.8f l=%.8f c=%.8f v=%.8f",
                                    last_5m.ts.isoformat(),
                                    last_5m.open,
                                    last_5m.high,
                                    last_5m.low,
                                    last_5m.close,
                                    last_5m.volume,
                                )

            except Exception as exc:
                logger.exception("WebSocket 连接异常: %s", exc)
                notify_error(
                    "OKX WebSocket",
                    str(exc),
                    action=f"将在 {WEBSOCKET_RECONNECT_INTERVAL} 秒后自动重连",
                    pair=TRADING_PAIR,
                    category="ws_reconnect",
                )
                logger.info("将在 %s 秒后重连...", WEBSOCKET_RECONNECT_INTERVAL)
                await asyncio.sleep(WEBSOCKET_RECONNECT_INTERVAL)

    async def run(self):
        validate_startup_config()
        logger.info("Bot started | pair=%s | mode=%s | equity=%.2f", TRADING_PAIR, RUN_MODE, self.equity)
        notify_startup(TRADING_PAIR, RUN_MODE, self.equity)
        await self.startup_check()
        heartbeat_task = asyncio.create_task(self.heartbeat_loop())
        try:
            await self.consume_public_ws()
        finally:
            heartbeat_task.cancel()


async def main():
    bot = TradingBot()
    await bot.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
