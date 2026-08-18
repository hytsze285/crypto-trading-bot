from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Literal, Dict, Any
from datetime import datetime, timedelta


SignalType = Literal["BUY", "SELL", "HOLD", "BLOCKED"]
ExitReason = Literal[
    "take_profit_1",
    "take_profit_2",
    "stop_loss",
    "trend_invalid",
    "timeout",
    "daily_loss_limit",
    "max_consecutive_losses",
    "max_trades_per_day",
    "cooldown",
    "none",
]


@dataclass
class Candle:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Position:
    has_position: bool = False
    entry_price: Optional[float] = None
    quantity: float = 0.0
    entry_time: Optional[datetime] = None
    stop_loss_price: Optional[float] = None
    take_profit_1_price: Optional[float] = None
    take_profit_2_price: Optional[float] = None
    tp1_hit: bool = False


@dataclass
class TradeRecord:
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    quantity: float
    pnl_amount: float
    pnl_pct: float
    reason: str


@dataclass
class RiskState:
    trading_day: Optional[str] = None
    trades_today: int = 0
    realized_pnl_today: float = 0.0
    consecutive_losses: int = 0
    cooldown_until: Optional[datetime] = None
    trade_history: List[TradeRecord] = field(default_factory=list)

    def reset_if_new_day(self, now: datetime) -> None:
        day_str = now.strftime("%Y-%m-%d")
        if self.trading_day != day_str:
            self.trading_day = day_str
            self.trades_today = 0
            self.realized_pnl_today = 0.0
            self.consecutive_losses = 0
            self.cooldown_until = None


@dataclass
class StrategyConfig:
    trend_fast_ma: int = 20
    trend_slow_ma: int = 60
    breakout_lookback: int = 20
    volume_lookback: int = 20
    volume_multiplier: float = 1.5
    max_breakout_extension_pct: float = 0.008
    stop_loss_pct: float = 0.012
    take_profit_1_pct: float = 0.02
    take_profit_2_pct: float = 0.04
    exit_fast_ma: int = 5
    exit_slow_ma: int = 20
    max_holding_minutes: int = 30
    min_profit_for_timeout_exit: float = 0.01
    risk_per_trade_pct: float = 0.005
    max_position_notional_pct: float = 0.03
    min_order_notional: float = 5.0
    max_daily_loss_pct: float = 0.02
    max_consecutive_losses: int = 3
    max_trades_per_day: int = 5
    cooldown_minutes_after_sell: int = 10
    use_atr_stop: bool = False
    atr_period: int = 14
    atr_stop_multiplier: float = 2.0


@dataclass
class StrategySignal:
    signal: SignalType
    reason: str
    price: Optional[float] = None
    quantity: float = 0.0
    stop_loss: Optional[float] = None
    take_profit_1: Optional[float] = None
    take_profit_2: Optional[float] = None
    meta: Dict[str, Any] = field(default_factory=dict)


def sma(values: List[float], period: int) -> Optional[float]:
    if len(values) < period or period <= 0:
        return None
    return sum(values[-period:]) / period


def highest_high(candles: List[Candle], lookback: int, exclude_current: bool = True) -> Optional[float]:
    needed = lookback + 1 if exclude_current else lookback
    if len(candles) < needed:
        return None

    source = candles[-lookback-1:-1] if exclude_current else candles[-lookback:]
    return max(c.high for c in source)


def average_volume(candles: List[Candle], lookback: int, exclude_current: bool = True) -> Optional[float]:
    needed = lookback + 1 if exclude_current else lookback
    if len(candles) < needed:
        return None

    source = candles[-lookback-1:-1] if exclude_current else candles[-lookback:]
    return sum(c.volume for c in source) / len(source)


def true_range(curr: Candle, prev_close: float) -> float:
    return max(
        curr.high - curr.low,
        abs(curr.high - prev_close),
        abs(curr.low - prev_close),
    )


def atr(candles: List[Candle], period: int) -> Optional[float]:
    if len(candles) < period + 1:
        return None

    trs = []
    for i in range(1, len(candles)):
        trs.append(true_range(candles[i], candles[i - 1].close))

    if len(trs) < period:
        return None

    return sum(trs[-period:]) / period


def calc_unrealized_pnl_pct(entry_price: float, current_price: float) -> float:
    return (current_price - entry_price) / entry_price


