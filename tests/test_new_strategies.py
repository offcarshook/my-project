import copy

import numpy as np
import pandas as pd
import pytest

from bot.config import load_config
from bot.strategy import BUY, HOLD, SELL, IctSweepFvg, OpeningRangeBreakout, VwapReversion, build_strategy

NY = "America/New_York"
BASE = load_config("config.yaml")


def cfg(**changes):
    c = copy.deepcopy(BASE.strategy)
    c.filters.liquidity["enabled"] = False
    for section, values in changes.items():
        getattr(c, section).update(values)
    return c


def bars(rows, date="2026-01-05", start="09:30", vol=1e5):
    """rows: (open, high, low, close)."""
    idx = pd.date_range(f"{date} {start}", periods=len(rows), freq="5min", tz=NY).tz_convert("UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx).assign(volume=vol)


def flat_day(date, n=78, lo=99.0, hi=101.0):
    mid = (lo + hi) / 2
    rows = [(mid, mid + 0.2, mid - 0.2, mid)] * n
    rows[10] = (mid, hi, mid - 0.2, mid)
    rows[40] = (mid, mid + 0.2, lo, mid)
    return bars(rows, date)


def mirror(df, axis=200.0):
    return pd.DataFrame({"open": axis - df["open"], "high": axis - df["low"], "low": axis - df["high"],
                         "close": axis - df["close"], "volume": df["volume"]}, index=df.index)


# ---- opening range breakout ---------------------------------------------------
def orb_day():
    rows = [(100.5, 101, 100, 100.5)] * 3                       # 09:30-09:45 range 100-101
    rows += [(100.6, 100.9, 100.4, 100.8), (100.8, 101.6, 100.7, 101.5)]   # 09:50 bar breaks out
    rows += [(101.5, 101.7, 100.8, 100.9), (100.9, 101.8, 100.8, 101.6)]   # dips back in, breaks again
    rows += [(101.6, 101.6, 99.5, 99.8)]                        # 10:05 breaks the low
    rows += [(99.8, 100, 99.6, 99.9)] * 20
    return pd.concat([flat_day("2026-01-02"), bars(rows)])      # prior session gives the ATR history


P = 78   # candles in the prior session


def test_orb_buys_first_close_above_range_with_range_stop():
    df = OpeningRangeBreakout(cfg()).compute(orb_day())
    buys = df.index[df["signal"] == BUY]
    assert len(buys) == 1 and buys[0] == df.index[P + 4]      # cap of one long per day
    assert df["stop_dist"].iloc[P + 4] == pytest.approx(101.5 - 100)
    assert np.isnan(df["target_dist"].iloc[P + 4])
    assert df["signal"].iloc[P + 7] == SELL and df["cross"].iloc[P + 7] == -1


def test_orb_mid_stop_and_no_look_ahead():
    day = orb_day()
    full = OpeningRangeBreakout(cfg(orb={"stop": "mid"})).compute(day)
    part = OpeningRangeBreakout(cfg(orb={"stop": "mid"})).compute(day.iloc[:P + 6])
    assert full["stop_dist"].iloc[P + 4] == pytest.approx(101.5 - 100.5)
    pd.testing.assert_series_equal(full["signal"].iloc[:P + 6], part["signal"])
    assert full["or_high"].iloc[P:P + 3].isna().all()          # range unknown until it's complete


def test_orb_range_size_filter_and_last_entry():
    assert (OpeningRangeBreakout(cfg(orb={"max_range_pct": 0.5})).compute(orb_day())["signal"] == HOLD).all()
    late = OpeningRangeBreakout(cfg(orb={"last_entry": "10:00"})).compute(orb_day())
    assert late["signal"].iloc[P + 4] == BUY and late["signal"].iloc[P + 7] == HOLD


# ---- VWAP mean reversion ------------------------------------------------------------
def vwap_day():
    rows = [(100, 100.1, 99.9, 100)] * 30                        # quiet: VWAP ~100, tiny ATR
    rows += [(100, 100, 98.5, 98.6), (98.6, 98.7, 98.2, 98.3)]   # flush far below VWAP
    rows += [(98.3, 98.9, 98.3, 98.85)]                          # closes above the prior high
    rows += [(98.9, 99.5, 98.8, 99.4)] * 10
    return bars(rows)


def test_vwap_reversion_long_targets_vwap():
    df = VwapReversion(cfg()).compute(vwap_day())
    i = df.index.get_loc(df.index[df["signal"] == BUY][0])
    assert i == 32
    assert df["target_dist"].iloc[i] == pytest.approx(df["vwap"].iloc[i] - df["close"].iloc[i])
    assert df["stop_dist"].iloc[i] > 0 and df["target_dist"].iloc[i] >= df["stop_dist"].iloc[i]
    assert (df["cross"] == 0).all()


def test_vwap_reversion_short_is_the_mirror():
    df = VwapReversion(cfg()).compute(mirror(vwap_day()))
    assert df["signal"].iloc[32] == SELL


def test_vwap_reversion_needs_room_to_target():
    assert (VwapReversion(cfg(vwap={"min_rr": 10})).compute(vwap_day())["signal"] == HOLD).all()


# ---- ICT sweep + MSS + FVG ----------------------------------------------------------
def ict_days():
    prior = flat_day("2026-01-02", lo=99.0, hi=101.0)             # previous day low = 99
    rows = [(100, 100.2, 99.8, 100)] * 20
    rows += [(100, 100.3, 99.9, 100.1), (100.1, 100.5, 100.0, 100.2), (100.2, 100.3, 99.9, 100.0),
             (100.0, 100.2, 99.8, 99.9), (99.9, 100.1, 99.7, 99.8)]        # swing high 100.5 at bar 21
    rows += [(99.8, 99.85, 98.8, 99.3)]                          # 25: sweeps below 99, closes back above
    rows += [(99.3, 100.4, 99.25, 100.35)]                       # 26: displacement
    rows += [(100.35, 101.0, 99.95, 100.9)]                      # 27: closes above 100.5 (MSS); FVG 99.85-99.95
    rows += [(100.9, 101.0, 99.9, 100.1)]                        # 28: dips into the gap, closes above it -> BUY
    rows += [(100.1, 100.6, 100.0, 100.5)] * 10
    return pd.concat([prior, bars(rows)])


def test_ict_long_setup_fires_on_fvg_retest():
    df = IctSweepFvg(cfg()).compute(ict_days())
    buys = df.index[df["signal"] == BUY]
    assert len(buys) == 1
    i = df.index.get_loc(buys[0])
    assert df.index[i] == ict_days().index[78 + 28]
    sweep_low = 98.8
    assert df["stop_dist"].iloc[i] == pytest.approx(df["close"].iloc[i] - (sweep_low - 0.1 * df["atr"].iloc[i]))


def test_ict_short_is_the_mirror():
    df = IctSweepFvg(cfg()).compute(mirror(ict_days()))
    assert (df["signal"] == SELL).sum() == 1 and (df["signal"] == BUY).sum() == 0


def test_ict_gap_failure_cancels_setup():
    days = ict_days()
    j = 78 + 28
    days.iloc[j] = [100.9, 101.0, 99.0, 99.5, 1e5]              # closes below the gap instead
    assert (IctSweepFvg(cfg()).compute(days)["signal"] == HOLD).all()


def test_ict_no_look_ahead():
    days = ict_days()
    full = IctSweepFvg(cfg()).compute(days)
    part = IctSweepFvg(cfg()).compute(days.iloc[:78 + 29])
    pd.testing.assert_series_equal(full["signal"].iloc[:78 + 29], part["signal"])


def test_factory_builds_each_type():
    for t, cls in (("orb", OpeningRangeBreakout), ("vwap_reversion", VwapReversion), ("ict_fvg", IctSweepFvg)):
        c = cfg()
        c.type = t
        assert isinstance(build_strategy(c), cls)
