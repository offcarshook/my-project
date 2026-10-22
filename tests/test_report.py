import numpy as np
import pandas as pd
import pytest

from bot import report
from bot.pipeline import score


def trades(pnls, risk=100.0):
    return pd.DataFrame({"pnl": pnls, "r": np.array(pnls) / risk, "fees": 1.0, "notional": 1000.0,
                         "symbol": "QQQ"})


def test_trade_stats():
    m = report.trade_stats(trades([200, -100, -100, 300, -100]), sessions=5, starting_cash=10_000)
    assert m["trades"] == 5 and m["trades_per_session"] == 1
    assert m["win_rate_pct"] == 40 and m["avg_win"] == 250 and m["avg_loss"] == -100
    assert m["profit_factor"] == pytest.approx(500 / 300)
    assert m["net_return_pct"] == 2 and m["expectancy_r"] == pytest.approx(0.4)
    assert m["longest_losing_streak"] == 2


def test_curve_stats_daily_and_overall_drawdown():
    idx = pd.to_datetime(["2026-01-05 15:00", "2026-01-05 16:00", "2026-01-06 15:00", "2026-01-06 16:00"], utc=True)
    eq = pd.DataFrame({"equity": [10_000, 10_500, 10_400, 9_900], "worst": [10_000, 10_400, 10_300, 9_800],
                       "positions": [1, 1, 0, 1]}, index=idx)
    m = report.curve_stats(eq, trades([1]), 10_000)
    assert m["max_drawdown_pct"] == pytest.approx((9_800 - 10_500) / 10_500 * 100)
    assert m["max_daily_drawdown_pct"] == pytest.approx((9_800 - 10_500) / 10_500 * 100)  # from prior close
    assert m["exposure_pct"] == 75


def test_selection_rejects_losers_and_rare_traders_and_penalizes_filters():
    good = {"trades": 500, "trades_per_session": 5, "expectancy_r": 0.1, "profit_factor": 1.2, "r_tstat": 2.0}
    bad = {**good, "expectancy_r": -0.01, "profit_factor": 0.98}
    rare = {**good, "trades_per_session": 1}
    assert score(good, bad, 0, 2)[0] == -np.inf
    assert score(rare, good, 0, 2)[1].startswith("too few trades")
    assert score(good, {**good, "r_tstat": 1.0}, 0, 2)[0] == 1.0
    assert score(good, good, 2, 2)[0] == pytest.approx(2.0 - 0.3)


def test_challenge_summary():
    w = pd.DataFrame({"result": ["PASSED", "FAILED", "FAILED", "TIMEOUT"],
                      "reason": ["profit target", "daily loss limit hit", "max total loss hit", "out of time"],
                      "trading_days": [10, 3, 15, 21]})
    s = report.challenge_summary(w)
    assert s["pass_pct"] == 25 and s["daily_breach_pct"] == 25 and s["total_breach_pct"] == 25
    assert s["median_days_to_pass"] == 10 and s["avg_days_to_fail"] == 9


def test_regime_labels_use_only_prior_days():
    close = pd.Series(np.linspace(100, 130, 40), index=pd.date_range("2026-01-01", periods=40).date)
    labels = report.regimes(close, lookback=10)
    assert (labels["trend"] == "trending up").all()
    assert labels.index[0] == close.index[11]  # first label needs 10 prior returns, shifted a day


def test_fmt_table_handles_specials():
    out = report.fmt_table(pd.DataFrame({"a": [1.0, np.nan, -np.inf, 0.123]}))
    assert "| 1 |" in out and "—" in out and "rejected" in out and "0.12" in out


def test_selection_rejects_too_many_trades():
    good = {"trades": 500, "trades_per_session": 5, "expectancy_r": 0.1, "profit_factor": 1.2, "r_tstat": 2.0}
    churn = {**good, "trades_per_session": 18}
    assert score(churn, good, 0, 4, 15)[1].startswith("too many trades")


def test_choose_risk_gates_on_fail_rate_and_luck_baseline():
    from bot.pipeline import choose_risk
    rows = [
        {"risk_pct": 0.25, "train_pass_pct": 5, "validation_pass_pct": 6, "train_fail_pct": 0, "validation_fail_pct": 0, "edge_margin_pct": 4},
        {"risk_pct": 0.5, "train_pass_pct": 30, "validation_pass_pct": 25, "train_fail_pct": 2, "validation_fail_pct": 4, "edge_margin_pct": 15},
        {"risk_pct": 0.75, "train_pass_pct": 45, "validation_pass_pct": 40, "train_fail_pct": 18, "validation_fail_pct": 9, "edge_margin_pct": 20},
    ]
    risk, note = choose_risk(rows, 0.25, max_fail_pct=10, min_margin=10)
    assert risk == 0.5                                  # 0.75 blows up too often
    risk, note = choose_risk(rows, 0.25, max_fail_pct=10, min_margin=50)
    assert risk == 0.25 and "stays" in note             # nothing beats luck by 50 points


def test_variants_include_config_as_is_and_keep_liquidity():
    from bot.config import load_config
    from bot.pipeline import variants
    cfg = load_config("config.yaml")
    v = variants(cfg)
    first = next(iter(v))
    assert first == "config.yaml as-is" and v[first].strategy == cfg.strategy and v[first].risk == cfg.risk
    for name, c in v.items():
        assert c.strategy.filters.liquidity["enabled"] == cfg.strategy.filters.liquidity["enabled"], name
    wide = v["EMA 9/21 wide stop"].risk.stop
    assert wide.atr_mult == 3.0 and wide.min_pct == 0.30
    assert len(v) <= 10


def test_luck_baseline_removes_the_edge():
    from bot.config import load_config
    from bot.pipeline import luck_baseline
    cfg = load_config("config.yaml")
    cfg.backtest.monte_carlo_runs = 500
    idx = pd.date_range("2026-01-05 15:00", periods=60, freq="D", tz="UTC")
    eq = pd.Series(25_000.0, index=idx)
    t = pd.DataFrame({"exit_ts": idx, "pnl": [150.0, -50.0] * 30})   # strong edge
    real, zero = luck_baseline(t, eq, 25_000, cfg)
    assert real.pass_rate > zero.pass_rate


def test_cap_hit_share():
    t = pd.DataFrame({"entry_ts": pd.to_datetime(["2026-01-05 15:00"] * 3 + ["2026-01-06 15:00"], utc=True)})
    assert report.cap_hit_pct(t, sessions=2, cap=3) == 50
