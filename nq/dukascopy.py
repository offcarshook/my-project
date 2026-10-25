"""Download Dukascopy 1-minute index-CFD candles and build 5-minute regular-session frames.

    python -m nq.dukascopy download --symbol USATECHIDXUSD --side BID --start 2016-01-01 --end 2026-10-23
    python -m nq.dukascopy build --symbol USATECHIDXUSD --out data/USATECH_5min.parquet

Source: https://datafeed.dukascopy.com/datafeed/<SYMBOL>/<YYYY>/<MM-1>/<DD>/<SIDE>_candles_min_1.bi5
(LZMA "alone" files of 24-byte big-endian records: seconds from 00:00 UTC, open, close, low, high
as int / 1000 for indices, volume float). Files are cached under data/dukascopy/ (git-ignored);
requests are throttled and retried because the feed rate-limits.

USATECH.IDX is Dukascopy's Nasdaq-100 CFD: it tracks the cash index, not NQ futures (no basis,
no rolls), and its volume is tick activity. Prices are used as-is (NDX is at NQ's price level)
and rounded to the 0.25 NQ tick for MNQ simulation.
"""
import argparse
import datetime as dt
import lzma
import os
import struct
import subprocess
import time

import numpy as np
import pandas as pd

BASE = "https://datafeed.dukascopy.com/datafeed"
CACHE = "data/dukascopy"
NY = "America/New_York"


def scale_of(symbol: str) -> float:
    """Price divisor of Dukascopy's integer prices (verified on samples: USATECH 16674229 ->
    16674.229, USDJPY 150499 -> 150.499, XAUUSD 2096485 -> 2096.485, GBPJPY 190822 -> 190.822)."""
    if "IDX" in symbol or symbol.endswith("JPY") or symbol.startswith(("XAU", "XAG")):
        return 1000.0
    return 100000.0


def day_path(symbol, side, day: dt.date) -> str:
    return f"{CACHE}/{symbol}/{side}/{day:%Y}/{day:%Y-%m-%d}.bi5"


def month_path(symbol, side, month: dt.date) -> str:
    return f"{CACHE}/{symbol}/{side}_hour/{month:%Y-%m}.bi5"


def fetch_day(symbol, side, day: dt.date, pause=6.0, retries=8) -> bool:
    """Download one day's 1-minute candles to the cache. True if a file is present afterwards."""
    url = f"{BASE}/{symbol}/{day.year}/{day.month - 1:02d}/{day.day:02d}/{side}_candles_min_1.bi5"
    return _fetch(url, day_path(symbol, side, day), pause, retries)


def fetch_month(symbol, side, month: dt.date, pause=6.0, retries=8) -> bool:
    """Download one month's 1-hour candles (one file per month)."""
    url = f"{BASE}/{symbol}/{month.year}/{month.month - 1:02d}/{side}_candles_hour_1.bi5"
    return _fetch(url, month_path(symbol, side, month), pause, retries)


def _fetch(url, path, pause, retries) -> bool:
    if os.path.exists(path):
        return True
    os.makedirs(os.path.dirname(path), exist_ok=True)
    wait = 20.0
    for _ in range(retries):
        tmp = path + ".part"
        r = subprocess.run(["curl", "-sS", "--max-time", "60", "-o", tmp, "-w", "%{http_code}", url],
                           capture_output=True, text=True)
        code = r.stdout.strip()
        time.sleep(pause)
        if code == "200" and os.path.getsize(tmp) > 0:
            head = open(tmp, "rb").read(1)
            if head != b"{":                                  # JSON body = rate-limit message
                os.replace(tmp, path)
                return True
        if code == "404":
            open(path, "wb").close()                          # no data that day (holiday/weekend)
            return True
        time.sleep(wait)                                      # rate-limited or dropped: back off
        wait = min(wait * 1.5, 120)
    return False


def decode(path: str, day, scale: float) -> pd.DataFrame:
    """Records' first field is seconds from the file's start (00:00 UTC of the day, or of the
    month's first day for hourly files)."""
    data = open(path, "rb").read()
    if not data:
        return pd.DataFrame()
    raw = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE).decompress(data)
    n = len(raw) // 24
    rec = np.array(struct.unpack(">" + "5if" * n, raw[: n * 24]), dtype=float).reshape(n, 6)
    idx = pd.Timestamp(day, tz="UTC") + pd.to_timedelta(rec[:, 0], unit="s")
    return pd.DataFrame({"open": rec[:, 1] / scale, "close": rec[:, 2] / scale, "low": rec[:, 3] / scale,
                         "high": rec[:, 4] / scale, "volume": rec[:, 5]}, index=idx)


