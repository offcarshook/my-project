# 📈 SMA Crossover Paper Trading Bot

A basic stock/ETF trading bot with a Streamlit dashboard. It compares a short moving
average with a long one, **buys when the short crosses above the long** and **sells when it
crosses back below**. Paper money only, so nothing real is at risk.

- **Markets:** ETFs (QQQ, SPY, VOO) and Nasdaq stocks (AAPL, MSFT, NVDA, AMZN, TSLA) by default, editable from the dashboard
- **Data:** [Alpaca Market Data API](https://alpaca.markets) (free IEX feed). Without keys it runs on random demo prices
- **Strategy:** SMA 20/50 by default, with a 50/200 "golden cross" preset or custom windows
- **Paper account:** $10k virtual cash, long only, one position per symbol, budget split evenly across the watchlist, 0.05% simulated slippage, saved in SQLite
- **Market hours:** only trades while the US market is open (Alpaca clock, falls back to 9:30–16:00 ET on weekdays)

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env        # then paste your Alpaca keys in
streamlit run app.py
```

Get free keys: sign up at alpaca.markets → Paper Trading → API Keys.

Flip **▶️ Bot running** in the sidebar. The bot checks for crossovers on every interval and
logs each paper trade.

## Backtest

Open the **🧪 Backtest** tab, pick how many years (1–8) and hit **Run backtest**. It replays the
same rules on daily bars for your watchlist and shows:

- total return, yearly return (CAGR), worst drop (max drawdown) and win rate
- a comparison against simply **buying and holding QQQ**
- your selected SMA settings next to the 20/50 and 50/200 presets
- realized P&L per symbol and the full trade list

Daily bars are used so years of history download fast. The live bot defaults to 15-minute
bars; set `TIMEFRAME=1Day` in `.env` if you want it to trade exactly what you backtested.

## Layout

| File | What it does |
| --- | --- |
| `app.py` | Streamlit dashboard |
| `bot/data.py` | Alpaca bars + market clock, demo feed |
| `bot/strategy.py` | SMA calculation and crossover signals |
| `bot/paper.py` | SQLite paper account (cash, positions, trades) |
| `bot/engine.py` | One bot tick: fetch → signal → trade |
| `bot/backtest.py` | Historical replay, buy-and-hold benchmark, metrics |
| `tests/` | `python -m pytest` |

> This is an educational toy, not financial advice. SMA crossovers lag the price and get
> chopped up in sideways markets.
