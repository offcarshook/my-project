import pandas as pd

from bot.strategy import BUY, HOLD, SELL, add_smas, crossovers, latest_signal


def frame(prices):
    return pd.DataFrame({"close": prices}, index=pd.date_range("2026-01-01", periods=len(prices), freq="15min"))


def test_buy_then_sell_on_crossovers():
    prices = [10] * 5 + [9, 8, 7] + [12, 15, 18] + [6, 4, 2]
    sig = crossovers(add_smas(frame(prices), 2, 4))
    assert BUY in sig.values and SELL in sig.values
    assert sig[sig == BUY].index[0] < sig[sig == SELL].index[-1]


def test_no_signal_without_enough_history():
    assert latest_signal(frame([1, 2, 3]), 2, 5) == HOLD


def test_latest_signal_detects_fresh_cross_up():
    prices = [10, 9, 8, 7, 6, 5, 20]
    assert latest_signal(frame(prices), 2, 4) == BUY
