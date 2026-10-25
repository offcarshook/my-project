"""NautilusTrader ORB (nq/). Skipped where nautilus_trader isn't installed."""
import numpy as np
import pandas as pd
import pytest

pytest.importorskip("nautilus_trader")

from nq.backtest import COMMISSION_PER_SIDE, run_orb  # noqa: E402
from nq.data import proxy_frame  # noqa: E402

NY = "America/New_York"


def day(date, rows):
    """rows: (open, high, low, close) for consecutive 5-minute candles from 09:30 (candle START)."""
    idx = pd.date_range(f"{date} 09:30", periods=len(rows), freq="5min", tz=NY)
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx).assign(volume=1e5)


def session(date, breakout=True):
    rows = [(20000, 20050, 19950, 20000)] * 3                       # range 19950-20050 (0.5%)
    rows += [(20000, 20005, 19995, 20002)]
    rows += [(20002, 20060, 20001, 20055)] if breakout else [(20002, 20040, 19996, 20004)]
    rows += [(20055, 20070, 20052, 20065)] * 72 if breakout else [(20004, 20030, 19980, 20010)] * 72
    return day(date, rows)


def frame(*days):
    return proxy_frame(pd.concat(days), ratio=1.0)


def test_breakout_enters_next_tick_with_range_stop_and_flattens():
    t, _ = run_orb(frame(session("2026-01-05")), risk_pct=2.0)
    assert len(t) == 1
    tr = t.iloc[0]
    assert tr.side == "LONG" and tr.stop == 19950
    assert tr.entry == 20055.25                                      # breakout close + 1 tick (in the price)
    assert tr.entry_ts.tz_convert(NY).strftime("%H:%M") == "09:55"
    assert tr.exit_ts.tz_convert(NY).strftime("%H:%M") == "15:50"    # flattened at the cutoff
    # $1,000 risk / (105 pts x $2 + costs) -> 4 contracts
    assert tr.qty == 4
    gross = (tr.exit - tr.entry) * tr.qty * 2
    assert tr.pnl == pytest.approx(gross - tr.qty * 2 * COMMISSION_PER_SIDE)    # market slippage is in the prices


def test_no_trade_without_breakout_or_with_a_missing_range_candle():
    t, _ = run_orb(frame(session("2026-01-05", breakout=False)))
    assert t.empty
    gappy = session("2026-01-05").drop(pd.Timestamp("2026-01-05 09:30", tz=NY))
    t, _ = run_orb(frame(gappy))
    assert t.empty


def test_stop_loss_costs_about_one_r():
    rows = [(20000, 20050, 19950, 20000)] * 3 + [(20000, 20005, 19995, 20002), (20002, 20060, 20001, 20055)]
    rows += [(20055, 20056, 19940, 19945)] + [(19945, 19950, 19940, 19945)] * 70   # straight through the stop
    t, _ = run_orb(frame(day("2026-01-05", rows)), risk_pct=2.0)
    tr = t.iloc[0]
    assert tr.exit == 19950 and tr.exit_kind == "stop"                   # engine fills a stop at its trigger
    gross = (tr.exit - tr.entry) * tr.qty * 2
    assert tr.pnl == pytest.approx(gross - tr.qty * (2 * COMMISSION_PER_SIDE + 0.25 * 2))   # + stop tick as cost
    assert -1.15 < tr.r < -1.0
