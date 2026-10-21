"""Market data: Alpaca REST API, or a synthetic demo feed when no keys are set."""
from datetime import datetime, time, timedelta, timezone
import zlib
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

from bot.config import Settings

DATA_URL = "https://data.alpaca.markets/v2/stocks/bars"
CLOCK_URL = "https://paper-api.alpaca.markets/v2/clock"
NY = ZoneInfo("America/New_York")

# Rough minutes per bar, used to size the history request.
TF_MINUTES = {"1Min": 1, "5Min": 5, "15Min": 15, "30Min": 30, "1Hour": 60, "1Day": 390}
COLUMNS = {"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}


class AlpacaData:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.headers = {
            "APCA-API-KEY-ID": settings.alpaca_key,
            "APCA-API-SECRET-KEY": settings.alpaca_secret,
        }

    def get_bars(self, symbols: list[str], limit: int) -> dict[str, pd.DataFrame]:
        """Return the latest `limit` close prices per symbol, indexed by timestamp."""
        tf = self.settings.timeframe
        # Ask for enough calendar days to cover `limit` bars of regular trading.
        trading_days = limit * TF_MINUTES.get(tf, 15) / 390
        start = datetime.now(timezone.utc) - timedelta(days=int(trading_days * 1.6) + 5)
        return {s: df.tail(limit) for s, df in self.get_history(symbols, start, tf).items()}

    def get_history(self, symbols: list[str], start: datetime,
                    timeframe: str = "1Day") -> dict[str, pd.DataFrame]:
        """Return every close price per symbol from `start` until now."""
        params = {
            "symbols": ",".join(symbols),
            "timeframe": timeframe,
            "start": start.isoformat(),
            "feed": self.settings.data_feed,
            "adjustment": "all",
            "limit": 10000,
        }
        rows: dict[str, list] = {s: [] for s in symbols}
        while True:
            resp = requests.get(DATA_URL, headers=self.headers, params=params, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
            for sym, bars in (payload.get("bars") or {}).items():
                rows.setdefault(sym, []).extend(bars)
            token = payload.get("next_page_token")
            if not token:
                break
            params["page_token"] = token

        out = {}
        for sym, bars in rows.items():
            if not bars:
                out[sym] = pd.DataFrame(columns=list(COLUMNS.values()))
                continue
            df = pd.DataFrame(bars)
            df["t"] = pd.to_datetime(df["t"])
            df = df.set_index("t")[list(COLUMNS)].rename(columns=COLUMNS)
            out[sym] = regular_hours(df) if timeframe != "1Day" else df
        return out

    def market_is_open(self) -> bool:
        resp = requests.get(CLOCK_URL, headers=self.headers, timeout=10)
        resp.raise_for_status()
        return bool(resp.json()["is_open"])


class DemoData:
    """Random-walk prices so the dashboard works before you have API keys."""

    BASE = {"QQQ": 480, "SPY": 560, "VOO": 515, "AAPL": 225, "MSFT": 430,
            "NVDA": 130, "AMZN": 190, "TSLA": 250, "TQQQ": 75, "SQQQ": 9, "SOXL": 30}

    def __init__(self, settings: Settings):
        self.settings = settings

    def get_bars(self, symbols: list[str], limit: int) -> dict[str, pd.DataFrame]:
        minutes = TF_MINUTES.get(self.settings.timeframe, 15)
        now = pd.Timestamp.now(tz="UTC").floor(f"{minutes}min")
        index = pd.date_range(end=now, periods=limit, freq=f"{minutes}min")
        bucket = int(now.timestamp() // (minutes * 60))
        out = {}
        for sym in symbols:
            # Seeded per symbol so history is stable; a new bar appears each period.
            rng = np.random.default_rng(zlib.crc32(sym.encode()))
            steps = rng.normal(0, 0.004, bucket % 100_000 + limit)[-limit:]
            out[sym] = _fake_candles(self.BASE.get(sym, 100), steps, index, rng)
        return out

    def get_history(self, symbols: list[str], start: datetime,
                    timeframe: str = "1Day") -> dict[str, pd.DataFrame]:
        """Random walk with a slight upward drift, from `start` until today.
        Intraday timeframes only produce regular-hours bars, like the real feed after filtering."""
        days = pd.bdate_range(start=pd.Timestamp(start).tz_convert(NY).normalize().tz_localize(None),
                              end=pd.Timestamp.now(tz=NY).normalize().tz_localize(None))
        if timeframe == "1Day":
            index, per_day = days.tz_localize("UTC"), 1
        else:
            minutes = TF_MINUTES.get(timeframe, 15)
            per_day = 390 // minutes
            offsets = pd.to_timedelta(np.arange(per_day) * minutes, unit="min") + pd.Timedelta("9h30min")
            local = (days.values[:, None] + offsets.values[None, :]).ravel()
            index = pd.DatetimeIndex(local).tz_localize(NY).tz_convert("UTC")
        out = {}
        for sym in symbols:
            rng = np.random.default_rng(zlib.crc32(sym.encode()) + per_day)
            steps = rng.normal(0.0004 / per_day, 0.016 / np.sqrt(per_day), len(index))
            base = self.BASE.get(sym, 100) * np.exp(-steps.sum())
            out[sym] = _fake_candles(base, steps, index, rng)
        return out

    def market_is_open(self) -> bool:
        return True


def _fake_candles(base: float, steps: np.ndarray, index: pd.DatetimeIndex, rng) -> pd.DataFrame:
    close = base * np.exp(np.cumsum(steps))
    open_ = np.concatenate([[base], close[:-1]])
    wick = np.abs(rng.normal(0, steps.std() or 0.001, len(close))) * close * 0.6
    return pd.DataFrame({
        "open": open_,
        "high": np.maximum(open_, close) + wick,
        "low": np.minimum(open_, close) - wick,
        "close": close,
        "volume": rng.lognormal(10, 0.5, len(close)).round(),
    }, index=index)


def regular_hours(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only bars that start between 9:30 and 16:00 New York time."""
    if df.empty:
        return df
    local = df.index.tz_convert(NY)
    mins = local.hour * 60 + local.minute
    return df[(mins >= 570) & (mins < 960)]


def local_market_is_open(now: datetime | None = None) -> bool:
    """Weekday 9:30-16:00 New York time. Ignores exchange holidays."""
    now = (now or datetime.now(timezone.utc)).astimezone(NY)
    return now.weekday() < 5 and time(9, 30) <= now.time() < time(16, 0)


def make_feed(settings: Settings):
    return AlpacaData(settings) if settings.has_alpaca_keys else DemoData(settings)
