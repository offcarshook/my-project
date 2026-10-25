"""NautilusTrader mean reversion (nq/). Skipped where nautilus_trader isn't installed."""
import pandas as pd
import pytest

pytest.importorskip("nautilus_trader")

from nq.backtest import run_mean_reversion  # noqa: E402
from nq.data import proxy_frame  # noqa: E402

NY = "America/New_York"


def day(date, closes, spread=2.0):
    idx = pd.date_range(f"{date} 09:30", periods=len(closes), freq="5min", tz=NY)
    rows = []
    prev = closes[0]
    for c in closes:
        rows.append((prev, max(prev, c) + spread, min(prev, c) - spread, c))
        prev = c
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx).assign(volume=1e5)


def dip_and_recover():
    closes = [20000.0] * 30                                  # quiet to 12:00: VWAP 20000, ATR ~4
    closes += [19990, 19975, 19960]                          # stretched far below VWAP
    closes += [19975]                                        # still beyond -2 ATR (ATR widened)
    closes += [19985]                                        # first candle back inside -k -> long
    closes += [19995, 20005] + [20004] * 40                  # reverts through VWAP
    return proxy_frame(day("2026-01-05", closes), 1.0)


def test_long_after_a_stretch_reverts_and_takes_profit_at_vwap():
    t, _ = run_mean_reversion(dip_and_recover(), signal="vwap", entry_k=2.0, risk_pct=1.0, min_rr=0.5)
    assert len(t) == 1
    tr = t.iloc[0]
    assert tr.side == "LONG" and tr.entry == 19985.25            # signal close + 1 tick
    assert tr.entry_ts.tz_convert(NY).strftime("%H:%M") == "12:25"
    assert tr.target % 0.25 == 0 and tr.stop % 0.25 == 0         # on the tick grid
    # limit at VWAP; NautilusTrader's FillModel(prob_slippage=1) also slips limit fills one tick
    # in bar mode, a conservative offset to assuming a limit fills on a touch
    assert tr.exit == tr.target - 0.25 and tr.pnl > 0


def test_trend_gate_blocks_the_fade_and_no_entry_outside_hours():
    t, _ = run_mean_reversion(dip_and_recover(), signal="vwap", entry_k=2.0, min_rr=0.5, er_max=0.2)
    assert t.empty                                               # the drop was a straight line
    t, _ = run_mean_reversion(dip_and_recover(), signal="vwap", entry_k=2.0, min_rr=0.5, last_entry="12:00")
    assert t.empty


def test_time_exit_closes_after_max_hold():
    closes = [20000.0] * 30 + [19990, 19975, 19960, 19975, 19985] + [19986] * 40   # never reaches VWAP or the stop
    t, _ = run_mean_reversion(proxy_frame(day("2026-01-05", closes, spread=1.0), 1.0), signal="vwap",
                              entry_k=2.0, min_rr=0.5, max_hold_bars=6, risk_pct=1.0)
    tr = t.iloc[0]
    held = (tr.exit_ts - tr.entry_ts).total_seconds() / 60
    assert 25 <= held <= 35                                       # ~6 candles
