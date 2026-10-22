"""Trading strategies. Each one adds a `signal` column (BUY / SELL / HOLD) and a `stop_dist`
column (price distance to the stop, or NaN to use the fixed stop % from the risk settings)."""
import numpy as np
import pandas as pd

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


def add_smas(df: pd.DataFrame, short: int, long: int) -> pd.DataFrame:
    df = df.copy()
    df["sma_short"] = df["close"].rolling(short).mean()
    df["sma_long"] = df["close"].rolling(long).mean()
    return df


def crossovers(df: pd.DataFrame) -> pd.Series:
    """BUY where short crosses above long, SELL where it crosses below, else HOLD."""
    above = df["sma_short"] > df["sma_long"]
    valid = df["sma_short"].notna() & df["sma_long"].notna()
    prev_above = above.shift(1)
    prev_valid = valid.shift(1, fill_value=False)
    signal = pd.Series(HOLD, index=df.index)
    ready = valid & prev_valid
    signal[ready & above & (prev_above == False)] = BUY  # noqa: E712
    signal[ready & ~above & (prev_above == True)] = SELL  # noqa: E712
    return signal


def latest_signal(df: pd.DataFrame, short: int, long: int) -> str:
    if len(df) < long + 1:
        return HOLD
    return crossovers(add_smas(df, short, long)).iloc[-1]


class SmaCross:
    """Buy when the short SMA crosses above the long one, sell when it crosses back below."""

    name = "SMA crossover"

    def __init__(self, short: int = 20, long: int = 50):
        self.short, self.long = short, long
        self.warmup = long + 1
        self.lines = {"sma_short": f"SMA{short}", "sma_long": f"SMA{long}"}

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        df = add_smas(df, self.short, self.long)
        df["signal"] = crossovers(df)
        df["stop_dist"] = np.nan
        return df


