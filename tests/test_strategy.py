import numpy as np
import pandas as pd

from bot.strategy import BUY, HOLD, SELL, SmaCross, VwapPullback, add_smas, crossovers, latest_signal


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
    assert latest_signal(frame([10, 9, 8, 7, 6, 5, 20]), 2, 4) == BUY


def test_sma_strategy_has_no_own_stop():
    df = SmaCross(2, 4).compute(frame([10, 9, 8, 7, 6, 5, 20]))
    assert df["signal"].iloc[-1] == BUY and np.isnan(df["stop_dist"].iloc[-1])


def session(closes, start="2026-01-05 09:30"):
    """5-minute candles for one New York session, built from a close path."""
    idx = pd.date_range(start, periods=len(closes), freq="5min", tz="America/New_York").tz_convert("UTC")
    c = np.array(closes, dtype=float)
    o = np.concatenate([[c[0]], c[:-1]])
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.05, "low": np.minimum(o, c) - 0.05,
                         "close": c, "volume": 1000.0}, index=idx)


def uptrend_with_pullback():
    # steady climb, a 3-bar dip to the fast EMA, then a strong green bar
    climb = list(np.linspace(100, 106, 40))
    dip = [105.6, 105.3, 105.2]
    return climb + dip + [106.4]


def test_vwap_pullback_buys_the_dip_in_an_uptrend():
    df = VwapPullback(rvol_min=0).compute(session(uptrend_with_pullback()))
    last = df.iloc[-1]
    assert last["signal"] == BUY
    assert 0.5 * last["atr"] <= last["stop_dist"] <= 2 * last["atr"]
    assert (df["signal"].iloc[:-1] != SELL).all()


def test_vwap_pullback_shorts_the_mirror_image():
    mirror = [212 - p for p in uptrend_with_pullback()]
    df = VwapPullback(rvol_min=0).compute(session(mirror))
    assert df["signal"].iloc[-1] == SELL


def test_vwap_pullback_respects_time_window():
    # same setup but the trigger bar lands at 9:35, before the 9:45 start
    df = VwapPullback(rvol_min=0).compute(session(uptrend_with_pullback(), start="2026-01-05 06:00"))
    assert df["signal"].iloc[-1] == HOLD


def test_vwap_pullback_volume_filter():
    candles = session(uptrend_with_pullback())
    assert VwapPullback(rvol_min=1.5).compute(candles)["signal"].iloc[-1] == HOLD
    candles.iloc[-1, candles.columns.get_loc("volume")] = 5000
    assert VwapPullback(rvol_min=1.5).compute(candles)["signal"].iloc[-1] == BUY
