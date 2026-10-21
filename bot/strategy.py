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


STRATEGIES = {SmaCross.name: SmaCross, VwapPullback.name: VwapPullback}
