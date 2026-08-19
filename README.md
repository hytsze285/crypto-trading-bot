# crypto-trading-bot

A Python-based crypto trading bot for OKX spot market monitoring and trading experiments.

## Features

- Connects to OKX public WebSocket trade stream
- Builds 1-minute and 5-minute candles from live trades
- Runs strategy-based entry and exit evaluation
- Supports `monitor`, `simulated_trade`, and `live_trade` modes
- Includes Telegram notifications for startup, signals, and errors
- Persists bot state to local JSON files

## Configuration

Create a `.env` file with your local configuration values. Do **not** commit `.env` to GitHub.

Typical settings include:

- `RUN_MODE`
- `TRADING_PAIR`
- `USE_SIMULATED_TRADING`
- `ENABLE_LIVE_TRADING`
- `OKX_API_KEY`
- `OKX_SECRET_KEY`
- `OKX_PASSPHRASE`
- `TELEGRAM_ENABLED`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## Running locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

## Safety notes

- Start with `RUN_MODE=monitor`
- Keep `ENABLE_LIVE_TRADING=false` until fully validated
- Rotate any secrets if they were ever committed to Git history
- Add `.env`, logs, and runtime state files to `.gitignore`

## Files

- `app.py` — main bot runtime
- `config.py` — environment-based configuration
- `exchange_api.py` — OKX API wrapper and safety checks
- `strategy.py` — trading strategy logic
- `telegram_notifier.py` — Telegram message sender

## License

Add your preferred license information here.
