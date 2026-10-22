"""Indicator helpers. Every value at row i uses data up to and including candle i only."""
import numpy as np
import pandas as pd

NY = "America/New_York"
OPEN_MINUTE = 9 * 60 + 30


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    prev_close = df["close"].shift()
    true_range = pd.concat([df["high"] - df["low"], (df["high"] - prev_close).abs(),
                            (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    return true_range.rolling(length).mean()


def efficiency_ratio(close: pd.Series, length: int) -> pd.Series:
    """Kaufman efficiency ratio: net move / total path over `length` candles (0 = chop, 1 = trend)."""
    net = (close - close.shift(length)).abs()
    path = close.diff().abs().rolling(length).sum()
    return (net / path.replace(0, np.nan)).fillna(0.0)


def ny_index(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    return index.tz_convert(NY) if index.tz is not None else index


def htf_close_and_ema(df: pd.DataFrame, bar_minutes: int, htf_minutes: int, length: int) -> pd.DataFrame:
    """Higher-timeframe close and EMA, aligned so each candle only sees HTF candles that had
    fully closed by the time that candle closed (no look-ahead).

    HTF candles are built per session from 9:30 New York time, so a 30-minute candle covers
    9:30-10:00, 10:00-10:30, ..."""
    local = ny_index(df.index)
    day = pd.Series(local.date, index=df.index)
    minutes = pd.Series(local.hour * 60 + local.minute, index=df.index)
    bucket = (minutes - OPEN_MINUTE) // htf_minutes
    end = df.index + pd.Timedelta(minutes=bar_minutes)

    grouped = pd.DataFrame({"day": day, "bucket": bucket, "close": df["close"]})
    htf = grouped.groupby(["day", "bucket"], sort=True)["close"].last()
    # An HTF candle counts as closed at its scheduled end only, so an unfinished one is never used.
    # (On early-close days the final partial candle simply becomes visible the next session.)
    closed_at = pd.DatetimeIndex([pd.Timestamp(f"{d} 00:00", tz=NY)
                                  + pd.Timedelta(minutes=OPEN_MINUTE + (b + 1) * htf_minutes) for d, b in htf.index])
    closed_at = closed_at.tz_convert(df.index.tz) if df.index.tz is not None else closed_at.tz_localize(None)
    htf_frame = pd.DataFrame({"closed_at": closed_at, "htf_close": htf.to_numpy()})
    htf_frame = htf_frame.sort_values("closed_at")
    htf_frame["htf_ema"] = ema(htf_frame["htf_close"], length).to_numpy()
    left = pd.DataFrame({"end": end}).reset_index(drop=True)
    left["end"] = left["end"].astype(htf_frame["closed_at"].dtype)
    merged = pd.merge_asof(left, htf_frame, left_on="end", right_on="closed_at", direction="backward")
    return pd.DataFrame({"htf_close": merged["htf_close"].to_numpy(), "htf_ema": merged["htf_ema"].to_numpy()},
                        index=df.index)
