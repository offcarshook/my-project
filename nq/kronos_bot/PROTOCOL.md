# Kronos bot research protocol (written before any analysis for this mission)

## Market and data
Nasdaq-100, intraday, one instrument.
* `CFD15`: user's Nasdaq-100 CFD 15-minute file (MetaTrader export, server clock EET -> New York),
  2016-11-15 .. 2025-10-01. Index CFD, not futures; tick volume.
* `QQQ15`: IEX-only QQQ 5-minute candles resampled to 15 minutes, x41 to NQ scale,
  2022-03-07 .. 2026-10-21. IEX-only (a few % of volume, noisy closes); proxy.
Costs are modelled as MNQ futures: $0.85/contract/side, 1 tick (0.25 pt) slippage on market and
stop fills; intraday only (no financing, no overnight).

## Periods and what each may be used for
| period | data | use | prior use in this repo |
|---|---|---|---|
| DEV 2016-11-15 .. 2021-12-31 | CFD15 | choose the strategy, measure Kronos forecast skill, set any thresholds | 2016-11..2022-03 was the ORB holdout (run once + once after a bug fix) |
| WALK-FORWARD 2022-01-01 .. 2025-07-31 | CFD15 | yearly re-fit/out-of-sample checks, reported, no rule changes from it | examined via QQQ in ORB, mean reversion and pairs research |
| RESERVED 2025-08-01 .. 2026-10-21 | QQQ15 (CFD15 overlap to 2025-10-01 as a consistency check) | final evaluation, run ONCE with frozen rules | ORB late-window evaluation on QQQ (other strategy) |

No period is pristine: every date range has been examined by earlier research on other
strategies. RESERVED is the only one after Kronos's public release (2025-08), i.e. the only one
Kronos's pretraining cannot have seen; its pretraining cutoff is not documented in the material
reachable here (the paper is unreachable from this environment), so any Kronos skill measured on
2016-2025 may include memorisation.
