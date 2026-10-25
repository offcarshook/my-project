# nq/: NQ/MNQ research on NautilusTrader (backtest + paper only)

```bash
pip install nautilus_trader==1.231.0 -r requirements.txt
python -m nq.backtest --data DIR --start 2022-03-21 --end 2024-03-31   # one ORB run
python -m nq.research --data DIR orb                                    # pre-registered variants, train+val
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
