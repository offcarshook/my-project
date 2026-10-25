# Frozen 5m ORB: trade journal report (MNQ proxy from IEX-only QQQ; not NQ futures data)

Frozen parameters: 5-minute opening range, stop at the other side, hold to 15:50, 0.5% risk, $50k start; $0.85/contract/side commission; 1 tick slippage on market and stop fills. `later_examined` was looked at before freezing: not a clean test. **Forward paper test: not started** (needs new data not used for any decision).

Intraday drawdown uses equity marked at each candle's worst price with the open position included; closed-trade drawdown is shown next to it. Journal and engine account reconcile to the cent for every period (asserted).

## Summary

| metric | train | validation | later_examined |
|---|---|---|---|
| trades | 628.000 | 262.000 | 352.000 |
| trades_per_week | 5.957 | 5.080 | 4.338 |
| trades_per_month | 25.120 | 21.833 | 18.526 |
| net_pnl_usd | 15,140.200 | 12,926.300 | -670.600 |
| return_pct | 30.280 | 25.853 | -1.341 |
| gross_profit_usd | 105,204.900 | 48,774.800 | 40,932.600 |
| gross_loss_usd | -90,064.700 | -35,848.500 | -41,603.200 |
| avg_pnl_per_trade_usd | 24.109 | 49.337 | -1.905 |
| win_rate_pct | 36.624 | 38.168 | 36.364 |
| breakeven_rate_pct | 0.000 | 0.000 | 0.000 |
| avg_winner_usd | 457.413 | 487.748 | 319.786 |
| avg_loser_usd | -226.293 | -221.287 | -185.729 |
| payoff_ratio | 2.021 | 2.204 | 1.722 |
| profit_factor | 1.168 | 1.361 | 0.984 |
| expectancy_usd | 24.109 | 49.337 | -1.905 |
| expectancy_r | 0.130 | 0.223 | 0.018 |
| r_tstat | 1.894 | 1.819 | 0.202 |
| max_dd_closed_usd | -3,745.800 | -3,458.700 | -4,109.000 |
| max_dd_closed_pct | -6.396 | -5.701 | -7.954 |
| max_dd_intraday_mtm_usd | -4,067.350 | -4,066.500 | -4,277.800 |
| max_dd_intraday_mtm_pct | -6.921 | -6.647 | -8.281 |
| return_over_max_dd | 3.722 | 3.179 | -0.157 |
| recovery_factor | 3.722 | 3.179 | -0.157 |
| longest_losing_streak | 11.000 | 7.000 | 8.000 |
| largest_loss_usd | -431.100 | -659.400 | -573.400 |
| avg_mae_r | 0.914 | 0.913 | 0.882 |
| avg_mfe_r | 1.494 | 1.670 | 1.376 |
| median_risk_pct_of_equity | 0.394 | 0.386 | 0.378 |
| commission_usd | 2,327.300 | 783.700 | 719.100 |
| slippage_usd | 1,369.000 | 461.000 | 423.000 |
| costs_per_trade_r | 0.025 | 0.022 | 0.018 |
| sharpe_daily_ann | 1.242 | 2.254 | -0.059 |
| sortino_daily_ann | 3.799 | 7.828 | -0.181 |
| months | 25.000 | 12.000 | 19.000 |
| profitable_months_pct | 60.000 | 75.000 | 36.842 |
| avg_month_pct | 1.112 | 1.988 | -0.041 |
| median_month_pct | 1.290 | 1.816 | -0.673 |
| best_month_pct | 8.297 | 8.782 | 6.824 |
| worst_month_pct | -3.931 | -3.195 | -3.083 |

## train (2022-03-21 .. 2024-03-31)

### By year
| date | net_pnl_usd |
|---|---|
| 2022 | 6,105.600 |
| 2023 | 10,956.900 |
| 2024 | -1,922.300 |

### By month (%)
| date | return_pct |
|---|---|
| 2022-03 | -3.930 |
| 2022-04 | 7.690 |
| 2022-05 | 2.310 |
| 2022-06 | -0.640 |
| 2022-07 | 8.300 |
| 2022-08 | -0.950 |
| 2022-09 | -2.080 |
| 2022-10 | 5.060 |
| 2022-11 | -2.690 |
| 2022-12 | -0.640 |
| 2023-01 | 4.350 |
| 2023-02 | -2.860 |
| 2023-03 | -1.950 |
| 2023-04 | 1.650 |
| 2023-05 | 3.970 |
| 2023-06 | 2.820 |
| 2023-07 | 2.680 |
| 2023-08 | 1.970 |
| 2023-09 | 2.320 |
| 2023-10 | 0.590 |
| 2023-11 | 1.290 |
| 2023-12 | 1.390 |
| 2024-01 | -2.900 |
| 2024-02 | -0.750 |
| 2024-03 | 0.790 |

