# Trading Bot — Project Prompt

## Original idea
Compare an asset's recent average price with its longer average price.
When the shorter average crosses above the longer one, the bot can buy. When it crosses back below, it can sell.

Build a basic trading bot with a dashboard, fed with live data, using paper trades only.

## Markets
- **Crypto:** Bitcoin (BTC) — live data from the CoinGecko API
- **ETFs:** QQQ (Nasdaq-100), SPY (S&P 500), VOO
- **Nasdaq stocks:** AAPL, MSFT, NVDA, AMZN, TSLA (watchlist editable from the dashboard)
- Stock/ETF data from the Alpaca Market Data API (free tier); CoinGecko only covers crypto

## Strategy
- SMA crossover: **20-period short / 50-period long** (configurable; 50/200 "golden cross" preset)
- Buy when short SMA crosses above long SMA, sell when it crosses back below
- Long only, one position per symbol, no leverage
- Stocks/ETFs trade only during US market hours (9:30–16:00 ET); crypto runs 24/7

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
- API keys (`COINGECKO_API_KEY`, `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`) via `.env`, never committed
