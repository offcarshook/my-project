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
    assert tr.side == "LONG" and tr.entry == 19985               # signal close (its tick is a cost)
    assert tr.entry_ts.tz_convert(NY).strftime("%H:%M") == "12:25"
    assert tr.target % 0.25 == 0 and tr.stop % 0.25 == 0         # on the tick grid
    assert tr.exit == tr.target and tr.pnl > 0                   # limit filled exactly at VWAP


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


def test_limit_never_fills_worse_than_its_price_and_needs_a_trade_through():
    from nq.backtest import COMMISSION_PER_SIDE
    t, _ = run_mean_reversion(dip_and_recover(), signal="vwap", entry_k=2.0, risk_pct=1.0, min_rr=0.5)
    tr = t.iloc[0]
    assert tr.exit >= tr.target                                  # a sell limit: at the limit or better
    # costs: entry market (commission + 1 tick) and exit limit (commission only)
    gross = (tr.exit - tr.entry) * tr.qty * 2
    assert tr.pnl == pytest.approx(gross - tr.qty * (2 * COMMISSION_PER_SIDE + 0.25 * 2))
    # price only TOUCHES the target (high == limit) and then falls back: no fill, time exit instead
    closes = [20000.0] * 30 + [19990, 19975, 19960, 19975, 19985] + [19986] * 40
    f = proxy_frame(day("2026-01-05", closes, spread=1.0), 1.0)
    t0, _ = run_mean_reversion(f, signal="vwap", entry_k=2.0, min_rr=0.5, max_hold_bars=6, risk_pct=1.0)
    target = t0.iloc[0].target
    touch = f.copy()
    i = touch.index.get_loc(t0.iloc[0].entry_ts) + 1
    touch.iloc[i, touch.columns.get_loc("high")] = target          # touches, never trades through
    t1, _ = run_mean_reversion(touch, signal="vwap", entry_k=2.0, min_rr=0.5, max_hold_bars=6, risk_pct=1.0)
    assert t1.iloc[0].exit < target
