# ORB baseline diagnosis (train vs validation)

Mean R per trade after costs. Numeric features are split at the train terciles. A pattern
only counts if it has the same sign in both periods and a plausible reason.

## symbol

| symbol | n_train | mean_r_train | t_train | win_pct_train | n_val | mean_r_val | t_val | win_pct_val |
|---|---|---|---|---|---|---|---|---|
| IWM | 448 | 0.10 | 1.75 | 46.43 | 222 | 0.06 | 0.70 | 48.20 |
| QQQ | 295 | 0.06 | 0.74 | 42.37 | 153 | 0.10 | 0.90 | 44.44 |
| SOXL | 178 | -0.05 | -0.50 | 39.89 | 102 | 0.27 | 1.90 | 48.04 |
| SPY | 437 | 0.05 | 0.75 | 40.50 | 179 | -0.04 | -0.40 | 40.78 |
| XLK | 502 | 0.06 | 1.01 | 42.03 | 226 | 0.27 | 2.67 | 50.44 |

## side

| side | n_train | mean_r_train | t_train | win_pct_train | n_val | mean_r_val | t_val | win_pct_val |
|---|---|---|---|---|---|---|---|---|
| LONG | 931 | 0.10 | 2.14 | 44.36 | 426 | 0.09 | 1.46 | 48.36 |
| SHORT | 929 | 0.02 | 0.47 | 40.80 | 456 | 0.16 | 2.22 | 44.96 |

## etf_side

| etf_side | n_train | mean_r_train | t_train | win_pct_train | n_val | mean_r_val | t_val | win_pct_val |
|---|---|---|---|---|---|---|---|---|
| IWM LONG | 220 | 0.03 | 0.35 | 46.36 | 102 | -0.02 | -0.18 | 47.06 |
| IWM SHORT | 228 | 0.18 | 2.03 | 46.49 | 120 | 0.13 | 0.98 | 49.17 |
| QQQ LONG | 139 | 0.05 | 0.44 | 40.29 | 77 | 0.13 | 0.88 | 49.35 |
| QQQ SHORT | 156 | 0.06 | 0.60 | 44.23 | 76 | 0.08 | 0.45 | 39.47 |
| SOXL LONG | 90 | 0.11 | 0.73 | 44.44 | 52 | 0.09 | 0.56 | 44.23 |
| SOXL SHORT | 88 | -0.20 | -1.71 | 35.23 | 50 | 0.45 | 1.94 | 52 |
| SPY LONG | 228 | 0.14 | 1.40 | 43.42 | 86 | 0.01 | 0.05 | 45.35 |
| SPY SHORT | 209 | -0.04 | -0.39 | 37.32 | 93 | -0.08 | -0.56 | 36.56 |
| XLK LONG | 254 | 0.14 | 1.56 | 45.67 | 109 | 0.22 | 1.62 | 53.21 |
| XLK SHORT | 248 | -0.02 | -0.26 | 38.31 | 117 | 0.31 | 2.13 | 47.86 |

## entry_bucket

| entry_bucket | n_train | mean_r_train | t_train | win_pct_train | n_val | mean_r_val | t_val | win_pct_val |
|---|---|---|---|---|---|---|---|---|
| 09:50 (first) | 670 | 0.09 | 1.72 | 43.58 | 310 | -0.04 | -0.50 | 39.68 |
| 09:55-10:15 | 794 | 0.06 | 1.19 | 42.57 | 403 | 0.26 | 3.61 | 51.37 |
| 10:20-11:30 | 396 | 0.00 | 0.07 | 40.91 | 169 | 0.10 | 0.97 | 47.93 |

## dow

| dow | n_train | mean_r_train | t_train | win_pct_train | n_val | mean_r_val | t_val | win_pct_val |
|---|---|---|---|---|---|---|---|---|
| Fri | 380 | 0.17 | 2.34 | 45.79 | 185 | 0.15 | 1.61 | 49.19 |
| Mon | 332 | -0.12 | -2.06 | 38.85 | 179 | 0.03 | 0.28 | 46.37 |
| Thu | 376 | 0.17 | 2.15 | 43.35 | 179 | 0.10 | 0.86 | 40.78 |
| Tue | 376 | 0.21 | 3.14 | 50.80 | 175 | 0.28 | 2.65 | 52.57 |
| Wed | 396 | -0.15 | -2.37 | 34.09 | 164 | 0.06 | 0.53 | 43.90 |

## own_trend

| own_trend | n_train | mean_r_train | t_train | win_pct_train | n_val | mean_r_val | t_val | win_pct_val |
|---|---|---|---|---|---|---|---|---|
| against | 927 | 0.03 | 0.57 | 40.99 | 449 | 0.16 | 2.44 | 48.77 |
| with | 933 | 0.09 | 2.08 | 44.16 | 433 | 0.09 | 1.31 | 44.34 |

## mkt_trend

| mkt_trend | n_train | mean_r_train | t_train | win_pct_train | n_val | mean_r_val | t_val | win_pct_val |
|---|---|---|---|---|---|---|---|---|
| against | 923 | 0.04 | 0.87 | 40.95 | 441 | 0.14 | 2.07 | 46.94 |
| with | 937 | 0.08 | 1.78 | 44.18 | 441 | 0.11 | 1.66 | 46.26 |

## mkt_vol

