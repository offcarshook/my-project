# Trading Bot — Project Prompt

## Original idea
Compare an asset's recent average price with its longer average price.
When the shorter average crosses above the longer one, the bot can buy. When it crosses back below, it can sell.

Build a basic trading bot with a dashboard, fed with live data, using paper trades only.

## Markets
- **ETFs:** QQQ (Nasdaq-100), SPY (S&P 500), VOO
- **Nasdaq stocks:** AAPL, MSFT, NVDA, AMZN, TSLA (watchlist editable from the dashboard)
- Stock/ETF data from the Alpaca Market Data API (free tier)
- Crypto dropped from v1

## Strategy
- SMA crossover: **20-period short / 50-period long** (configurable; 50/200 "golden cross" preset)
- Buy when short SMA crosses above long SMA, sell when it crosses back below
- Long only, one position per symbol, no leverage
- Trades only during US market hours (9:30–16:00 ET)

## Paper trading
- $10,000 virtual starting balance
- Simulated fills at the latest price with a small fee/slippage
- Track positions, cash, P&L, and full trade history; persist to SQLite

## Dashboard
- Python + **Streamlit**
- Price chart per symbol with both SMAs and buy/sell markers
- Portfolio value, open positions, P&L, trade log
- Symbol picker, SMA settings, start/stop bot, reset paper account

## Config
- API keys (`ALPACA_API_KEY`, `ALPACA_SECRET_KEY`) via `.env`, never committed

## v2: aggressive mode for prop firm challenges
- Goal: pass a prop challenge (+8% in 30 days, max -5% per day, -10% total), paper only
- Modes: Safe / Aggressive / Degen with leverage, risk-per-trade sizing, stop loss,
  take profit, shorting, max positions and flatten-before-close
- Backtest runs a challenge simulator over rolling 30-day windows and reports the pass rate

## v3: lower risk, higher frequency
- Max 1% risk per trade, 5% daily loss limit, 10% max drawdown
- Trade more often instead of bigger: new ⚡ Scalper mode on 1-minute bars (default)
- Degen mode removed; Aggressive lowered to 1% risk