### By side
| side | trades | net_pnl_usd | win_pct | expectancy_r |
|---|---|---|---|---|
| LONG | 316.000 | 9,615.000 | 36.392 | 0.140 |
| SHORT | 312.000 | 5,525.200 | 36.859 | 0.119 |

### By day of week
| weekday | trades | net_pnl_usd | win_pct | expectancy_r |
|---|---|---|---|---|
| Fri | 122.000 | 8,091.800 | 39.344 | 0.330 |
| Mon | 108.000 | -7,040.300 | 25.000 | -0.295 |
| Thu | 130.000 | 8,063.800 | 40.769 | 0.285 |
| Tue | 130.000 | 11,819.900 | 44.615 | 0.405 |
| Wed | 138.000 | -5,795.000 | 31.884 | -0.121 |

### By entry hour (session)
| entry_hour | trades | net_pnl_usd | win_pct | expectancy_r |
|---|---|---|---|---|
| 09:00 | 418.000 | 16,774.200 | 37.081 | 0.210 |
| 10:00 | 187.000 | -1,265.000 | 35.294 | -0.028 |
| 11:00 | 23.000 | -369.000 | 39.130 | -0.048 |

### By exit reason
| exit_reason | trades | net_pnl_usd | win_pct | expectancy_r |
|---|---|---|---|---|
| end of day | 258.000 | 102,599.000 | 89.147 | 1.786 |
| stop loss | 370.000 | -87,458.800 | 0.000 | -1.025 |

### By gap vs trade direction
| gap_direction | trades | net_pnl_usd | win_pct | expectancy_r |
|---|---|---|---|---|
| against | 300.000 | -1,327.700 | 33.667 | -0.005 |
| with | 328.000 | 16,467.900 | 39.329 | 0.253 |

### By prior-day trend/chop
| prior_day_regime | trades | net_pnl_usd | win_pct | expectancy_r |
|---|---|---|---|---|
| chop | 569.000 | 17,462.700 | 37.610 | 0.160 |
| trend | 59.000 | -2,322.500 | 27.119 | -0.158 |

### By range width vs 20 sessions
| or_width_bucket | trades | net_pnl_usd | win_pct | expectancy_r |
|---|---|---|---|---|
| narrow | 210.000 | 3,990.200 | 34.286 | 0.126 |
| normal | 209.000 | 10,023.800 | 37.799 | 0.224 |
| wide | 209.000 | 1,126.200 | 37.799 | 0.039 |

## validation (2024-04-01 .. 2025-03-31)

### By year
| date | net_pnl_usd |
|---|---|
| 2024 | 11,183.800 |
| 2025 | 1,742.500 |

### By month (%)
| date | return_pct |
|---|---|
| 2024-04 | 0.430 |
| 2024-05 | 2.520 |
| 2024-06 | 1.220 |
| 2024-07 | 8.780 |
| 2024-08 | 5.520 |
| 2024-09 | -2.220 |
| 2024-10 | -1.240 |
| 2024-11 | 0.880 |
| 2024-12 | 5.000 |
| 2025-01 | -3.190 |
| 2025-02 | 2.410 |
| 2025-03 | 3.740 |

### By side
| side | trades | net_pnl_usd | win_pct | expectancy_r |
|---|---|---|---|---|
| LONG | 134.000 | 5,829.300 | 41.791 | 0.235 |
| SHORT | 128.000 | 7,097.000 | 34.375 | 0.211 |

### By day of week
| weekday | trades | net_pnl_usd | win_pct | expectancy_r |
|---|---|---|---|---|
| Fri | 57.000 | 877.200 | 36.842 | 0.070 |
| Mon | 55.000 | -778.300 | 27.273 | -0.128 |
| Thu | 45.000 | 6,052.500 | 42.222 | 0.630 |
| Tue | 53.000 | 4,321.400 | 41.509 | 0.347 |
| Wed | 52.000 | 2,453.500 | 44.231 | 0.284 |

### By entry hour (session)
| entry_hour | trades | net_pnl_usd | win_pct | expectancy_r |
|---|---|---|---|---|
| 09:00 | 183.000 | 7,484.300 | 38.251 | 0.178 |
| 10:00 | 69.000 | 6,794.200 | 42.029 | 0.487 |
| 11:00 | 10.000 | -1,352.200 | 10.000 | -0.771 |