def calc_position_size(
    equity: float,
    entry_price: float,
    stop_price: float,
    cfg: StrategyConfig,
) -> float:
    if entry_price <= 0 or stop_price <= 0 or equity <= 0:
        return 0.0

    risk_amount = equity * cfg.risk_per_trade_pct
    per_unit_risk = abs(entry_price - stop_price)

    if per_unit_risk <= 0:
        return 0.0

    qty_by_risk = risk_amount / per_unit_risk
    max_notional = equity * cfg.max_position_notional_pct
    qty_by_notional = max_notional / entry_price

    qty = min(qty_by_risk, qty_by_notional)
    notional = qty * entry_price

    if notional < cfg.min_order_notional:
        return 0.0

    return max(qty, 0.0)


def detect_regime(candles_5m: List[Candle], cfg: StrategyConfig) -> str:
    closes = [c.close for c in candles_5m]
    fast = sma(closes, cfg.trend_fast_ma)
    slow = sma(closes, cfg.trend_slow_ma)

    if fast is None or slow is None:
        return "unknown"

    if fast > slow:
        return "trend_up"
    return "range"


def trend_filter_ok(candles_5m: List[Candle], cfg: StrategyConfig) -> bool:
    return detect_regime(candles_5m, cfg) == "trend_up"


def breakout_entry_ok(candles_1m: List[Candle], cfg: StrategyConfig) -> Dict[str, Any]:
    if len(candles_1m) < max(cfg.breakout_lookback, cfg.volume_lookback) + 1:
        return {"ok": False}

    current = candles_1m[-1]
    breakout_level = highest_high(candles_1m, cfg.breakout_lookback, exclude_current=True)
    avg_vol = average_volume(candles_1m, cfg.volume_lookback, exclude_current=True)

    if breakout_level is None or avg_vol is None or avg_vol <= 0:
        return {"ok": False}

    is_breakout = current.close > breakout_level
    volume_ok = current.volume >= avg_vol * cfg.volume_multiplier
    extension_pct = (current.close - breakout_level) / breakout_level if breakout_level > 0 else None
    not_overextended = extension_pct is not None and extension_pct <= cfg.max_breakout_extension_pct

    return {
        "ok": bool(is_breakout and volume_ok and not_overextended),
        "breakout_level": breakout_level,
        "current_price": current.close,
        "current_volume": current.volume,
        "avg_volume": avg_vol,
        "extension_pct": extension_pct,
        "is_breakout": is_breakout,
        "volume_ok": volume_ok,
        "not_overextended": not_overextended,
    }


def build_stop_loss(entry_price: float, candles_1m: List[Candle], cfg: StrategyConfig) -> float:
    base_stop = entry_price * (1 - cfg.stop_loss_pct)

    if cfg.use_atr_stop:
        a = atr(candles_1m, cfg.atr_period)
        if a is not None and a > 0:
            atr_stop = entry_price - a * cfg.atr_stop_multiplier
            return max(atr_stop, 0.00000001)

    return max(base_stop, 0.00000001)


def entry_signal(
    candles_1m: List[Candle],
    candles_5m: List[Candle],
    position: Position,
    risk_state: RiskState,
    equity: float,
    now: datetime,
    cfg: StrategyConfig,
) -> StrategySignal:
    risk_state.reset_if_new_day(now)

    if position.has_position:
        return StrategySignal(signal="HOLD", reason="already_in_position")

    if risk_state.cooldown_until and now < risk_state.cooldown_until:
        return StrategySignal(
            signal="BLOCKED",
            reason="cooldown",
            meta={"cooldown_until": risk_state.cooldown_until.isoformat()},
        )

    if risk_state.realized_pnl_today <= -(equity * cfg.max_daily_loss_pct):
        return StrategySignal(signal="BLOCKED", reason="daily_loss_limit")

    if risk_state.consecutive_losses >= cfg.max_consecutive_losses:
        return StrategySignal(signal="BLOCKED", reason="max_consecutive_losses")

    if risk_state.trades_today >= cfg.max_trades_per_day:
        return StrategySignal(signal="BLOCKED", reason="max_trades_per_day")

    regime = detect_regime(candles_5m, cfg)
    if regime != "trend_up":
        return StrategySignal(
            signal="HOLD",
            reason="trend_filter_not_passed",
            meta={"regime": regime},
        )

    breakout = breakout_entry_ok(candles_1m, cfg)
    if not breakout.get("ok", False):
        return StrategySignal(
            signal="HOLD",
            reason="breakout_not_passed",
            meta=breakout,
        )

    entry_price = float(breakout["current_price"])
    stop_price = build_stop_loss(entry_price, candles_1m, cfg)
    tp1 = entry_price * (1 + cfg.take_profit_1_pct)
    tp2 = entry_price * (1 + cfg.take_profit_2_pct)

    qty = calc_position_size(
        equity=equity,
        entry_price=entry_price,
        stop_price=stop_price,
        cfg=cfg,
    )

    if qty <= 0:
        return StrategySignal(
            signal="BLOCKED",
            reason="position_too_small_or_invalid",
            price=entry_price,
            stop_loss=stop_price,
            take_profit_1=tp1,
            take_profit_2=tp2,
        )

    return StrategySignal(
        signal="BUY",
        reason="trend_breakout_confirmed",
        price=entry_price,
        quantity=qty,
        stop_loss=stop_price,
        take_profit_1=tp1,
        take_profit_2=tp2,
        meta={
            "regime": regime,
            "breakout_level": breakout.get("breakout_level"),
            "extension_pct": breakout.get("extension_pct"),
            "current_volume": breakout.get("current_volume"),
            "avg_volume": breakout.get("avg_volume"),
        },
    )


