import asyncio
import json
import logging
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Deque, List, Optional

import websockets

from config import (
    ALLOWED_INST_ID,
    ENABLE_LIVE_TRADING,
    INITIAL_EQUITY,
    LOG_FILE,
    LOG_LEVEL,
    MARKET_DATA_BUFFER_SIZE,
    OKX_HTTP_TIMEOUT,
    OKX_ORDER_EXP_WINDOW_MS,
    OKX_PUBLIC_WS,
    RUN_MODE,
    STATE_FILE,
    TRADING_PAIR,
    USE_SIMULATED_TRADING,
    WEBSOCKET_PING_INTERVAL,
    WEBSOCKET_RECONNECT_INTERVAL,
    TREND_FAST_MA,
    TREND_SLOW_MA,
    BREAKOUT_LOOKBACK,
    VOLUME_LOOKBACK,
    VOLUME_MULTIPLIER,
    STOP_LOSS_PCT,
    TAKE_PROFIT_1_PCT,
    TAKE_PROFIT_2_PCT,
    MAX_HOLDING_MINUTES,
    RISK_PER_TRADE_PCT,
    MAX_POSITION_NOTIONAL_PCT,
    MIN_ORDER_NOTIONAL,
    MAX_DAILY_LOSS_PCT,
    MAX_CONSECUTIVE_LOSSES,
    MAX_TRADES_PER_DAY,
    COOLDOWN_MINUTES,
    TELEGRAM_NOTIFY_ERRORS,
    TELEGRAM_NOTIFY_SIGNALS,
    TELEGRAM_NOTIFY_STARTUP,
)
from exchange_api import OKXApiError, OKXClient, OKXSafetyError
from strategy import Candle, StrategyConfig, StrategyEngine
from telegram_notifier import notify


