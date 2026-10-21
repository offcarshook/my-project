# 📈 SMA Crossover Paper Trading Bot

A stock/ETF trading bot with a Streamlit dashboard. It compares a short moving average with
a long one, **buys when the short crosses above the long** and **sells (or shorts) when it
crosses back below**. Paper money only, so nothing real is at risk.

## Modes

| | 🐢 Safe | 🔥 Aggressive | ⚡ Scalper (default) |
| --- | --- | --- | --- |
| Bars | 15Min | 5Min | 1Min |
| SMAs | 20 / 50 | 9 / 21 | 5 / 20 |
| Leverage cap | 1x | 4x | 4x |
| Risk per trade | split evenly | 1% | 1% |
| Stop / take profit | none | 0.5% / 1% | 0.4% / 0.8% |
| Shorting | no | yes | yes |
| Max positions | no limit | 3 | 2 |
| Flat by 4pm | no | yes | yes |
| Checks every | 60s | 30s | 15s |
| Prop challenge rules | off | on | on |

Every setting can be changed in the sidebar, but risk per trade is capped at **1%**. With **risk per trade** set, position size is
`risk % ÷ stop %` of the account (2% risk with a 1% stop = a position worth 2x the account),
capped by leverage.

The faster the bot trades, the more trading costs matter: set **Trading cost per side %** to
what your broker or prop firm really charges (spread + commission) before trusting a backtest.

## Prop challenge rules

Default: **+8% profit target, -5% max daily loss, -10% max total loss, 30 days**. All editable.

- Hitting the target passes the challenge and closes everything.
- Breaking either loss limit fails it, closes everything, and stops the bot until you reset.
- Daily loss is measured from the previous close, so overnight gaps count.
- New positions are sized down so a single stop-out can't break a limit on its own.
- Rules are checked on bar closes. A real firm checks every tick, so they're a bit stricter.

- **Markets:** ETFs (QQQ, SPY, VOO) and Nasdaq stocks (AAPL, MSFT, NVDA, AMZN, TSLA) by default, editable from the dashboard
- **Data:** [Alpaca Market Data API](https://alpaca.markets) (free IEX feed). Without keys it runs on random demo prices
- **Paper account:** $10k virtual cash, one position per symbol, 0.05% simulated slippage, saved in SQLite
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

Open the **🧪 Backtest** tab, pick how many months and hit **Run backtest**. It replays the
exact sidebar settings (mode, bar size, leverage, stops…) on past prices and shows:

- **Prop challenge simulator:** starts a fresh challenge on every trading day (every 5th day
  for long periods) and reports the pass rate, blown accounts, timeouts and average days to pass
- total return, average month, worst drop, win rate and a monthly returns chart
- a comparison against simply **buying and holding QQQ**
- realized P&L per symbol and the full trade list

Intraday backtests go back up to 24 months, daily ones up to 8 years.

## Layout

| File | What it does |
| --- | --- |
| `app.py` | Streamlit dashboard |
| `bot/data.py` | Alpaca bars + market clock, demo feed |
| `bot/strategy.py` | SMA calculation and crossover signals |
| `bot/book.py` | Positions, leverage, stops, shorting, challenge rules (shared by live + backtest) |
| `bot/paper.py` | Saves the live book to SQLite between ticks |
| `bot/engine.py` | One bot tick: fetch → signal → trade |
| `bot/backtest.py` | Historical replay, challenge simulator, buy-and-hold benchmark |
| `tests/` | `python -m pytest` |

> This is an educational toy, not financial advice. SMA crossovers lag the price and get
> chopped up in sideways markets, and leverage makes both of those hurt more.
