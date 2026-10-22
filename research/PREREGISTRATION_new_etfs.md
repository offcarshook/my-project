# Pre-registration: do breakouts work on volatile ETFs? (written before seeing any new data)

## Why
On QQQ, SPY, XLK, IWM and SOXL, both breakout strategies were flat to negative on Apr 2025 to
Oct 2026, but SOXL was positive in both (ORB +0.09R, noise +0.11R per trade) and SPY negative in
both (-0.05R, -0.08R). That pattern was found by looking at that period, so it can't be tested on
those ETFs. It can be tested on ETFs never used in this research.

## Hypothesis
Intraday breakout momentum survives costs on volatile, less efficient ETFs and not on calm,
heavily traded ones.

## Test (no changes allowed after data arrives)
* **Strategies, frozen as committed:** ORB baseline (15m range, range stop, hold to close), ORB
  A2 + F1 (`research/frozen_orb.json`), noise-area N1 (`research/frozen_noise.json`).
* **Data:** whatever 5-minute history the new ETFs have (expected Mar 2022 to Oct 2026), same
  periods as before: train Mar 2022 to Mar 2024, validation Apr 2024 to Mar 2025, late Apr 2025 to
  Oct 2026.
* **Volatile vs calm, by rule, not by name:** an ETF is *volatile* if its median daily range
  (high-low as % of close) over the train period is at least 1.5x SPY's over the same days;
  otherwise *calm*.
* **Costs:** same slippage (1 bp) and fees as before. Assumed spreads: leveraged/inverse ETFs
  3 bps, sector/thematic ETFs 2 bps, broad-index ETFs 1 bp. Each ETF's correlation group and
  leverage come from what it tracks (e.g. TQQQ = nasdaq, 3x; SOXS = nasdaq, -3x).
* **Watchlist run:** the new ETFs only, same risk settings (0.25% research risk, max 3
  positions, 2 per group), each group (volatile / calm) run separately.

## Success criteria (all must hold for the volatile group, per strategy)
1. Net expectancy after costs > 0 over the full span, with t-stat >= 2.
2. Net expectancy > 0 in at least 2 of the 3 periods, including Apr 2025 to Oct 2026.
3. Volatile-group expectancy > calm-group expectancy.
4. Still > 0 with costs x1.5.

If a strategy passes: build a volatile-ETF watchlist with drawdown limits (daily loss stop in R,
one position per correlation group) and estimate challenge pass rates against the luck baseline.
If none passes: stop pursuing intraday breakouts on ETFs and report that.

Every strategy x group combination tested will be reported, passing or not.
