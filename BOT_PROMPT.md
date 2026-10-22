# Prop Pass: project brief

Build a simple, efficient, moderately aggressive intraday bot to practice passing simulated
prop firm challenges, paper trading only.

- Challenge: +8% target, 5% max daily drawdown, 10% max overall drawdown (configurable)
- About 4-15 completed trades per day across QQQ, SPY, IWM, DIA, XLK, SMH, TQQQ, SQQQ, SOXL, VOO
- Strategy: 9/21 EMA crossover on completed 5-minute candles, longs and shorts, with light
  optional filters (higher-timeframe trend, ATR band, liquidity/spread, chop)
- Risk: 0.25% per trade (0.10-0.50% range), ATR stops, 1.5-2R targets, 2-4 positions,
  correlation and exposure limits, max 20 trades a day, flat before the close
- A risk manager independent of the strategy that enforces the firm's drawdown rules
- Realistic backtest with costs, train/validation/untouched test, challenge simulation and
  Monte Carlo
- Alpaca paper trading with logging, reconciliation, duplicate-order protection and an
  emergency stop

See README.md for how it was built and how to run it.
