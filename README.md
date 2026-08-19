# crypto-trading-bot

一个基于 Python 的 OKX 现货交易机器人项目，支持实时行情监听、K 线构建、策略信号评估，以及 Telegram 通知。

## 项目简介

该项目主要用于：

- 连接 OKX 公共 WebSocket 行情流
- 从逐笔成交数据构建 1 分钟与 5 分钟 K 线
- 根据策略生成买入/卖出信号
- 在 `monitor`、`simulated_trade`、`live_trade` 三种模式下运行
- 通过 Telegram 推送启动、信号、错误等关键信息
- 将运行状态保存到本地 JSON 文件，便于恢复和排查

> 建议先以 **观察模式（`RUN_MODE=monitor`）** 运行，确认策略、通知和交易所连接全部稳定后，再考虑切换到真实下单模式。

---

## 功能特性

- OKX 公共 WebSocket 实时成交订阅
- 自动构建 1m / 5m K 线
- 策略引擎：入场 / 出场评估
- 监控模式、模拟交易模式、实盘交易模式
- OKX 启动自检与安全校验
- Telegram 启动通知、信号通知、异常通知
- 本地状态持久化（如 `state.json`）
- 日志输出到控制台和日志文件

---

## 目录说明

- `app.py` — 主程序入口，负责 WebSocket 消费、K 线处理、信号执行
- `config.py` — 配置加载与环境变量读取
- `exchange_api.py` — OKX API 封装与安全检查
- `strategy.py` — 策略逻辑与信号计算
- `telegram_notifier.py` — Telegram 通知发送模块
- `state.json` / `bot_state.json` — 运行状态文件（不应提交到 GitHub）
- `logs/` — 日志目录（不应提交到 GitHub）

---

## 环境要求

- Python 3.10+
- Linux / Ubuntu 服务器（推荐）
- OKX 账户与 API Key
- Telegram Bot（如启用通知）

---

## 安装步骤

### 1. 克隆仓库

```bash
git clone https://github.com/hytsze285/crypto-trading-bot.git
cd crypto-trading-bot
```

### 2. 创建虚拟环境

```bash
python -m venv .venv
source .venv/bin/activate
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

---

## 配置说明

项目通过 `.env` 文件读取配置。请在项目根目录新建 `.env` 文件：

```env name=.env.example
RUN_MODE=monitor
TRADING_PAIR=BTC-USDT
USE_SIMULATED_TRADING=false
ENABLE_LIVE_TRADING=false
INITIAL_EQUITY=1000

OKX_API_KEY=your_okx_api_key
OKX_SECRET_KEY=your_okx_secret_key
OKX_PASSPHRASE=your_okx_passphrase

TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_chat_id

TELEGRAM_NOTIFY_STARTUP=true
TELEGRAM_NOTIFY_SIGNALS=true
TELEGRAM_NOTIFY_ERRORS=true
```

### 关键配置项说明

#### 运行模式

- `RUN_MODE=monitor`：只监控，不下单，推荐初始使用
- `RUN_MODE=simulated_trade`：模拟成交，用于策略验证
- `RUN_MODE=live_trade`：真实下单，务必谨慎

#### 交易相关

- `TRADING_PAIR`：交易对，如 `BTC-USDT`
- `USE_SIMULATED_TRADING`：是否连接模拟交易环境
- `ENABLE_LIVE_TRADING`：是否允许真实下单
- `INITIAL_EQUITY`：初始权益，仅用于策略内部估算

#### Telegram 通知相关

- `TELEGRAM_ENABLED`：是否开启 Telegram 通知
- `TELEGRAM_BOT_TOKEN`：Telegram Bot Token
- `TELEGRAM_CHAT_ID`：接收通知的聊天 ID
- `TELEGRAM_NOTIFY_STARTUP`：是否通知启动类消息
- `TELEGRAM_NOTIFY_SIGNALS`：是否通知交易信号
- `TELEGRAM_NOTIFY_ERRORS`：是否通知错误/异常

> **注意：`.env` 不要提交到 GitHub。**

---

## 启动方式

### 前台运行

```bash
python app.py
```

### 后台运行（systemd 示例）

如果你希望服务在服务器上常驻运行，建议使用 `systemd` 管理。

示例：

```ini name=/etc/systemd/system/crypto-trading-bot.service
[Unit]
Description=Crypto Trading Bot
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/crypto-trading-bot
EnvironmentFile=/home/ubuntu/crypto-trading-bot/.env
ExecStart=/home/ubuntu/crypto-trading-bot/.venv/bin/python /home/ubuntu/crypto-trading-bot/app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

启用与启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable crypto-trading-bot
sudo systemctl start crypto-trading-bot
```

查看状态与日志：

```bash
sudo systemctl status crypto-trading-bot --no-pager -l
journalctl -u crypto-trading-bot -f
```

---

## Telegram 通知说明

当前已支持以下通知类型：

- Bot 启动通知
- OKX 启动自检通过 / 失败通知
- WebSocket 连接与订阅通知
- ENTRY / EXIT 策略信号通知
- BUY / SELL 执行结果通知
- 异常与错误通知

建议在生产环境中增加防刷屏策略，避免因重复错误或频繁重连导致消息过多。

---

## 安全建议

1. **先使用 `RUN_MODE=monitor` 观察运行情况**
2. 确认 Telegram、日志、信号、交易所连接都稳定后，再考虑切换模式
3. 如��将 `.env` 提交到 GitHub，请立即轮换：
   - OKX API Key / Secret / Passphrase
   - Telegram Bot Token
4. 将以下文件加入 `.gitignore`：

```gitignore name=.gitignore
.env
bot.out
bot_state.json
state.json
logs/*.log
__pycache__/
*.pyc
```

5. 如果使用真实下单，请：
   - 限制 API Key 权限，只保留必要权限
   - 配置 IP 白名单
   - 小资金验证后再逐步扩大

---

## 风险提示

本项目仅用于学习、研究和自动化交易实验。加密货币交易存在较高风险，包括但不限于：

- 市场剧烈波动
- API 异常
- 网络中断
- 策略失效
- 程序逻辑错误

请务必自行承担使用本项目带来的全部风险，不要在未充分验证前直接使用大额资金进行真实交易。

---

## 后续建议

你可以在此基础上继续完善：

- 增加 `.env.example`
- 增加单元测试
- 增加通知防刷屏机制
- 增加持仓与收益统计
- 增加更完整的部署文档
- 增加回测与参数优化工具

---

## License

如需开源发布，请补充你希望使用的许可证，例如 MIT、Apache-2.0 等。
