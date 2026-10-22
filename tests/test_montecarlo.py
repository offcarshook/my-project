import numpy as np
import pandas as pd
import pytest

from bot.montecarlo import daily_trade_returns, simulate_luck



class RULES:
    profit_target_pct, max_daily_loss_pct, max_drawdown_pct = 8, 5, 10


def test_steady_winner_always_passes_on_day_8():
    res = simulate_luck([np.array([0.01])], RULES, n_days=21, n_sims=200)
    assert res.pass_rate == 1 and (res.days_to_pass == 8).all()  # 1.01**8 > 1.08 > 1.01**7


def test_big_losing_day_fails_daily_limit():
    res = simulate_luck([np.array([-0.06])], RULES, n_sims=100)
    assert res.fail_rate == 1 and res.fail_reasons["daily loss limit hit"] == 100


def test_slow_bleed_fails_max_drawdown():
    res = simulate_luck([np.array([-0.02, -0.02])], RULES, n_sims=50)
    assert res.fail_reasons["max drawdown hit"] == 50


def test_no_trades_times_out():
    res = simulate_luck([np.array([])], RULES, n_sims=50)
    assert res.timeout_rate == 1 and (res.period_returns == 0).all()


def test_mixed_days_give_a_spread_and_rates_add_up():
    days = [np.array([0.01, 0.01]), np.array([-0.01]), np.array([])]
    res = simulate_luck(days, RULES, n_sims=2000)
    assert 0 < res.pass_rate < 1
    assert res.pass_rate + res.fail_rate + res.timeout_rate == pytest.approx(1)
    assert res.period_returns.std() > 0


def test_daily_trade_returns_uses_equity_before_the_trade():
    idx = pd.date_range("2026-01-05 15:00", periods=4, freq="1h", tz="UTC")  # 10:00-13:00 NY, one day
    equity = pd.Series([10_000, 10_000, 10_100, 10_100], index=idx)
    trades = pd.DataFrame({"ts": [idx[2]], "pnl": [100.0], "side": ["SELL"]})
    [day] = daily_trade_returns(trades, equity, 10_000)
    assert day == pytest.approx([0.01])
