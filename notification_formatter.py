from datetime import datetime


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _translate_check_item(label: str, value: str) -> str:
    value = str(value or "")
    lower = value.lower()

    if "time_ok" in lower:
        return f"{label}: 正常（{value}）"
    if "instrument_ok" in lower or "state=live" in lower:
        return f"{label}: 可交易（{value}）"
    if "whitelist_ok" in lower:
        return f"{label}: 已通过（{value}）"
    if "credentials_ok" in lower or "code=0" in lower:
        return f"{label}: 正常（{value}）"
    if not value:
        return f"{label}: 未知"
    return f"{label}: {value}"


def format_startup_message(pair: str, run_mode: str, equity: float | None = None) -> str:
    message = (
        "🤖 交易机器人已启动\n\n"
        f"交易对: {pair}\n"
        f"模式: {run_mode}\n"
        f"时间: {_now_str()}"
    )
    if equity is not None:
        message += f"\n初始权益: {equity:.2f}"
    return message


def format_ws_connecting_message(endpoint: str) -> str:
    return (
        "🔌 正在连接 OKX 公共 WebSocket\n\n"
        f"地址: {endpoint}\n"
        f"时间: {_now_str()}"
    )


def format_subscription_message(pair: str, channel: str = "trades") -> str:
    return (
        "📡 行情订阅成功\n\n"
        f"交易对: {pair}\n"
        f"频道: {channel}\n"
        f"时间: {_now_str()}"
    )


def format_startup_check_passed(pair: str, result: dict) -> str:
    time_drift = _translate_check_item("时间同步", result.get("time_drift", "未知"))
    instrument = _translate_check_item("交易状态", result.get("instrument_tradable", "未知"))
    whitelist = _translate_check_item("白名单", result.get("instrument_whitelist", "未知"))
    credentials = _translate_check_item("API 鉴权", result.get("api_credentials", "未知"))

    return (
        "✅ OKX 启动自检通过\n\n"
        f"交易对: {pair}\n"
        f"{time_drift}\n"
        f"{instrument}\n"
        f"{whitelist}\n"
        f"{credentials}"
    )


def format_startup_check_failed(pair: str, error_text: str) -> str:
    return (
        "❌ OKX 启动自检失败\n\n"
        f"交易对: {pair}\n"
        f"原因: {error_text}\n"
        f"时间: {_now_str()}"
    )


def format_signal_message(
    pair: str,
    signal: str,
    reason: str,
    meta: dict | None = None,
    phase: str | None = None,
) -> str:
    meta = meta or {}
    signal = str(signal or "").upper()
    reason = str(reason or "unknown")
    regime = str(meta.get("regime", "unknown"))

    reason_map = {
        "trend_filter_not_passed": "趋势过滤未通过",
        "breakout_confirmed": "突破确认",
        "pullback_entry": "回踩入场",
        "take_profit": "止盈条件触发",
        "stop_loss": "止损条件触发",
        "exit_signal": "离场条件触发",
    }
    reason_text = reason_map.get(reason, reason)

    if signal == "HOLD":
        title = "⏸️ 持续观望"
        if phase == "exit":
            title = "⏸️ ���无离场动作"
        return (
            f"{title}\n\n"
            f"交易对: {pair}\n"
            f"结果: HOLD\n"
            f"原因: {reason_text}\n"
            f"市场状态: {regime}\n"
            f"时间: {_now_str()}"
        )

    title = "📈 开仓信号"
    if phase == "exit" or signal in {"SELL", "EXIT", "CLOSE"}:
        title = "📉 平仓/卖出信号"

    return (
        f"{title}\n\n"
        f"交易对: {pair}\n"
        f"动作: {signal}\n"
        f"原因: {reason_text}\n"
        f"市场状态: {regime}\n"
        f"时间: {_now_str()}"
    )


def format_execution_message(
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
) -> str:
    mode_map = {
        "monitor": "观察模式",
        "simulated_trade": "模拟交易",
        "live_trade": "实盘交易",
    }
    side = side.upper()
    mode_text = mode_map.get(run_mode, run_mode)
    title = "🟢 买入执行"
    if side == "SELL":
        title = "🔵 卖出执行"

    lines = [
        title,
        "",
        f"模式: {mode_text}",
        f"交易对: {pair}",
        f"方向: {side}",
        f"数量: {quantity:.8f}",
        f"价格: {price:.8f}",
    ]
    if reason:
        lines.append(f"原因: {reason}")
    if stop_loss is not None:
        lines.append(f"止损: {stop_loss:.8f}")
    if take_profit_1 is not None:
        lines.append(f"止盈1: {take_profit_1:.8f}")
    if take_profit_2 is not None:
        lines.append(f"止盈2: {take_profit_2:.8f}")
    if result is not None:
        lines.append(f"结果: {result}")
    lines.append(f"时间: {_now_str()}")
    return "\n".join(lines)


def format_error_message(
    module: str,
    error_text: str,
    action: str | None = None,
    pair: str | None = None,
) -> str:
    lines = [
        "⚠️ 运行异常",
        "",
        f"模块: {module}",
    ]
    if pair:
        lines.append(f"交易对: {pair}")
    lines.extend([
        f"原因: {error_text}",
        f"时间: {_now_str()}",
    ])
    if action:
        lines.append(f"处理: {action}")
    return "\n".join(lines)