def _ny(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    return index.tz_convert("America/New_York") if index.tz is not None else index


class VwapPullback:
    """Intraday trend pullback.

    Long setup (shorts are the mirror image):
      1. Trend: price above session VWAP, fast EMA above slow EMA, slow EMA rising.
      2. Pullback: in the last few bars price dipped to the fast EMA.
      3. Trigger: a green bar that closes above the previous bar's high.
      4. Filters: volume at least `rvol_min` x its 20-bar average, and only between
         `start` and `end` New York time (skips the messy open and the last half hour).
    Stop goes just under the pullback low, clamped to 0.5-2 ATR.
    """

    name = "VWAP pullback"

    def __init__(self, fast: int = 9, slow: int = 21, atr_len: int = 14, rvol_min: float = 1.0,
                 lookback: int = 3, start: str = "09:45", end: str = "15:30"):
        self.fast, self.slow, self.atr_len = fast, slow, atr_len
        self.rvol_min, self.lookback = rvol_min, lookback
        self.start = int(start[:2]) * 60 + int(start[3:])
        self.end = int(end[:2]) * 60 + int(end[3:])
        self.warmup = max(slow * 3, atr_len + 20)
        self.lines = {"vwap": "VWAP", "ema_fast": f"EMA{fast}", "ema_slow": f"EMA{slow}"}

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        c = df["close"]
        o = df["open"] if "open" in df else c.shift().fillna(c)
        h = df["high"] if "high" in df else c
        lo = df["low"] if "low" in df else c
        v = df["volume"] if "volume" in df else pd.Series(1.0, index=df.index)

        local = _ny(df.index)
        day = pd.Series(local.date, index=df.index)
        typical = (h + lo + c) / 3
        df["vwap"] = (typical * v).groupby(day).cumsum() / v.groupby(day).cumsum().replace(0, np.nan)
        df["ema_fast"] = c.ewm(span=self.fast, adjust=False).mean()
        df["ema_slow"] = c.ewm(span=self.slow, adjust=False).mean()
        prev_c = c.shift()
        true_range = pd.concat([h - lo, (h - prev_c).abs(), (lo - prev_c).abs()], axis=1).max(axis=1)
        df["atr"] = true_range.rolling(self.atr_len).mean()
        rvol = v / v.rolling(20).mean()

        mins = pd.Series(local.hour * 60 + local.minute, index=df.index)
        ok = (mins >= self.start) & (mins < self.end) & (rvol >= self.rvol_min) & df["atr"].notna()
        ema_f, ema_s = df["ema_fast"], df["ema_slow"]
        up = (c > df["vwap"]) & (ema_f > ema_s) & (ema_s > ema_s.shift(3))
        down = (c < df["vwap"]) & (ema_f < ema_s) & (ema_s < ema_s.shift(3))
        dipped = (lo <= ema_f).astype(float).rolling(self.lookback).max() == 1
        popped = (h >= ema_f).astype(float).rolling(self.lookback).max() == 1
        buy = ok & up & dipped & (c > o) & (c > h.shift())
        sell = ok & down & popped & (c < o) & (c < lo.shift())

        atr = df["atr"]
        long_stop = (c - lo.rolling(self.lookback).min() + 0.1 * atr).clip(0.5 * atr, 2 * atr)
        short_stop = (h.rolling(self.lookback).max() - c + 0.1 * atr).clip(0.5 * atr, 2 * atr)
        df["signal"] = np.where(buy, BUY, np.where(sell, SELL, HOLD))
        df["stop_dist"] = np.where(buy, long_stop, np.where(sell, short_stop, np.nan))
        return df


def stop_distance(price, atr_value, stop) -> float | np.ndarray:
    """Stop distance in price units from a StopConfig: ATR-based (clamped to min/max % of
    price) or a fixed % of price."""
    if stop.type == "fixed":
        return price * stop.fixed_pct / 100
    return np.clip(stop.atr_mult * atr_value, price * stop.min_pct / 100, price * stop.max_pct / 100)


class EmaCross:
    """Prop Pass strategy: fast/slow EMA crossover on completed candles, plus optional
    lightweight filters (all off unless enabled in the config).

    Output columns:
      cross  +1 / -1 on the candle where fast crosses above / below slow (raw, unfiltered;
             used to exit on an opposite signal)
      signal BUY / SELL / HOLD: a cross that passed every enabled filter and whose next
             candle (where the order would execute) opens inside the entry window
      atr, stop_dist, plus each filter's inputs for inspection
    """

    name = "EMA crossover"

    def __init__(self, cfg, stop=None, bar_minutes: int = 5, data_feed: str = "sip"):
        self.cfg, self.stop = cfg, stop
        self.bar_minutes, self.data_feed = bar_minutes, data_feed
        f = cfg.filters
        self.warmup = max(cfg.slow * 4, f.atr_band["period"] + 1,
                          f.atr_band["lookback_bars"] if f.atr_band["enabled"] else 0,
                          f.htf_trend["ema"] * f.htf_trend["minutes"] // bar_minutes if f.htf_trend["enabled"] else 0)
        self.lines = {"ema_fast": f"EMA{cfg.fast}", "ema_slow": f"EMA{cfg.slow}"}

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        from bot.indicators import atr, efficiency_ratio, ema, htf_close_and_ema, ny_index

        cfg, f = self.cfg, self.cfg.filters
        df = df.copy()
        c = df["close"]
        df["ema_fast"], df["ema_slow"] = ema(c, cfg.fast), ema(c, cfg.slow)
        df["atr"] = atr(df, f.atr_band["period"])
        above = df["ema_fast"] > df["ema_slow"]
        prev = above.shift(1)
        ready = pd.Series(np.arange(len(df)) >= cfg.slow, index=df.index)
        cross = np.where(ready & above & (prev == False), 1, np.where(ready & ~above & (prev == True), -1, 0))  # noqa: E712
        df["cross"] = cross

        ok_long = pd.Series(True, index=df.index) & df["atr"].notna()
        ok_short = ok_long.copy()
        if f.htf_trend["enabled"]:
            htf = htf_close_and_ema(df, self.bar_minutes, f.htf_trend["minutes"], f.htf_trend["ema"])
            df["htf_close"], df["htf_ema"] = htf["htf_close"], htf["htf_ema"]
            ok_long &= df["htf_close"] > df["htf_ema"]
            ok_short &= df["htf_close"] < df["htf_ema"]
        if f.atr_band["enabled"]:
            ratio = df["atr"] / df["atr"].rolling(f.atr_band["lookback_bars"], min_periods=50).median()
            df["atr_ratio"] = ratio
            in_band = ratio.between(f.atr_band["min_ratio"], f.atr_band["max_ratio"])
            ok_long &= in_band
            ok_short &= in_band
        if f.liquidity["enabled"] and "volume" in df:
            floor = f.liquidity["min_dollar_volume_iex" if self.data_feed == "iex" else "min_dollar_volume_sip"]
            df["dollar_volume"] = c * df["volume"]
            liquid = df["dollar_volume"] >= floor
            ok_long &= liquid
            ok_short &= liquid
        if f.chop["enabled"]:
            df["efficiency"] = efficiency_ratio(c, f.chop["period"])
            trending = df["efficiency"] >= f.chop["min_efficiency"]
            ok_long &= trending
            ok_short &= trending

        # Orders execute when the signal candle closes, i.e. at the next candle's open.
        local_end = ny_index(df.index + pd.Timedelta(minutes=self.bar_minutes))
        hhmm = pd.Series(local_end.strftime("%H:%M"), index=df.index)
        in_window = (hhmm >= cfg.entry_start) & (hhmm < cfg.entry_end)
        buy = (df["cross"] == 1) & ok_long & in_window
        sell = (df["cross"] == -1) & ok_short & in_window
        df["signal"] = np.where(buy, BUY, np.where(sell, SELL, HOLD))
        df["stop_dist"] = stop_distance(c, df["atr"], self.stop) if self.stop is not None else np.nan
        return df


STRATEGIES = {SmaCross.name: SmaCross, VwapPullback.name: VwapPullback}
