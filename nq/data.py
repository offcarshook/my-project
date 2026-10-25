"""Proxy futures bars built from ETF candles.

No NQ/ES futures data is available here, so MNQ/MES are proxied from QQQ/SPY 5-minute
regular-hours candles scaled to index-futures price levels (NQ ~ 41 x QQQ, ES ~ 10 x SPY) and
rounded to the 0.25 tick. This keeps intraday shape and timing but NOT: overnight (Globex)
trading, futures-specific liquidity and volume, roll effects, or the basis to the ETF. Results
on this proxy are a first look, not futures evidence.

Bars are stamped at their CLOSE (NautilusTrader's convention), so a strategy only ever sees a
completed candle."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from nautilus_trader.model.data import BarType
from nautilus_trader.persistence.wranglers import BarDataWrangler

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot.data import load_local_bars, regular_hours, sessions_from_data  # noqa: E402

PROXY = {"MNQ": ("QQQ", 41.0), "MES": ("SPY", 10.0)}
BAR_MINUTES = 5


def bar_type(instrument, minutes: int = BAR_MINUTES) -> BarType:
    return BarType.from_str(f"{instrument.id}-{minutes}-MINUTE-LAST-EXTERNAL")


def proxy_frame(etf: pd.DataFrame, ratio: float) -> pd.DataFrame:
    """ETF candles (indexed by candle START) -> futures-scale candles indexed by candle CLOSE."""
    out = etf[["open", "high", "low", "close", "volume"]].copy()
    for col in ("open", "high", "low", "close"):
        out[col] = np.round(out[col] * ratio * 4) / 4          # 0.25 tick
    out["high"] = out[["open", "high", "close"]].max(axis=1)
    out["low"] = out[["open", "low", "close"]].min(axis=1)
    out.index = out.index + pd.Timedelta(minutes=BAR_MINUTES)
    out.index.name = "timestamp"
    return out


def load_proxy(data_dir: str, roots=("MNQ",)) -> dict[str, pd.DataFrame]:
    etfs = [PROXY[r][0] for r in roots]
    raw = load_local_bars(data_dir, etfs)
    sessions = sessions_from_data(raw)
    return {r: proxy_frame(regular_hours(raw[PROXY[r][0]], sessions), PROXY[r][1]) for r in roots}


def to_bars(frame: pd.DataFrame, instrument, minutes: int = BAR_MINUTES) -> list:
    # fresh writable column arrays: the wrangler's Cython code rejects pandas' read-only
    # copy-on-write buffers (e.g. after editing a copied frame)
    data = frame[["open", "high", "low", "close", "volume"]].copy()
    for col in data.columns:
        data[col] = data[col].to_numpy(dtype=float, copy=True)
    return BarDataWrangler(bar_type(instrument, minutes), instrument).process(data)


def load_mt_cfd(path: str, server_tz: str = "Europe/Athens", minutes: int = 15) -> pd.DataFrame:
    """MetaTrader CFD export (tab-separated DateTime/Open/High/Low/Close/Volume/TickVolume, server
    clock) -> regular-session candles indexed by candle CLOSE time in New York, prices rounded to
    the 0.25 NQ tick. Tick volume is used as volume."""
    import pandas_market_calendars as mcal
    d = pd.read_csv(path, sep="\t")
    idx = pd.to_datetime(d["DateTime"], format="%Y.%m.%d %H:%M:%S").dt.tz_localize(
        server_tz, ambiguous="NaT", nonexistent="NaT")
    df = pd.DataFrame({"open": d.Open, "high": d.High, "low": d.Low, "close": d.Close,
                       "volume": d.TickVolume.astype(float)}).set_index(pd.DatetimeIndex(idx))
    df = df[df.index.notna()].sort_index()
    df = df[~df.index.duplicated()].tz_convert("America/New_York")
    sched = mcal.get_calendar("XNYS").schedule(start_date=df.index[0].date(), end_date=df.index[-1].date())
    keep = np.zeros(len(df), dtype=bool)
    starts = df.index.tz_convert("UTC")
    for _, row in sched.iterrows():
        keep |= (starts >= row.market_open) & (starts < row.market_close)
    df = df[keep]
    for col in ("open", "high", "low", "close"):
        df[col] = np.round(df[col] * 4) / 4
    df["high"] = df[["open", "high", "close"]].max(axis=1)
    df["low"] = df[["open", "low", "close"]].min(axis=1)
    df.index = df.index + pd.Timedelta(minutes=minutes)
    df.index.name = "timestamp"
    return df