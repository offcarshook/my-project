"""QQQ-SPY pairs research code (nq/pairs.py): causality, synchronized fills, two-leg costs."""
import numpy as np
import pandas as pd
import pytest

pytest.importorskip("statsmodels")

from nq.pairs import PairConfig, backtest, daily_betas, leg_cost_bps, signals  # noqa: E402

NY = "America/New_York"


def frame(days=25, seed=0, shock_day=None):
    rng = np.random.default_rng(seed)
    idx = []
    for d in pd.bdate_range("2026-01-05", periods=days):
        idx += list(pd.date_range(f"{d.date()} 09:30", periods=78, freq="5min", tz=NY))
    idx = pd.DatetimeIndex(idx)
    rs = rng.normal(0, 0.001, len(idx))
    rq = 1.2 * rs + rng.normal(0, 0.0003, len(idx))
    s, q = 500 * np.exp(np.cumsum(rs)), 400 * np.exp(np.cumsum(rq))
    df = pd.DataFrame({"q_open": q, "q_close": q, "s_open": s, "s_close": s}, index=idx)
    df["day"] = df.index.date
    return df


def test_hedge_ratio_uses_previous_sessions_only():
    df = frame()
    b = daily_betas(df, 20)
    assert b.iloc[:20].isna().all() and b.iloc[20] == pytest.approx(1.2, abs=0.05)
    changed = df.copy()
    last = changed.day == changed.day.iloc[-1]
    changed.loc[last, "q_close"] *= np.linspace(1, 1.5, last.sum())   # wild final day
    assert daily_betas(changed, 20).iloc[-1] == pytest.approx(b.iloc[-1])  # can't see its own day


def test_signals_have_no_look_ahead():
    df = frame()
    cfg = PairConfig("t")
    full = signals(df, cfg)
    part = signals(df.iloc[:-30], cfg)
    pd.testing.assert_series_equal(full.z.iloc[:-30], part.z)


def test_both_legs_fill_together_at_the_next_open_and_pay_costs_on_four_fills():
    df = frame()
    t = backtest(df, PairConfig("t"), notional=10_000)
    assert len(t) > 0
    tr = t.iloc[0]
    i = df.index.get_loc(tr.entry_ts)
    assert df.index[i - 1].date() == tr.entry_ts.date()          # entry is the candle after a signal
    gross_notional = 10_000 * (1 + abs(tr.beta))
    per_side = (leg_cost_bps("QQQ", False) + leg_cost_bps("SPY", False)) / 2
    assert tr.cost_usd == pytest.approx(gross_notional * 2 * per_side / 1e4, rel=0.15)
    assert (t.net_usd < t.gross_usd).all()
    assert (t.exit_ts.dt.date == t.entry_ts.dt.date).all()        # intraday only
