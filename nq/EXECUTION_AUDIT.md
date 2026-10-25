# Execution-model and data audit (NautilusTrader 1.231.0, bar execution)

Historical backtests and paper simulation only. All data is **IEX-only QQQ/SPY 5-minute candles**;
"MNQ" is QQQ x 41 rounded to the 0.25 tick. Nothing here validates real NQ/ES futures profitability.

## Verified issues and fixes

| # | Issue (verified in the installed source / by test) | Fix |
|---|---|---|
| 1 | `FillModel(prob_slippage)` slips **every** fill on an L1 book, limit orders included (`engine.pyx`: `if self.book_type == BookType.L1_MBP and self._fill_model.is_slipped()`), so limits filled a tick worse than their price. | `ConservativeFillModel` (`nq/execution.py`): prob_slippage=0; overrides `get_orderbook_for_fill_simulation` so **market** orders fill 1 tick beyond the touch (slippage in the fill price). |
| 2 | The interim `CommissionAndSlippage` fee model kept fill prices exact and booked all slippage as a fee, hiding it from fill prices. | Replaced: market slippage is now in the fill price; fees are commissions (+ stop slippage, see 3). |
| 3 | Overriding the book for **stop** orders loses the engine's trigger-vs-gap logic (`_fill_at_market` is internal): a stop at 19,950 filled at the bar's 19,940 low. | Stops use the engine's own logic (fill at the trigger when the replay moves through it, at the open on a gap); their 1 tick is a separately reported cost in `CommissionAndStopSlippage`. Slippage is booked once per fill: in the price (market) or the fee (stop). |
| 4 | Bars replay in the fixed order Open→High→Low→Close: when one bar reaches both a long's target and stop, the **target fills first** (optimistic). Shorts already hit the stop first. No native stop-first option. | `conservative_adjust` re-books such long exits as stop-outs; the mean-reversion study reports both. Affected 4 of 15,775 MR trades (0.03%): results unchanged to 3 decimals except vwap configs (e.g. -0.026R → -0.029R). ORB has no target, unaffected. |
| 5 | A market order sent from `on_bar` fills at that bar's **close** (+1 tick). On these IEX candles the next open is a median **7 ticks** from that close (p90 26; only 13% within 1 tick). An earlier note claiming "within a tick or two" was **wrong**. | Measured, not hidden: `nq/fill_timing.py` re-prices entries at the next open. ORB train 5m +0.130R → +0.099R, late +0.018R → +0.005R, validation unchanged; MR unchanged. Close fills slightly flatter ORB. |
| 6 | Limit orders that a bar **gaps past** fill at the limit, not the better open (no price improvement). | Kept (conservative) and pinned by tests. Limits need a trade-through (`prob_fill_on_limit=0`); a touch doesn't fill. |
| 7 | Bar wrangler rejected pandas copy-on-write (read-only) arrays from edited frames. | `nq/data.to_bars` passes fresh writable columns. |
| 8 | Trade logs didn't say which order closed each position. | `exit_kind` (stop / target / market) logged from the closing order; reporting only, no trading-logic change. |

## Reconciliation (frozen 5m ORB, Apr 2025..Oct 2026, previously examined period)

Trade log $49,329.40 = engine account $49,329.40 (gap $0.00); compounded monthly = cumulative
(-1.341%); exits: 210 stops, 142 market (flatten). Risk actually taken: median 0.38% per trade vs
the 0.5% setting (whole contracts). `test_account_equals_fill_prices_minus_fees_exactly` proves
account equity = fill prices x quantity x multiplier - fees, with one slippage per fill.

## Reproduced results under the audited model (`nq/results/execution_audit/`)

Same data, frozen parameters, $0.85/contract/side commission, 1 tick slippage on market and stop
fills. Earlier reports are kept unchanged next to these.

| | original | after limit fix | audited model |
|---|---|---|---|
| ORB 5m train / val (R) | +0.130 / +0.224 | +0.130 / +0.224 | +0.130 / +0.223 |
| ORB 5m late (examined) | -1.341% | -1.341% | -1.341% (+0.018R) |
| MR walk-forward OOS (R) | -0.055 | -0.037 | -0.037 (conservative: -0.037) |
| MR configs positive | 0 / 18 | 0 / 18 | 0 / 18 (engine and conservative) |

Conclusions unchanged: ORB positive on 2022-03..2025-03, flat on the examined 2025-26 window;
mean reversion and the QQQ-SPY pair rejected.

## Remaining limitations

* **IEX-only candles** (`source = iex`): OHLC from a few % of volume; noisy closes (see 5); volume
  is IEX volume. Not consolidated tape, not futures.
* **Missing candles:** ~250 of 1,162 sessions per symbol miss at least one candle; ORB skips days
  missing an opening-range candle; pairs use only candles present for both legs.
* **Not dividend-adjusted** (SPY opens at 431.72 on 2022-03-07): affects ex-dividend mornings only
  (all strategies are flat overnight).
* **Proxy scaling:** MNQ = QQQ x 41, MES = SPY x 10: no Globex session, futures liquidity, rolls,
  basis or futures microstructure. 1 QQQ cent = 1.64 MNQ ticks, so tick-level costs are not
  representative of real NQ.
* **OHLC replay:** intrabar order unknown; handled conservatively for target/stop conflicts (4) and
  measured for market-entry timing (5). No latency or queue modelling.
* **No untouched holdout:** Apr 2025..Oct 2026 has been examined; it is not a clean test.

## Reproduce

```bash
pip install nautilus_trader==1.231.0 statsmodels -r requirements.txt
python -m pytest -q                                        # everything (nq tests need nautilus_trader)
python -m pytest -q tests/test_nq_execution.py             # order-type / fill / reconciliation audit
python -m nq.audit --data DIR                              # accounting reconciliation (frozen ORB, late window)
python -m nq.research --data DIR reproduce                 # all corrected results -> nq/results/execution_audit/
python -m nq.fill_timing --data DIR                        # close-vs-next-open entry sensitivity
```
