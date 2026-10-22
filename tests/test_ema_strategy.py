import numpy as np
import pandas as pd
import pytest

from bot.config import FilterConfig, StopConfig, StrategyConfig
from bot.indicators import efficiency_ratio, htf_close_and_ema
from bot.strategy import BUY, HOLD, SELL, EmaCross, stop_distance


def candles(closes, start="2026-01-05 09:30", vol=1e5):
    idx = pd.date_range(start, periods=len(closes), freq="5min", tz="America/New_York").tz_convert("UTC")
    c = np.asarray(closes, dtype=float)
    o = np.concatenate([[c[0]], c[:-1]])
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.05, "low": np.minimum(o, c) - 0.05,
                         "close": c, "volume": vol}, index=idx)


def cfg(**filters):
    f = FilterConfig()
    for name, values in filters.items():
        setattr(f, name, {**getattr(f, name), **values})
    return StrategyConfig(fast=3, slow=6, filters=f)


DOWN_THEN_UP = list(np.linspace(110, 100, 20)) + list(np.linspace(100.5, 110, 20))


def test_cross_up_gives_buy_on_the_crossing_candle_only():
    df = EmaCross(cfg()).compute(candles(DOWN_THEN_UP))
    buys = df.index[df["signal"] == BUY]
    assert len(buys) == 1
    i = df.index.get_loc(buys[0])
    assert df["ema_fast"].iloc[i] > df["ema_slow"].iloc[i] and df["ema_fast"].iloc[i - 1] <= df["ema_slow"].iloc[i - 1]


def test_cross_down_gives_sell():
    df = EmaCross(cfg()).compute(candles([220 - p for p in DOWN_THEN_UP]))
    assert (df["signal"] == SELL).sum() == 1 and (df["signal"] == BUY).sum() == 0


def test_no_look_ahead_signals_unchanged_when_future_candles_added():
    full = EmaCross(cfg()).compute(candles(DOWN_THEN_UP + list(np.linspace(110, 95, 15))))
    part = EmaCross(cfg()).compute(candles(DOWN_THEN_UP))
    pd.testing.assert_series_equal(full["signal"].iloc[:len(part)], part["signal"])


def test_entry_window_blocks_early_crosses():
    # same pattern at 06:00, so the cross lands outside 09:45-15:30
    df = EmaCross(cfg()).compute(candles(DOWN_THEN_UP, start="2026-01-05 06:00"))
    assert (df["cross"] == 1).sum() == 1 and (df["signal"] == HOLD).all()


def test_liquidity_filter_uses_feed_specific_floor():
    thin = candles(DOWN_THEN_UP, vol=10)  # ~$1k per candle
    f = {"liquidity": {"enabled": True, "min_dollar_volume_sip": 250_000, "min_dollar_volume_iex": 500}}
    assert (EmaCross(cfg(**f), data_feed="sip").compute(thin)["signal"] == HOLD).all()
    assert (EmaCross(cfg(**f), data_feed="iex").compute(thin)["signal"] == BUY).sum() == 1


def test_chop_filter_blocks_noisy_cross():
    zigzag = [100 + (1 if i % 2 else -1) * 0.8 + i * 0.01 for i in range(40)]
    base = EmaCross(cfg()).compute(candles(zigzag))
    chop = EmaCross(cfg(chop={"enabled": True, "period": 10, "min_efficiency": 0.3})).compute(candles(zigzag))
    assert (base["signal"] != HOLD).sum() > 0 and (chop["signal"] == HOLD).all()


def test_htf_trend_filter_blocks_counter_trend_buy():
    # long decline over prior session, then a small bounce that crosses up on 5-min only
    prior = candles(list(np.linspace(130, 110, 78)), start="2026-01-02 09:30")
    today = candles(DOWN_THEN_UP[:30], start="2026-01-05 09:30")  # bounce stays far below the 30-min EMA
    df_all = pd.concat([prior, today])
    base = EmaCross(cfg()).compute(df_all)
    filt = EmaCross(cfg(htf_trend={"enabled": True, "minutes": 30, "ema": 20})).compute(df_all)
    assert (base["signal"] == BUY).sum() >= 1
    assert (filt["signal"] == BUY).sum() == 0


def test_atr_band_filter():
    df = candles(DOWN_THEN_UP)
    f = {"atr_band": {"enabled": True, "period": 3, "lookback_bars": 60, "min_ratio": 5.0, "max_ratio": 9.0}}
    out = EmaCross(cfg(**f)).compute(df)
    assert (out["signal"] == HOLD).all()  # nothing is 5x its own median


def test_htf_alignment_only_uses_closed_candles():
    df = candles(np.arange(12.0))
    htf = htf_close_and_ema(df, 5, 30, 3)
    assert htf["htf_close"].iloc[:5].isna().all()       # 9:30-9:55 candles: 30-min candle not closed yet
    assert htf["htf_close"].iloc[5] == 5                 # 9:55 candle closes at 10:00
    assert (htf["htf_close"].iloc[6:11] == 5).all()      # 10:00-10:30 candle still open


def test_efficiency_ratio_extremes():
    assert efficiency_ratio(pd.Series(np.arange(20.0)), 5).iloc[-1] == pytest.approx(1)
    assert efficiency_ratio(pd.Series([1.0, 2.0] * 10), 6).iloc[-1] == 0


def test_stop_distance_atr_clamped_and_fixed():
    stop = StopConfig(type="atr", atr_mult=1.5, min_pct=0.2, max_pct=1.0)
    assert stop_distance(100, 0.5, stop) == pytest.approx(0.75)
    assert stop_distance(100, 0.01, stop) == pytest.approx(0.2)    # min 0.2%
    assert stop_distance(100, 5.0, stop) == pytest.approx(1.0)     # max 1%
    assert stop_distance(100, 5.0, StopConfig(type="fixed", fixed_pct=0.3)) == pytest.approx(0.3)


def test_atr_and_efficiency_ignore_the_overnight_gap():
    from bot.indicators import atr
    prior = candles([100.0] * 20, start="2026-01-02 15:00")
    zigzag = [101 + (0.05 if i % 2 else -0.05) for i in range(20)]   # +1% gap, then pure chop
    df = pd.concat([prior, candles(zigzag, start="2026-01-05 09:30")])
    a = atr(df, 14)
    first_today = df.index.get_loc(candles(zigzag).index[0])
    assert a.iloc[first_today + 5] < 0.3                                   # no 1-point gap in the average
    er = efficiency_ratio(df["close"], 12)
    assert (er.iloc[first_today:first_today + 12] == 0).all()             # no verdict before 12 candles
    assert er.iloc[-1] < 0.3                                               # chop reads as chop


def test_regime_high_vol_uses_only_past_volatility():
    from bot import report
    calm = list(100 + np.sin(np.arange(60)) * 0.2)
    wild = list(100 + np.sin(np.arange(60)) * 5)
    close = pd.Series(calm + wild, index=pd.date_range("2026-01-01", periods=120).date)
    early = report.regimes(close.iloc[:60])
    full = report.regimes(close)
    common = early.index.intersection(full.index)
    assert (early.loc[common, "volatility"] == full.loc[common, "volatility"]).all()
