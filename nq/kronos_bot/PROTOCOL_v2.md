# Kronos bot upgrade: pre-registration (written before any filter, threshold or sizing result)

Extends PROTOCOL.md (periods, data, costs unchanged). Everything below is fixed before the
first filtered result is computed; deviations, if any, are listed at the end of REPORT_v2.md.

## What has already been seen (honest accounting)
* Unfiltered Kronos-mini last-30-minute skill (hit rate, IC, mean net bp of trading every day by
  the sign of the average forecast) was viewed in progress checks on 2016-11..2020-06, and the
  pre-existing `kronos-skill` stage prints the unfiltered DEV-wide table (2016-11..2021-12).
  No confidence, move-size, EV or sizing rule has been evaluated on any period.
* So VALIDATION (below) is clean with respect to every filter/sizing choice, but not with respect
  to the unfiltered Kronos signal (variant A).

## Splits (CFD15 unless noted)
| name | dates | use |
|---|---|---|
| TRAIN | 2016-11-15 .. 2019-12-31 | every threshold, the EV slope, the volatility reference; choice of the final variant |
| VALIDATION | 2020-01-01 .. 2021-12-31 | reported; pass/fail gate; nothing is re-chosen from it |
| OOS | 2022-01-01 .. 2025-07-31 | frozen rules, run once per variant. New for these rules, but the market period was used by earlier research on other strategies, and Kronos's pretraining may include it |
| RESERVED | 2025-08-01 .. 2026-10-21 | spent ONLY if a variant passes both the VALIDATION and OOS gates (it is the only period after Kronos's release; QQQ15 is regular-session only, a different Kronos input than CFD15, so the CFD overlap 2025-08..2025-10 is the like-for-like check) |

## Signals (identical machinery for both windows)
Windows: `last30` = 15:30 -> 16:00 close; `10:00` = 10:00 -> 15:45 close (decision candles close at
the entry time; Kronos sees only candles up to and including it, 24h CFD history).
* Direction: sign of `k_exp_ret` (mean of 16 sampled paths). Fixed a priori: the mean is the
  point forecast that matches expected P&L. Median, probability-up and signal-to-noise
  (`k_exp_ret / k_disp`) are compared as diagnostics only.
* Confidence filter: trade only if sign(`k_prob_up` - 0.5) agrees with the direction and
  |`k_prob_up` - 0.5| >= c, c in {none, 1/16, 2/16, 3/16, 4/16} (probability resolution is 1/16).
* Move-size filter: |`k_exp_ret`| x price >= m x round-trip cost (1.35 index points),
  m in {0, 0.5, 1, 2}.
* EV filter: on TRAIN fit realized = b x `k_exp_ret` (least squares through the origin; no
  intercept, so drift cannot masquerade as Kronos skill). Trade when |b x `k_exp_ret`| x price >=
  1 x cost, i.e. a move filter with m = 1/b, combined with the variant-B confidence threshold. If
  b <= 0 the EV variant takes no trades (reported as such).

## Selection (TRAIN only)
Fast evaluator: per-session net return = direction x realized log return - cost/price
(0 on no-trade days). Criterion: annualised Sharpe of daily net returns (sqrt 252), subject to
>= 150 trades on TRAIN. Ties: fewer active filters. The full 20-cell grid is reported.
* A = no filter (trade every eligible day). Existing-baseline reference: H3 rule, same dates.
* B = best confidence threshold with m = 0.
* C = best cell with m > 0. C-EV = EV filter.
* S = whichever of B, C, C-EV has the highest TRAIN Sharpe.
* D = S + volatility sizing (below). The final variant is D if D's TRAIN Sharpe >= S's, else S.

## Volatility-aware sizing (fixed a priori, no tuning)
* v_t = mean |realized window return| over the previous 20 sessions (strictly before t).
* size multiplier = clip(v_ref / v_t, 0.25, 1.0), v_ref = median of v_t on TRAIN. Never above 1:
  exposure only falls when volatility rises.
* Diagnostic first (TRAIN): net edge of S by v_t tercile (cut points from TRAIN). Reported
  whatever it shows; the rule above is not changed by it.
* Bot sizing for every variant: risk 0.5% of equity per trade, stop 2 x ATR(14 x 15-minute
  candles), contracts = floor(risk x multiplier / (stop x $2 + $2.70)), capped at 50 contracts and
  at 3x notional/equity (new `max_leverage`; previous results used no cap and are not re-run).
* Guards: 3% daily loss (flatten, stop for the day), 10% total drawdown (flatten, stop for good).
  Edge comparisons are run with guards off so periods stay comparable; the final variant is also
  reported with guards on.

## Nautilus evaluation
One continuous run per variant over 2016-11-15 .. 2025-07-31 with identical dates, costs and
starting cash ($50k), sliced into TRAIN / VALIDATION / OOS from per-trade returns
(pnl / equity before the trade). Costs: MNQ $0.85/side + 1 tick slippage on market and stop fills;
stress at +1 tick per side and at 2x total cost, applied per contract to the same trades.
Reported per period: net return, average monthly return, trades, expectancy (R and $), win rate,
win/loss ratio, profit factor, max drawdown, monthly distribution, by year, by regime (trend =
sign of the prior 50-session return; volatility tercile), share of net P&L from the best 5% of
days, and net P&L without the best 10 days.

## Gates
* VALIDATION (fast evaluator, base costs): expectancy > 0, profit factor > 1, >= 40 trades.
* OOS (Nautilus, base costs, guards off): mean net R per trade > 0 with t >= 1.65; net positive in
  >= 2 of 2022, 2023, 2024, 2025 (Jan-Jul); expectancy still > 0 at +1 tick per side.
* A variant failing a gate is reported as failed; no rule is changed after seeing VALIDATION or OOS.

## 10:00 window
Diagnostic first: forecasts on TRAIN only. If its best TRAIN cell has Sharpe <= 0 or the
unfiltered IC <= 0, it is rejected without computing VALIDATION/OOS forecasts. Otherwise it goes
through the same selection and gates, standalone. A portfolio (E) is evaluated only if both
windows pass their gates; both trade the same instrument, so combined exposure is capped at the
single-strategy limits (overlap 15:30-15:45 counts once against the leverage cap).

## Third market (ES, gold)
Not attempted: no verified futures data, contract specs or cost data are available here.