| mkt_vol | n_train | mean_r_train | t_train | win_pct_train | n_val | mean_r_val | t_val | win_pct_val |
|---|---|---|---|---|---|---|---|---|
| high | 32 | -0.07 | -0.34 | 40.62 | 137 | 0.30 | 2.47 | 51.83 |
| low | 1,117 | 0.09 | 2.15 | 43.06 | 351 | 0.24 | 2.85 | 47.01 |
| n/a | 247 | 0.09 | 0.94 | 39.68 | — | — | — | — |
| normal | 464 | -0.02 | -0.33 | 43.10 | 394 | -0.04 | -0.67 | 44.42 |

## reason

| reason | n_train | mean_r_train | t_train | win_pct_train | n_val | mean_r_val | t_val | win_pct_val |
|---|---|---|---|---|---|---|---|---|
| end of day | 1,003 | 1 | 25.98 | 78.96 | 516 | 0.96 | 17.24 | 79.65 |
| stop loss | 857 | -1.04 | -675.42 | 0.00 | 366 | -1.06 | -107.08 | 0.00 |

## fast_fail

| fast_fail | n_train | mean_r_train | t_train | win_pct_train | n_val | mean_r_val | t_val | win_pct_val |
|---|---|---|---|---|---|---|---|---|
| False | 1,762 | 0.12 | 3.71 | 44.95 | 823 | 0.21 | 4.26 | 49.94 |
| True | 98 | -1.06 | -119.11 | 0.00 | 59 | -1.06 | -119.12 | 0.00 |

## strength_atr

| strength_atr | n_train | mean_r_train | t_train | win_pct_train | n_val | mean_r_val | t_val | win_pct_val |
|---|---|---|---|---|---|---|---|---|
| (-inf, 0.244] | 620 | 0.02 | 0.38 | 38.06 | 286 | 0.02 | 0.20 | 41.61 |
| (0.244, 0.582] | 620 | 0.04 | 0.74 | 42.26 | 293 | 0.09 | 1.16 | 46.08 |
| (0.582, inf] | 620 | 0.11 | 2.32 | 47.42 | 303 | 0.26 | 3.28 | 51.81 |

## chase_r

| chase_r | n_train | mean_r_train | t_train | win_pct_train | n_val | mean_r_val | t_val | win_pct_val |
|---|---|---|---|---|---|---|---|---|
| (-inf, 0.117] | 620 | 0.06 | 1.07 | 41.13 | 278 | 0.11 | 1.22 | 44.24 |
| (0.117, 0.233] | 620 | 0.05 | 0.96 | 43.06 | 308 | 0.15 | 1.82 | 47.08 |
| (0.233, inf] | 620 | 0.06 | 1.23 | 43.55 | 296 | 0.12 | 1.55 | 48.31 |

## rvol

| rvol | n_train | mean_r_train | t_train | win_pct_train | n_val | mean_r_val | t_val | win_pct_val |
|---|---|---|---|---|---|---|---|---|
| (-inf, 0.689] | 620 | 0.04 | 0.72 | 40.81 | 324 | 0.11 | 1.39 | 46.60 |
| (0.689, 1.261] | 620 | 0.06 | 1.07 | 43.55 | 296 | 0.17 | 1.96 | 47.30 |
| (1.261, inf] | 620 | 0.08 | 1.48 | 43.39 | 262 | 0.10 | 1.20 | 45.80 |

## width_datr

| width_datr | n_train | mean_r_train | t_train | win_pct_train | n_val | mean_r_val | t_val | win_pct_val |
|---|---|---|---|---|---|---|---|---|
| (-inf, 0.218] | 620 | 0.06 | 1.01 | 38.71 | 312 | 0.09 | 1.03 | 42.63 |
| (0.218, 0.295] | 620 | 0.04 | 0.73 | 41.61 | 269 | 0.28 | 3.29 | 54.65 |
| (0.295, inf] | 620 | 0.07 | 1.64 | 47.42 | 301 | 0.02 | 0.31 | 43.52 |

## gap_with_pct

| gap_with_pct | n_train | mean_r_train | t_train | win_pct_train | n_val | mean_r_val | t_val | win_pct_val |
|---|---|---|---|---|---|---|---|---|
| (-0.243, 0.349] | 620 | 0.02 | 0.39 | 41.45 | 326 | -0.00 | -0.03 | 39.57 |
| (-inf, -0.243] | 620 | 0.05 | 0.89 | 42.10 | 315 | 0.17 | 2.24 | 49.21 |
| (0.349, inf] | 620 | 0.11 | 1.93 | 44.19 | 241 | 0.23 | 2.65 | 52.70 |

## hold_min

| hold_min | n_train | mean_r_train | t_train | win_pct_train | n_val | mean_r_val | t_val | win_pct_val |
|---|---|---|---|---|---|---|---|---|
| (-inf, 150.0] | 630 | -1.04 | -399.79 | 0.00 | 258 | -1.02 | -31.41 | 0.39 |
| (150.0, 345.0] | 643 | 0.22 | 4.27 | 49.46 | 328 | 0.22 | 3.04 | 53.35 |
| (345.0, inf] | 587 | 1.06 | 20.49 | 80.75 | 296 | 1.01 | 13.38 | 79.39 |

## exits and path (train)

* MFE (best move in R before exit): median 0.80, 43% reach +1R, 18% reach +2R, 7% reach +3R
* stopped within 3 candles: 5% of trades
* fill was on average 0.18R past the breakout level
* gross 0.120R vs net 0.059R per trade

## exits and path (validation)

* MFE (best move in R before exit): median 0.89, 46% reach +1R, 21% reach +2R, 9% reach +3R
* stopped within 3 candles: 7% of trades
* fill was on average 0.17R past the breakout level
* gross 0.191R vs net 0.124R per trade
