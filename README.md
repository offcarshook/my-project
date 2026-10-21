# 📈 Prop Challenge Paper Trading Bot

A stock/ETF day-trading bot with a Streamlit dashboard, built to practice passing a prop firm
challenge with paper money. Nothing real is at risk.

## Strategies

### 🎯 VWAP Pullback (default)

Trades with the intraday trend, after a pullback, on 5-minute candles:

1. **Trend:** price above the session VWAP, 9 EMA above 21 EMA, 21 EMA rising (shorts: the mirror image)
2. **Pullback:** in the last 3 bars, price dipped back to the 9 EMA
3. **Trigger:** a green candle that closes above the previous candle's high
4. **Filters:** volume at least 1.2x its 20-bar average, only between 9:45 and 15:30 New York time
5. **Stop:** just past the pullback low/high, kept between 0.5 and 2 ATR
6. **Target:** 2x the risk; everything is closed before 4pm

### ⚡ SMA Scalper / 🐢 Safe

The original moving average crossover: buy when the short SMA crosses above the long one,
sell (or short) when it crosses back below.

## Modes

| | 🎯 VWAP Pullback | ⚡ SMA Scalper | 🐢 Safe |
| --- | --- | --- | --- |
| Bars | 5Min | 1Min | 15Min |
| Risk per trade | 0.5% | 0.5% | split evenly |
| Stop | from the strategy (0.5–2 ATR) | 0.4% | none |
| Target | 2x risk | 2x risk | none |
| Max trades per day | 10 | 10 | no limit |
| Max open positions | 3 | 2 | no limit |
| Shorting | yes | yes | no |
| Flat by 4pm | yes | yes | no |
| Prop challenge rules | on | on | off |

Every setting can be changed in the sidebar; risk per trade is capped at **1%**. Position
size is `risk ÷ stop distance`, capped by the leverage limit (4x by default). The **min volume**
slider controls how picky the VWAP strategy is: raise it for fewer trades.

**Break-even math:** with a 2x target you need to win more than 1 in 3 trades just to break
even before fees. The backtest shows your win rate next to that line. Set **Trading cost per
side %** to what your broker or prop firm really charges (spread + commission).

## Prop challenge rules

Default: **+8% profit target, -5% max daily loss, -10% max total loss, 30 days**. All editable.

- Hitting the target passes the challenge and closes everything.
- Breaking either loss limit fails it, closes everything, and stops the bot until you reset.
- Daily loss is measured from the previous close, so overnight gaps count.
- New positions are sized down so a single stop-out can't break a limit on its own.
- Rules are checked on bar closes. A real firm checks every tick, so they're a bit stricter.
- New trades stop after the daily trade limit, and only finished candles can trigger a trade.

- **Markets:** ETFs (QQQ, SPY, VOO) and Nasdaq stocks (AAPL, MSFT, NVDA, AMZN, TSLA) by default, editable from the dashboard
- **Data:** [Alpaca Market Data API](https://alpaca.markets) (free IEX feed). Without keys it runs on random demo prices
- **Paper account:** $10k virtual cash, one position per symbol, 0.05% simulated slippage, saved in SQLite
- **Market hours:** only trades while the US market is open (Alpaca clock, falls back to 9:30–16:00 ET on weekdays)
- **Data:** full candles (open, high, low, close, volume), regular trading hours only for intraday bars

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
- total return, average month, trades per day, worst drop, win rate vs break-even and a monthly returns chart
- a comparison against simply **buying and holding QQQ**
- realized P&L per symbol and the full trade list

Intraday backtests go back up to 24 months, daily ones up to 8 years.

## Real-data check (no keys needed)

```bash
pip install arch
python scripts/real_data_sim.py
```

Runs the backtest and challenge simulator on real daily NASDAQ Composite and S&P 500 candles
(1999–2018) bundled with the `arch` package. Results at 0.05% cost per side:

| | NASDAQ | S&P 500 |
| --- | --- | --- |
| Buy & hold | +65.6% (worst drop -58.8%) | +77.6% (worst drop -55.7%) |
| SMA 20/50, longs only | +73.3% (-41.1%) | +30.6% (-29.5%) |
| SMA 50/200, longs only | **+184.4% (-22.6%)** | **+175.0% (-20.1%)** |

On daily candles every 30-day prop challenge ran out of time (0% pass, ~0% fail): daily
crossovers trade far too rarely to make +8% in a month. Passing needs an intraday strategy,
which needs Alpaca 5-minute data.

## Layout

| File | What it does |
| --- | --- |
| `app.py` | Streamlit dashboard |
| `bot/data.py` | Alpaca bars + market clock, demo feed |
| `bot/strategy.py` | VWAP pullback and SMA crossover strategies |
| `bot/book.py` | Positions, leverage, stops, shorting, challenge rules (shared by live + backtest) |
| `bot/paper.py` | Saves the live book to SQLite between ticks |
| `bot/engine.py` | One bot tick: fetch → signal → trade |
| `bot/backtest.py` | Historical replay, challenge simulator, buy-and-hold benchmark |
| `scripts/real_data_sim.py` | Simulation on real daily index history |
| `tests/` | `python -m pytest` |

> This is an educational toy, not financial advice. SMA crossovers lag the price and get
> chopped up in sideways markets, and leverage makes both of those hurt more.