def exit_signal(
    candles_1m: List[Candle],
    position: Position,
    now: datetime,
    cfg: StrategyConfig,
) -> StrategySignal:
    if not position.has_position or position.entry_price is None or position.entry_time is None:
        return StrategySignal(signal="HOLD", reason="no_position")

    if not candles_1m:
        return StrategySignal(signal="HOLD", reason="no_market_data")

    current_price = candles_1m[-1].close
    pnl_pct = calc_unrealized_pnl_pct(position.entry_price, current_price)

    if position.stop_loss_price is not None and current_price <= position.stop_loss_price:
        return StrategySignal(
            signal="SELL",
            reason="stop_loss",
            price=current_price,
            quantity=position.quantity,
            meta={"pnl_pct": pnl_pct},
        )

    if (
        not position.tp1_hit
        and position.take_profit_1_price is not None
        and current_price >= position.take_profit_1_price
    ):
        return StrategySignal(
            signal="SELL",
            reason="take_profit_1",
            price=current_price,
            quantity=position.quantity * 0.5,
            meta={"pnl_pct": pnl_pct, "partial": True},
        )

    if position.take_profit_2_price is not None and current_price >= position.take_profit_2_price:
        return StrategySignal(
            signal="SELL",
            reason="take_profit_2",
            price=current_price,
            quantity=position.quantity,
            meta={"pnl_pct": pnl_pct, "partial": False},
        )

    closes = [c.close for c in candles_1m]
    fast = sma(closes, cfg.exit_fast_ma)
    slow = sma(closes, cfg.exit_slow_ma)
    if fast is not None and slow is not None and fast < slow:
        return StrategySignal(
            signal="SELL",
            reason="trend_invalid",
            price=current_price,
            quantity=position.quantity,
            meta={"pnl_pct": pnl_pct, "fast_ma": fast, "slow_ma": slow},
        )

    holding_minutes = (now - position.entry_time).total_seconds() / 60.0
    if holding_minutes >= cfg.max_holding_minutes and pnl_pct < cfg.min_profit_for_timeout_exit:
        return StrategySignal(
            signal="SELL",
            reason="timeout",
            price=current_price,
            quantity=position.quantity,
            meta={"pnl_pct": pnl_pct, "holding_minutes": holding_minutes},
        )

    return StrategySignal(
        signal="HOLD",
        reason="hold_position",
        price=current_price,
        quantity=position.quantity,
        meta={"pnl_pct": pnl_pct},
    )


def apply_buy_fill(
    position: Position,
    signal: StrategySignal,
    fill_time: datetime,
) -> Position:
    if signal.signal != "BUY" or signal.price is None:
        return position

    return Position(
        has_position=True,
        entry_price=signal.price,
        quantity=signal.quantity,
        entry_time=fill_time,
        stop_loss_price=signal.stop_loss,
        take_profit_1_price=signal.take_profit_1,
        take_profit_2_price=signal.take_profit_2,
        tp1_hit=False,
    )


