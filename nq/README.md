# nq/: NQ/MNQ research on NautilusTrader (backtest + paper only)

```bash
pip install nautilus_trader==1.231.0 -r requirements.txt
python -m nq.backtest --data DIR --start 2022-03-21 --end 2024-03-31   # one ORB run
python -m nq.research --data DIR orb                                    # pre-registered variants, train+val
python -m nq.research --data DIR --out nq/results mr                    # Phase 2: mean reversion walk-forward
python -m nq.audit --data DIR                                           # accounting reconciliation
python -m pytest tests/test_nq_orb.py
```

* **Data: proxy, not futures.** No NQ/ES data is available, so MNQ/MES bars are QQQ/SPY 5-minute
  regular-hours candles x 41 / x 10, rounded to the 0.25 tick, stamped at candle close
  (`nq/data.py`). No Globex session, futures volume, rolls or basis.
* **Engine:** NautilusTrader `BacktestEngine`, `FuturesContract` (MNQ $2/pt, MES $5/pt), margin
  account, `PerContractFeeModel` ($0.85/contract/side), `FillModel(prob_slippage=1)` (one tick on
  market and stop fills). Market orders fill at the signal candle's close + 1 tick.
* **ORB** (`nq/strategies/orb.py`): clock-based range, completed candles only, stop-market at the
  other side of the range, flat at 15:50, contracts sized from risk %.

## Results so far (MNQ proxy, $50k, 0.5% risk)

| | train 2022-03..2024-03 | validation 2024-04..2025-03 | Apr 2025..Oct 2026 |
|---|---|---|---|
| ORB 15m baseline | +0.06R (t 1.0), +11% | +0.29R (t 2.7), +30% | +0.00R, -1.1% |
| ORB 5m (frozen pick) | +0.13R (t 1.9), +30% | +0.22R (t 1.8), +26% | +0.02R (t 0.2), -1.3% |

All six variants were positive in train and validation with futures-level costs (much lower than
ETF costs), but the frozen pick was flat on Apr 2025..Oct 2026. That period's QQQ data had already
been used in the ETF study, so it isn't an untouched test. Variant table:
`results/orb_variants_train_val.csv`.

## Accounting audit (`python -m nq.audit`, frozen 5m ORB, Apr 2025..Oct 2026)

* Trade log vs NautilusTrader account: final equity $49,329.40 both ways (gap $0.00).
* Compounded monthly returns = cumulative return (-1.341% both); arithmetic mean month -0.042%,
  geometric -0.071%.
* **Risk actually taken is below the setting**: median 0.38% per trade (p10-p90 0.28-0.47%) vs
  0.5%, because MNQ contracts are whole numbers and sizing includes a cost buffer. Stopped trades
  lose a median 0.39% of equity.
* Drawdowns are on closed-trade equity; intraday open-trade dips are not included.
* Execution: market and stop fills at the candle close + 1 tick; NautilusTrader's bar-mode fill
  model also slips limit fills by 1 tick (kept as a conservative offset to touch-fills).

## Phase 2: mean reversion (`nq/strategies/mean_reversion.py`)

Signals: VWAP deviation, rolling z-score, EMA/ATR dislocation; entry when the stretch crosses
back inside k; target = the mean fixed at entry; stop 1.5 ATR; 12-candle time exit; optional trend
(efficiency ratio > 0.35) and volatility (ATR > 2x its median) gates. 18 configs fixed before
running (3 signals x k 1.5/2.0/2.5 x gates off/on). Walk-forward on Mar 2022..Mar 2025 only: pick
the best t-stat on the previous 12 months, trade the next 6.

* **All 18 configs lose money over the research span** (best: -0.025R per trade). None is
  positive in more than 3 of 6 half-years. Gates help every signal (fading strong moves is the
  worst case) but don't make any positive.
* Walk-forward out-of-sample (4 half-years, 439 trades): **-0.055R per trade (t -1.16)**; the
  picks change every window (no stable parameter set).
* Not a cost problem: VWAP k=2 with gates is +0.008R **before** costs (0.050R costs per trade on
  ~28-point stops); without gates -0.005R gross.
* Same blocks, same engine: frozen 5m ORB +0.18R (t 2.4). (ORB was chosen on data overlapping
  these blocks, so this is not out-of-sample for ORB.)
* **Verdict: rejected.** No candidate qualified, so none was run on Apr 2025..Oct 2026; that
  period stays reserved (it is previously examined via the ETF study, not pristine). There is no
  untouched holdout left in the available data. Files: `results/mr_walkforward_picks.csv`,
  `results/mr_stability.csv`.
