"""The ONE bot's Nautilus strategy (nq/kronos_bot/strategy.py) on synthetic 15-minute sessions."""
import pandas as pd
import pytest

pytest.importorskip("nautilus_trader")

from nq.backtest import COMMISSION_PER_SIDE, run_strategy  # noqa: E402
from nq.data import proxy_frame  # noqa: E402
from nq.kronos_bot.strategy import SessionSignalBot, SessionSignalConfig  # noqa: E402

NY = "America/New_York"


def session(date, closes):
    """15-minute candles closing 09:45..16:00 (26), from candle START times 09:30.."""
    idx = pd.date_range(f"{date} 09:30", periods=len(closes), freq="15min", tz=NY)
    rows, prev = [], closes[0]
    for c in closes:
        rows.append((prev, max(prev, c) + 2, min(prev, c) - 2, c))
        prev = c
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx).assign(volume=1e3)


def run(frames, signals, **kw):
    f = proxy_frame(pd.concat(frames), 1.0).copy()
    f.index = f.index + pd.Timedelta(minutes=10)            # proxy_frame shifts by 5; 15-minute candles close +15
    holder = {}

    class Rec(SessionSignalBot):
        def __init__(self, config):
            super().__init__(config)
            holder["s"] = self
    trades, equity = run_strategy(Rec, SessionSignalConfig, f, bar_minutes=15, signals=tuple(signals), **kw)
    return trades, equity, holder["s"]


QUIET = [20000.0] * 23 + [20000, 20040, 20060]               # 15:30 close 20000 -> 16:00 close 20060


def test_trades_once_at_entry_time_and_exits_at_the_close():
    t, eq, bot = run([session("2026-01-05", QUIET)], [("2026-01-05", 1, 1.0)], risk_pct=1.0)
    assert len(t) == 1
    tr = t.iloc[0]
    assert tr.entry_ts.tz_convert(NY).strftime("%H:%M") == "15:30" and tr.entry == 20000.25
    assert tr.exit_ts.tz_convert(NY).strftime("%H:%M") == "16:00" and tr.exit_kind == "market"
    assert tr.pnl == pytest.approx((tr.exit - tr.entry) * tr.qty * 2 - 2 * COMMISSION_PER_SIDE * tr.qty)
    assert 50_000 + t.pnl.sum() == pytest.approx(float(eq.iloc[-1]))


def test_no_signal_short_signal_and_missing_entry_candle():
    t, _, _ = run([session("2026-01-05", QUIET)], [("2026-01-05", 0, 1.0)])
    assert t.empty
    t, _, _ = run([session("2026-01-05", QUIET)], [("2026-01-05", -1, 1.0)], risk_pct=1.0)
    assert t.iloc[0].side == "SHORT" and t.iloc[0].pnl < 0
    gappy = session("2026-01-05", QUIET).drop(pd.Timestamp("2026-01-05 15:15", tz=NY))   # no 15:30-close candle
    t, _, _ = run([gappy], [("2026-01-05", 1, 1.0)])
    assert t.empty


def test_protective_stop_and_daily_loss_guard():
    crash = [20000.0] * 23 + [20000, 19700, 19650]
    t, _, bot = run([session("2026-01-05", crash)], [("2026-01-05", 1, 1.0)], risk_pct=1.0, stop_atr=2.0)
    tr = t.iloc[0]
    assert tr.exit_kind == "stop" and tr.exit == tr.stop and -1.2 < tr.r < -0.9
    t, _, bot = run([session("2026-01-05", crash)], [("2026-01-05", 1, 1.0)], risk_pct=5.0, stop_atr=50.0,
                    max_daily_loss_pct=1.0)
    assert any("daily loss" in e["event"] for e in bot.events)
    assert t.iloc[0].exit_ts.tz_convert(NY).strftime("%H:%M") == "15:45"   # flattened by the guard, not at 16:00
