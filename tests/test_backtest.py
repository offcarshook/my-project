import pandas as pd
import pytest

from bot.backtest import buy_and_hold, max_drawdown, run_backtest, run_challenges
from bot.book import ChallengeRules, RiskConfig

NO_SLIP = RiskConfig(slippage_pct=0)


def bars(prices, freq="B"):
    idx = pd.date_range("2024-01-01", periods=len(prices), freq=freq, tz="UTC")
    return pd.DataFrame({"close": prices}, index=idx)


ROUND_TRIP = [100] * 6 + [95, 90, 85] + [110, 130, 150] + [90, 60, 40]


def test_round_trip_trade_and_metrics():
    res = run_backtest({"QQQ": bars(ROUND_TRIP)}, 2, 4, "2024-01-01", 1000, NO_SLIP)
    assert list(res.trades["side"]) == ["BUY", "SELL"]
    buy, sell = res.trades.iloc[0], res.trades.iloc[1]
    assert sell["pnl"] == pytest.approx((sell["price"] - buy["price"]) * buy["qty"])
    assert res.metrics["closed_trades"] == 1
    assert res.metrics["final_value"] == pytest.approx(res.equity.iloc[-1])


def test_shorting_profits_from_crash():
    risk = RiskConfig(allow_short=True, slippage_pct=0)
    res = run_backtest({"QQQ": bars(ROUND_TRIP)}, 2, 4, "2024-01-01", 1000, risk)
    assert list(res.trades["side"]) == ["BUY", "SELL", "SHORT"]
    assert res.equity.iloc[-1] > 1000


def test_budget_split_and_no_overspend():
    up = [100] * 6 + [90, 80, 120, 140, 160]
    res = run_backtest({"A": bars(up), "B": bars(up)}, 2, 4, "2024-01-01", 1000, NO_SLIP)
    buys = res.trades[res.trades["side"] == "BUY"]
    assert len(buys) == 2
    assert (buys["qty"] * buys["price"]).sum() <= 1000


def test_start_date_excludes_warmup():
    df = bars(ROUND_TRIP)
    res = run_backtest({"QQQ": df}, 2, 4, df.index[-2], 1000, NO_SLIP)
    assert len(res.equity) == 2


def test_challenge_windows_report_pass_and_fail():
    # dip then steady rally: a challenge starting before the cross up passes with leverage
    up = bars([100 * 1.01 ** i for i in range(120)])
    up.iloc[:10, 0] = 100 - pd.Series(range(10)).values
    risk = RiskConfig(leverage=4, slippage_pct=0)
    rules = ChallengeRules(profit_target_pct=8, max_daily_loss_pct=5, max_drawdown_pct=10)
    out = run_challenges({"A": up}, 2, 4, up.index[8], 1000, risk, rules, window_days=30, every_n_days=200)
    assert out.iloc[0]["result"] == "PASSED" and out.iloc[0]["days"] < 30

    # a window starting mid-trend never sees a fresh cross, so it runs out of time
    out = run_challenges({"A": up}, 2, 4, up.index[40], 1000, risk, rules, window_days=30, every_n_days=200)
    assert out.iloc[0]["result"] == "TIMEOUT"

    # cross up, then a 3%-a-day slide at 2x leverage blows the 5% daily limit
    down = bars([100] * 5 + [90] + [120 * 0.97 ** i for i in range(60)])
    out = run_challenges({"A": down}, 2, 4, down.index[0], 1000, RiskConfig(leverage=2, slippage_pct=0), rules, window_days=30, every_n_days=200)
    assert out.iloc[0]["result"] == "FAILED" and out.iloc[0]["reason"] == "daily loss limit hit"


def test_buy_and_hold_and_drawdown():
    close = bars([100, 120, 60, 90])["close"]
    curve = buy_and_hold(close, close.index[0], starting_cash=1000)
    assert list(curve) == [1000, 1200, 600, 900]
    assert max_drawdown(curve) == pytest.approx(-50)