def apply_sell_fill(
    position: Position,
    risk_state: RiskState,
    sell_price: float,
    sell_qty: float,
    sell_time: datetime,
    reason: str,
    cfg: StrategyConfig,
) -> Position:
    if not position.has_position or position.entry_price is None or sell_qty <= 0:
        return position

    sell_qty = min(sell_qty, position.quantity)
    pnl_amount = (sell_price - position.entry_price) * sell_qty
    pnl_pct = (sell_price - position.entry_price) / position.entry_price

    risk_state.trade_history.append(
        TradeRecord(
            entry_time=position.entry_time or sell_time,
            exit_time=sell_time,
            entry_price=position.entry_price,
            exit_price=sell_price,
            quantity=sell_qty,
            pnl_amount=pnl_amount,
            pnl_pct=pnl_pct,
            reason=reason,
        )
    )

    remaining_qty = position.quantity - sell_qty
    fully_closed = remaining_qty <= 1e-12

    risk_state.realized_pnl_today += pnl_amount

    if fully_closed:
        risk_state.trades_today += 1
        if pnl_amount < 0:
            risk_state.consecutive_losses += 1
        else:
            risk_state.consecutive_losses = 0

        risk_state.cooldown_until = sell_time + timedelta(minutes=cfg.cooldown_minutes_after_sell)

        return Position()

    new_position = Position(
        has_position=True,
        entry_price=position.entry_price,
        quantity=remaining_qty,
        entry_time=position.entry_time,
        stop_loss_price=position.stop_loss_price,
        take_profit_1_price=position.take_profit_1_price,
        take_profit_2_price=position.take_profit_2_price,
        tp1_hit=position.tp1_hit or reason == "take_profit_1",
    )

    return new_position


class StrategyEngine:
    def __init__(self, config: Optional[StrategyConfig] = None):
        self.cfg = config or StrategyConfig()
        self.position = Position()
        self.risk_state = RiskState()

    def evaluate_entry(
        self,
        candles_1m: List[Candle],
        candles_5m: List[Candle],
        equity: float,
        now: Optional[datetime] = None,
    ) -> StrategySignal:
        now = now or datetime.utcnow()
        return entry_signal(
            candles_1m=candles_1m,
            candles_5m=candles_5m,
            position=self.position,
            risk_state=self.risk_state,
            equity=equity,
            now=now,
            cfg=self.cfg,
        )

    def evaluate_exit(
        self,
        candles_1m: List[Candle],
        now: Optional[datetime] = None,
    ) -> StrategySignal:
        now = now or datetime.utcnow()
        return exit_signal(
            candles_1m=candles_1m,
            position=self.position,
            now=now,
            cfg=self.cfg,
        )

    def on_buy_filled(self, signal: StrategySignal, fill_time: Optional[datetime] = None) -> None:
        fill_time = fill_time or datetime.utcnow()
        self.position = apply_buy_fill(
            position=self.position,
            signal=signal,
            fill_time=fill_time,
        )

    def on_sell_filled(
        self,
        sell_price: float,
        sell_qty: float,
        reason: str,
        fill_time: Optional[datetime] = None,
    ) -> None:
        fill_time = fill_time or datetime.utcnow()
        self.position = apply_sell_fill(
            position=self.position,
            risk_state=self.risk_state,
            sell_price=sell_price,
            sell_qty=sell_qty,
            sell_time=fill_time,
            reason=reason,
            cfg=self.cfg,
        )

    def snapshot(self) -> Dict[str, Any]:
        return {
            "position": {
                "has_position": self.position.has_position,
                "entry_price": self.position.entry_price,
                "quantity": self.position.quantity,
                "entry_time": self.position.entry_time.isoformat() if self.position.entry_time else None,
                "stop_loss_price": self.position.stop_loss_price,
                "take_profit_1_price": self.position.take_profit_1_price,
                "take_profit_2_price": self.position.take_profit_2_price,
                "tp1_hit": self.position.tp1_hit,
            },
            "risk": {
                "trading_day": self.risk_state.trading_day,
                "trades_today": self.risk_state.trades_today,
                "realized_pnl_today": self.risk_state.realized_pnl_today,
                "consecutive_losses": self.risk_state.consecutive_losses,
                "cooldown_until": self.risk_state.cooldown_until.isoformat()
                if self.risk_state.cooldown_until else None,
                "trade_history_count": len(self.risk_state.trade_history),
            },
            "config": self.cfg.__dict__,
        }