def setup_logger() -> logging.Logger:
    logger = logging.getLogger("crypto_bot")
    logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
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

    async def startup_check(self):
        try:
            result = await asyncio.to_thread(
                self.okx.startup_self_check,
                TRADING_PAIR,
            )
            logger.info("OKX startup self-check passed: %s", result)
            if TELEGRAM_NOTIFY_STARTUP:
                notify(f"✅ OKX startup self-check passed\n{result}")
        except Exception as exc:
            logger.exception("OKX startup self-check failed: %s", exc)
            if TELEGRAM_NOTIFY_ERRORS:
                notify(f"❌ OKX startup self-check failed\n{exc}")
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
            if TELEGRAM_NOTIFY_SIGNALS:
                notify(
                    f"📈 ENTRY signal\n"
                    f"pair={TRADING_PAIR}\n"
                    f"signal={signal.signal}\n"
                    f"reason={signal.reason}\n"
                    f"meta={signal.meta}"
                )

            if signal.signal == "BUY":
                await self.execute_buy(signal)
        else:
            signal = self.engine.evaluate_exit(
                candles_1m=candles_1m,
                now=now,
            )
            logger.info("EXIT signal=%s reason=%s meta=%s", signal.signal, signal.reason, signal.meta)
            if TELEGRAM_NOTIFY_SIGNALS:
                notify(
                    f"📉 EXIT signal\n"
                    f"pair={TRADING_PAIR}\n"
                    f"signal={signal.signal}\n"
                    f"reason={signal.reason}\n"
                    f"meta={signal.meta}"
                )

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
            if TELEGRAM_NOTIFY_SIGNALS:
                notify(
                    f"🟡 [MONITOR] BUY\n"
                    f"pair={TRADING_PAIR}\n"
                    f"qty={signal.quantity:.8f}\n"
                    f"price={(signal.price or 0.0):.8f}\n"
                    f"stop={(signal.stop_loss or 0.0):.8f}\n"
                    f"tp1={(signal.take_profit_1 or 0.0):.8f}\n"
                    f"tp2={(signal.take_profit_2 or 0.0):.8f}"
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
            if TELEGRAM_NOTIFY_SIGNALS:
                notify(
                    f"🟠 [SIMULATED] BUY\n"
                    f"pair={TRADING_PAIR}\n"
                    f"qty={signal.quantity:.8f}\n"
                    f"price={(signal.price or 0.0):.8f}"
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
                    if TELEGRAM_NOTIFY_SIGNALS:
                        notify(
                            f"🟢 [LIVE] BUY SUCCESS\n"
                            f"pair={TRADING_PAIR}\n"
                            f"qty={signal.quantity:.8f}\n"
                            f"price={(signal.price or 0.0):.8f}\n"
                            f"result={result}"
                        )
                    return
                except (OKXApiError, OKXSafetyError) as exc:
                    logger.error("[LIVE] BUY FAILED %s", exc)
                    if TELEGRAM_NOTIFY_ERRORS:
                        notify(f"🔴 [LIVE] BUY FAILED\npair={TRADING_PAIR}\nerror={exc}")
                    return
                except Exception as exc:
                    logger.exception("[LIVE] BUY UNKNOWN ERROR %s", exc)
                    if TELEGRAM_NOTIFY_ERRORS:
                        notify(f"🔥 [LIVE] BUY UNKNOWN ERROR\npair={TRADING_PAIR}\nerror={exc}")
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
            if TELEGRAM_NOTIFY_SIGNALS:
                notify(
                    f"🟡 [MONITOR] SELL\n"
                    f"pair={TRADING_PAIR}\n"
                    f"qty={signal.quantity:.8f}\n"
                    f"price={sell_price:.8f}\n"
                    f"reason={signal.reason}"
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
            if TELEGRAM_NOTIFY_SIGNALS:
                notify(
                    f"🟠 [SIMULATED] SELL\n"
                    f"pair={TRADING_PAIR}\n"
                    f"qty={signal.quantity:.8f}\n"
                    f"price={sell_price:.8f}\n"
                    f"reason={signal.reason}"
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
                    if TELEGRAM_NOTIFY_SIGNALS:
                        notify(
                            f"🟢 [LIVE] SELL SUCCESS\n"
                            f"pair={TRADING_PAIR}\n"
                            f"qty={signal.quantity:.8f}\n"
                            f"price={sell_price:.8f}\n"
                            f"reason={signal.reason}\n"
                            f"result={result}"
                        )
                    return
                except (OKXApiError, OKXSafetyError) as exc:
                    logger.error("[LIVE] SELL FAILED %s", exc)
                    if TELEGRAM_NOTIFY_ERRORS:
                        notify(f"🔴 [LIVE] SELL FAILED\npair={TRADING_PAIR}\nerror={exc}")
                    return
                except Exception as exc:
                    logger.exception("[LIVE] SELL UNKNOWN ERROR %s", exc)
                    if TELEGRAM_NOTIFY_ERRORS:
                        notify(f"🔥 [LIVE] SELL UNKNOWN ERROR\npair={TRADING_PAIR}\nerror={exc}")
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
                if TELEGRAM_NOTIFY_STARTUP:
                    notify(f"🔌 Connecting OKX Public WS\n{OKX_PUBLIC_WS}")
                async with websockets.connect(
                    OKX_PUBLIC_WS,
                    ping_interval=WEBSOCKET_PING_INTERVAL,
                    ping_timeout=WEBSOCKET_PING_INTERVAL,
                    close_timeout=10,
                    max_size=2**20,
                ) as ws:
                    await ws.send(json.dumps(sub_msg))
                    logger.info("已订阅 %s trades", TRADING_PAIR)
                    if TELEGRAM_NOTIFY_STARTUP:
                        notify(f"📡 已订阅 trades\npair={TRADING_PAIR}")

                    async for raw in ws:
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
                if TELEGRAM_NOTIFY_ERRORS:
                    notify(f"⚠️ WebSocket 连接异常\nerror={exc}\n将在 {WEBSOCKET_RECONNECT_INTERVAL} 秒后重连")
                logger.info("将在 %s 秒后重连...", WEBSOCKET_RECONNECT_INTERVAL)
                await asyncio.sleep(WEBSOCKET_RECONNECT_INTERVAL)

    async def run(self):
        logger.info("Bot started | pair=%s | mode=%s | equity=%.2f", TRADING_PAIR, RUN_MODE, self.equity)
        if TELEGRAM_NOTIFY_STARTUP:
            notify(f"🤖 Bot started\npair={TRADING_PAIR}\nmode={RUN_MODE}\nequity={self.equity:.2f}")
        await self.startup_check()
        await self.consume_public_ws()


async def main():
    bot = TradingBot()
    await bot.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
