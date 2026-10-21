import pandas as pd
import pytest

from bot.backtest import buy_and_hold, max_drawdown, run_backtest


def bars(prices):
    idx = pd.bdate_range("2024-01-01", periods=len(prices))
    return pd.DataFrame({"close": prices}, index=idx)


def test_round_trip_trade_and_metrics():
    # warm-up flat, dip, rally (cross up -> buy), crash (cross down -> sell)
    prices = [100] * 6 + [95, 90, 85] + [110, 130, 150] + [90, 60, 40]
    res = run_backtest({"QQQ": bars(prices)}, 2, 4, "2024-01-01", starting_cash=1000, slippage_pct=0)
    assert list(res.trades["side"]) == ["BUY", "SELL"]
    buy, sell = res.trades.iloc[0], res.trades.iloc[1]
    assert sell["pnl"] == pytest.approx((sell["price"] - buy["price"]) * buy["qty"])
    assert res.metrics["closed_trades"] == 1
    assert res.metrics["final_value"] == pytest.approx(res.equity.iloc[-1])


def test_budget_split_and_no_overspend():
    up = [100] * 6 + [90, 80, 120, 140, 160]
    res = run_backtest({"A": bars(up), "B": bars(up)}, 2, 4, "2024-01-01",
                       starting_cash=1000, slippage_pct=0)
    buys = res.trades[res.trades["side"] == "BUY"]
    assert len(buys) == 2
    assert (buys["qty"] * buys["price"]).sum() <= 1000


def test_start_date_excludes_warmup_trades():
    prices = [100] * 6 + [95, 90, 85] + [110, 130, 150] + [90, 60, 40]
    df = bars(prices)
    res = run_backtest({"QQQ": df}, 2, 4, df.index[-2], starting_cash=1000, slippage_pct=0)
    assert len(res.equity) == 2
    assert res.trades.empty or (res.trades["date"] >= df.index[-2]).all()


def test_buy_and_hold_and_drawdown():
    close = bars([100, 120, 60, 90])["close"]
    curve = buy_and_hold(close, close.index[0], starting_cash=1000)
    assert list(curve) == [1000, 1200, 600, 900]
    assert max_drawdown(curve) == pytest.approx(-50)