### By exit reason
| exit_reason | trades | net_pnl_usd | win_pct | expectancy_r |
|---|---|---|---|---|
| end of day | 106.000 | 48,353.700 | 94.340 | 2.093 |
| stop loss | 156.000 | -35,427.400 | 0.000 | -1.047 |

### By gap vs trade direction
| gap_direction | trades | net_pnl_usd | win_pct | expectancy_r |
|---|---|---|---|---|
| against | 133.000 | 7,060.800 | 36.090 | 0.217 |
| with | 129.000 | 5,865.500 | 40.310 | 0.230 |

### By prior-day trend/chop
| prior_day_regime | trades | net_pnl_usd | win_pct | expectancy_r |
|---|---|---|---|---|
| chop | 242.000 | 13,160.700 | 38.430 | 0.238 |
| trend | 20.000 | -234.400 | 35.000 | 0.044 |

### By range width vs 20 sessions
| or_width_bucket | trades | net_pnl_usd | win_pct | expectancy_r |
|---|---|---|---|---|
| narrow | 88.000 | 6,867.800 | 36.364 | 0.368 |
| normal | 87.000 | 4,346.000 | 35.632 | 0.221 |
| wide | 87.000 | 1,712.500 | 42.529 | 0.078 |

## later_examined (2025-04-01 .. 2026-10-21)

### By year
| date | net_pnl_usd |
|---|---|
| 2025 | -458.400 |
| 2026 | -212.200 |

### By month (%)
| date | return_pct |
|---|---|
| 2025-04 | 2.720 |
| 2025-05 | -1.930 |
| 2025-06 | -3.080 |
| 2025-07 | -0.640 |
| 2025-08 | 2.690 |
| 2025-09 | -2.110 |
| 2025-10 | 2.160 |
| 2025-11 | -1.990 |
| 2025-12 | 1.470 |
| 2026-01 | -1.680 |
| 2026-02 | 6.820 |
| 2026-03 | -1.450 |
| 2026-04 | -2.110 |
| 2026-05 | -0.130 |
| 2026-06 | 2.450 |
| 2026-07 | 0.970 |
| 2026-08 | -2.510 |
| 2026-09 | -0.670 |
| 2026-10 | -1.770 |

### By side
| side | trades | net_pnl_usd | win_pct | expectancy_r |
|---|---|---|---|---|
| LONG | 191.000 | 1,005.100 | 40.838 | 0.071 |
| SHORT | 161.000 | -1,675.700 | 31.056 | -0.046 |

### By day of week
| weekday | trades | net_pnl_usd | win_pct | expectancy_r |
|---|---|---|---|---|
| Fri | 71.000 | -1,137.700 | 29.577 | -0.091 |
| Mon | 57.000 | 1,962.600 | 45.614 | 0.229 |
| Thu | 71.000 | 3,136.400 | 36.620 | 0.225 |
| Tue | 76.000 | -2,198.000 | 35.526 | -0.102 |
| Wed | 77.000 | -2,433.900 | 36.364 | -0.112 |

### By entry hour (session)
| entry_hour | trades | net_pnl_usd | win_pct | expectancy_r |
|---|---|---|---|---|
| 09:00 | 236.000 | -688.400 | 33.051 | 0.009 |
| 10:00 | 93.000 | -8.400 | 41.935 | 0.033 |
| 11:00 | 23.000 | 26.200 | 47.826 | 0.038 |

### By exit reason
| exit_reason | trades | net_pnl_usd | win_pct | expectancy_r |
|---|---|---|---|---|
| end of day | 142.000 | 40,141.800 | 90.141 | 1.560 |
| stop loss | 210.000 | -40,812.400 | 0.000 | -1.025 |

### By gap vs trade direction
| gap_direction | trades | net_pnl_usd | win_pct | expectancy_r |
|---|---|---|---|---|
| against | 175.000 | -133.100 | 37.714 | 0.014 |
| with | 177.000 | -537.500 | 35.028 | 0.021 |

### By prior-day trend/chop
| prior_day_regime | trades | net_pnl_usd | win_pct | expectancy_r |
|---|---|---|---|---|
| chop | 323.000 | 606.900 | 36.842 | 0.036 |
| trend | 29.000 | -1,277.500 | 31.034 | -0.188 |

### By range width vs 20 sessions
| or_width_bucket | trades | net_pnl_usd | win_pct | expectancy_r |
|---|---|---|---|---|
| narrow | 118.000 | 3,583.000 | 37.288 | 0.219 |
| normal | 118.000 | -3,337.100 | 32.203 | -0.145 |
| wide | 116.000 | -916.500 | 39.655 | -0.023 |
