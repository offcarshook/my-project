# Frozen noise-area momentum on 2025-04-01 -> 2026-10-21

Frozen: `N1 paper rules (14d band, 30-min checks, VWAP)` at 0.5% risk (commit 476ea3f). First run of this strategy on this period; ORB had been evaluated on it before.

## Strategy metrics

|  | N1 paper rules (14d band, 30-min checks, VWAP) |
|---|---|
| trades | 1,269 |
| trades_per_session | 3.24 |
| win_rate_pct | 33.41 |
| avg_win | 101.13 |
| avg_loss | -52.38 |
| profit_factor | 0.97 |
| expectancy_r_gross | 0.04 |
| expectancy_r | -0.01 |
| r_tstat | -0.26 |
| cost_r | 0.05 |
| net_return_pct | -5.53 |
| annualized_pct | -3.59 |
| month_mean_pct | -0.19 |
| month_median_pct | -0.78 |
| months_positive_pct | 47.37 |
| month_best_pct | 12.79 |
| month_worst_pct | -7.87 |
| max_drawdown_pct | -23.99 |
| longest_losing_streak | 17 |
| avg_hold_min | 115.32 |
| clustered_pct | 54.14 |

## Prop challenges (fresh start every session)

| candidate | risk_pct | period | windows | independent_windows | pass_pct | pass_ci95 | fail_pct | timeout_pct | median_days_to_pass | mc_pass_pct | zero_edge_pass_pct | edge_margin_pct |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| N1 paper rules (14d band, 30-min checks, VWAP) | 0.50 | seen | 392 | 18.67 | 10.97 | 3-32 | 0.00 | 89.03 | 7 | 14.68 | 16.14 | -1.46 |

## Per ETF

|  | trades | trades_per_session | win_rate_pct | avg_win | avg_loss | profit_factor | net_pnl | expectancy | expectancy_r | longest_losing_streak | fees |
|---|---|---|---|---|---|---|---|---|---|---|---|
| SOXL | 219 | 0.56 | 37.44 | 137.44 | -62.30 | 1.32 | 2734.42 | 12.49 | 0.11 | 18 | 32.23 |
| QQQ | 178 | 0.45 | 36.52 | 95.02 | -51.33 | 1.06 | 376.24 | 2.11 | 0.03 | 10 | 104.18 |
| XLK | 255 | 0.65 | 32.16 | 104.38 | -49.69 | 1.00 | -37.44 | -0.15 | 0.00 | 9 | 116.46 |
| IWM | 315 | 0.80 | 32.38 | 86.09 | -48.97 | 0.84 | -1650.45 | -5.24 | -0.04 | 13 | 157.45 |
| SPY | 302 | 0.77 | 30.80 | 87 | -52.14 | 0.74 | -2806.24 | -9.29 | -0.08 | 11 | 251.49 |

## By month

| ts | return_pct | trades | win_rate_pct | net_pnl |
|---|---|---|---|---|
| 2025-04 | 12.79 | 65 | 36.92 | 3197.76 |
| 2025-05 | -3.14 | 44 | 27.27 | -886.33 |
| 2025-06 | -1.45 | 61 | 34.43 | -396.34 |
| 2025-07 | -2.86 | 67 | 32.84 | -769.51 |
| 2025-08 | 0.65 | 69 | 33.33 | 168.87 |
| 2025-09 | -7.87 | 77 | 23.38 | -2071.03 |
| 2025-10 | 6.53 | 69 | 36.23 | 1584.17 |
| 2025-11 | 5.80 | 66 | 45.45 | 1498.80 |
| 2025-12 | -4.39 | 54 | 35.19 | -1199.09 |
| 2026-01 | -0.78 | 68 | 41.18 | -202.70 |
| 2026-02 | 0.64 | 69 | 39.13 | 166.45 |
| 2026-03 | 1.85 | 74 | 28.38 | 483.68 |
| 2026-04 | -2.15 | 59 | 30.51 | -570.05 |
| 2026-05 | -4.05 | 95 | 34.74 | -1052.32 |
| 2026-06 | 0.27 | 75 | 38.67 | 67.82 |
| 2026-07 | -6.51 | 90 | 23.33 | -1629.27 |
| 2026-08 | 0.05 | 49 | 26.53 | 11.54 |
| 2026-09 | -2.81 | 76 | 27.63 | -656.68 |
| 2026-10 | 3.83 | 42 | 45.24 | 870.76 |

## By side (R)

| side | size | mean |
|---|---|---|
| LONG | 671 | 0.00 |
| SHORT | 598 | -0.02 |

## By market regime (labels from past data only)

| regime | sessions | trades | win_rate_pct | expectancy_r | profit_factor | net_pnl |
|---|---|---|---|---|---|---|
| declining | 235 | 194 | 35.57 | 0.23 | 1.77 | 5266.03 |
| sideways | 442 | 579 | 31.09 | -0.08 | 0.73 | -5730.34 |
| trending up | 464 | 496 | 35.28 | -0.01 | 0.94 | -919.16 |
| high vol | 54 | 137 | 32.85 | 0.09 | 1.27 | 1528.30 |
| normal vol | 1,087 | 1,132 | 33.48 | -0.02 | 0.92 | -2911.76 |