def build(symbol, side="BID", start=None, end=None) -> pd.DataFrame:
    """Cached 1-minute candles -> 5-minute candles (index = candle START, New York time),
    regular NYSE session only (09:30 to the close, early closes and holidays from the exchange
    calendar), dropping flat no-trade minutes."""
    import pandas_market_calendars as mcal
    scale = scale_of(symbol)
    root = f"{CACHE}/{symbol}/{side}"
    frames = []
    for year in sorted(os.listdir(root)):
        for f in sorted(os.listdir(f"{root}/{year}")):
            if not f.endswith(".bi5"):
                continue
            day = dt.date.fromisoformat(f[:10])
            if (start and day < dt.date.fromisoformat(start)) or (end and day > dt.date.fromisoformat(end)):
                continue
            d = decode(f"{root}/{year}/{f}", day, scale)
            if len(d):
                frames.append(d)
    m1 = pd.concat(frames).sort_index()
    m1 = m1[~m1.index.duplicated()]
    sched = mcal.get_calendar("XNYS").schedule(start_date=m1.index[0].date(), end_date=m1.index[-1].date())
    keep = np.zeros(len(m1), dtype=bool)
    for _, row in sched.iterrows():                           # market_open/market_close in UTC
        keep |= (m1.index >= row.market_open) & (m1.index < row.market_close)
    m1 = m1[keep].tz_convert(NY)
    m5 = m1.resample("5min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    return m5


def build_hourly(symbol, side="BID") -> pd.DataFrame:
    """Cached monthly 1-hour candles -> one frame (index = candle START, UTC), flat
    zero-volume hours (market closed) dropped."""
    root = f"{CACHE}/{symbol}/{side}_hour"
    frames = [decode(f"{root}/{f}", dt.date.fromisoformat(f[:7] + "-01"), scale_of(symbol))
              for f in sorted(os.listdir(root)) if f.endswith(".bi5")]
    h = pd.concat([f for f in frames if len(f)]).sort_index()
    return h[(h.volume > 0) | (h.high > h.low)]


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("stage", choices=["download", "download-hourly", "build", "build-hourly"])
    p.add_argument("--symbol", default="USATECHIDXUSD")
    p.add_argument("--side", default="BID")
    p.add_argument("--start", default="2016-01-01")
    p.add_argument("--end", default=dt.date.today().isoformat())
    p.add_argument("--every", type=int, default=1, help="download: every Nth weekday (e.g. ASK samples)")
    p.add_argument("--out")
    a = p.parse_args(argv)
    if a.stage == "download":
        days = [d.date() for d in pd.bdate_range(a.start, a.end)][::a.every]
        failed = []
        for i, d in enumerate(days):
            if not fetch_day(a.symbol, a.side, d):
                failed.append(d)
            if i % 50 == 0:
                print(f"{a.side} {d} ({i + 1}/{len(days)}), failed so far {len(failed)}", flush=True)
        print(f"done: {len(days) - len(failed)}/{len(days)} days; failed: {failed[:10]}")
    elif a.stage == "download-hourly":
        months = [d.date() for d in pd.date_range(a.start, a.end, freq="MS")]
        failed = [m for m in months if not fetch_month(a.symbol, a.side, m)]
        print(f"{a.symbol} hourly: {len(months) - len(failed)}/{len(months)} months; failed: {failed[:10]}", flush=True)
    elif a.stage == "build-hourly":
        h = build_hourly(a.symbol, a.side)
        h.to_parquet(a.out)
        print(f"{len(h):,} hourly candles {h.index[0]} .. {h.index[-1]} -> {a.out}")
    else:
        m5 = build(a.symbol, a.side, a.start, a.end)
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        m5.to_parquet(a.out)
        print(f"{len(m5):,} candles {m5.index[0]} .. {m5.index[-1]} -> {a.out}")


if __name__ == "__main__":
    main()
