"""Kronos trade selection (nq/kronos_bot/selection.py): filters, causality, train-only selection."""
import numpy as np
import pandas as pd
import pytest

from nq.kronos_bot.selection import (COST_PTS, assemble, bot_stats, direction, fast_extra, net, select,
                                     trade_returns, vol_mult)

NY = "America/New_York"


def synthetic(n=1400, seed=0):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2016-11-15", periods=n)
    ts = pd.DatetimeIndex([pd.Timestamp(f"{d.date()} 15:30", tz=NY) for d in days])
    k = rng.normal(0, 1e-3, n)
    realized = 0.5 * k + rng.normal(0, 2e-3, n)
    prob = np.round(np.clip(0.5 + 150 * k + rng.normal(0, 0.1, n), 0, 1) * 16) / 16
    feats = pd.DataFrame({"k_exp_ret": k, "k_prob_up": prob, "k_disp": 2e-3, "k_range": 4e-3, "k_median_ret": k},
                         index=ts)
    s = pd.DataFrame({"prev_close": 10000 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))}, index=days)
    return [(t, 2, r) for t, r in zip(ts, realized)], feats, pd.Series(10000.0, index=ts), s


def test_direction_confidence_and_move_filters():
    df = pd.DataFrame({"k_exp_ret": [2e-4, 2e-4, -2e-4, 1e-5, 0.0], "k_prob_up": [0.75, 0.4375, 0.25, 0.75, 0.5],
                       "price": 10000.0})
    assert direction(df).tolist() == [1, 1, -1, 1, 0]
    assert direction(df, conf=2 / 16).tolist() == [1, 0, -1, 1, 0]          # 0.4375 disagrees with a long
    move = 2e-4 * 10000 / COST_PTS                                           # exactly the first rows' move
    assert direction(df, move=move).tolist() == [1, 1, -1, 0, 0]
    assert direction(df, move=float("inf")).abs().sum() == 0


def test_context_features_use_only_earlier_decisions():
    points, feats, prices, s = synthetic(200)
    a = assemble(points, feats, prices, s)
    i = 120
    bumped = points.copy()
    bumped[i] = (points[i][0], 2, points[i][2] + 0.05)
    b = assemble(bumped, feats, prices, s)
    assert a["v20"].iloc[:i + 1].equals(b["v20"].iloc[:i + 1])               # today's outcome never used today
    assert a["v20"].iloc[i + 1] != b["v20"].iloc[i + 1]
    assert a["trend50"].iloc[:60].isna().sum() >= 50                          # needs 50 previous sessions


def test_vol_mult_only_reduces_exposure():
    df = pd.DataFrame({"v20": [np.nan, 0.5, 1.0, 2.0, 100.0]})
    m = vol_mult(df, 1.0)
    assert m.tolist() == [1.0, 1.0, 1.0, 0.5, 0.25]


def test_selection_ignores_everything_after_train():
    points, feats, prices, s = synthetic()
    df = assemble(points, feats, prices, s)
    a = select(df)
    later = df["period"] != "train"
    scrambled = df.copy()
    scrambled.loc[later, "realized"] = -scrambled.loc[later, "realized"] * 3
    scrambled.loc[later, "k_exp_ret"] = np.random.default_rng(1).normal(0, 1e-2, later.sum())
    b = select(scrambled)
    assert a["variants"] == b["variants"] and a["final"] == b["final"]
    assert a["ev_slope_b"] == b["ev_slope_b"] and a["v_ref"] == b["v_ref"]
    assert a["grid"].equals(b["grid"])
    assert a["variants"]["S"]["source"] in ("B", "C", "C-EV")


def test_net_books_cost_only_on_trades():
    df = pd.DataFrame({"realized": [0.01, -0.01, 0.02], "price": 10000.0})
    d = pd.Series([1.0, 1.0, 0.0])
    r = net(df, d, mult=0.5)
    c = COST_PTS / 10000
    assert r.tolist() == pytest.approx([(0.01 - c) * 0.5, (-0.01 - c) * 0.5, 0.0])


def test_fast_extra_drawdown_ci_and_months():
    df = pd.DataFrame({"date": pd.to_datetime(["2020-01-02", "2020-01-03", "2020-02-03", "2020-02-04"])})
    r = pd.Series([0.01, -0.03, 0.0, 0.01])
    d = pd.Series([1.0, 1.0, 0.0, -1.0])
    x = fast_extra(df, r, d)
    assert x["max_dd_pct"] == pytest.approx(-3.0)                       # peak +1% -> -2%
    assert x["month_mean_pct"] == pytest.approx((-2.0 + 1.0) / 2) and x["months_positive_pct"] == 50.0
    assert x["exp_bp_ci95_low"] < -33.3 < x["exp_bp_ci95_high"]         # mean of the 3 trades = -33.3 bp


def test_trade_returns_compound_and_stress():
    ts = pd.to_datetime(["2020-01-02 15:30", "2020-01-03 15:30", "2020-02-03 15:30"]).tz_localize(NY)
    trades = pd.DataFrame({"entry_ts": ts, "exit_ts": ts + pd.Timedelta(minutes=30), "pnl": [500.0, -250.0, 1000.0],
                           "risk": 250.0, "qty": 2.0})
    t = trade_returns(trades, cash=50_000)
    assert t["ret"].tolist() == pytest.approx([500 / 50_000, -250 / 50_500, 1000 / 50_250])
    st = bot_stats(t, "2020-01-01", "2020-03-31")
    assert st["net_return_pct"] == pytest.approx((51_250 / 50_000 - 1) * 100)
    assert st["pf"] == pytest.approx(1500 / 250) and st["trades"] == 3
    assert st["month_mean_pct"] == pytest.approx(((50_250 / 50_000 - 1) + 1000 / 50_250 + 0.0) / 3 * 100)
    s = trade_returns(trades, extra_cost=1.0, cash=50_000)
    assert s["pnl"].tolist() == [498.0, -252.0, 998.0]
