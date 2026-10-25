# Pre-registration: ORB on the Nasdaq-100 CFD 15-minute file (written before any performance run)

## Data
`15m_data.csv` (user upload): Nasdaq-100 index CFD, 15-minute candles, MetaTrader export,
2016-11-15 .. 2025-10-01, tick volume only. Server clock = New York + 7 h (the 09:30 New York open
is the 16:30 candle; 15:30 during the March/October weeks when US and EU clocks differ), i.e.
Eastern European time with EU daylight-saving rules: converted from `Europe/Athens` to
`America/New_York`. Regular session only (NYSE calendar: holidays, early closes). A CFD tracks the
cash index, not NQ futures. Only timing/quality audits were run before writing this.

## Periods
* **Holdout, never used in any research here: 2016-11-15 .. 2022-03-20.** Run once.
* Comparison (already examined via the QQQ proxy): 2022-03-21 .. 2025-10-01, reported separately.

## Strategy (fixed now, no changes after seeing results)
The pre-existing ORB baseline variant "ORB 15m range, range stop, hold" (`nq/strategies/orb.py`,
defaults): opening range = 09:30-09:45 New York (one 15-minute candle), entry on the first
15-minute candle closing beyond the range (next fill at that close + 1 tick), at most one long and
one short per day, no entries after 11:30, stop at the other side of the range, no target, flat
at 15:50 (the 15:45 candle close on 15-minute data), 0.5% risk, ranges outside 0.15-2.5% skipped.
The frozen 5-minute ORB cannot run on 15-minute candles, so it is not tested here.

Costs as MNQ futures: $0.85/contract/side commission, 1 tick (0.25) slippage on market and stop
fills; price = CFD price rounded to 0.25. $50,000 start.

## Pass criteria (holdout)
1. Net expectancy > 0 with t-stat >= 2.0.
2. Profit factor > 1.
3. Net positive in at least 4 of the 6 calendar years 2016*-2022* (partial years included).
If it fails, the ORB edge is not confirmed on independent data, and it is reported that way.
