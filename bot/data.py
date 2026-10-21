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
_TF_MINUTES = {"1Min": 1, "5Min": 5, "15Min": 15, "30Min": 30, "1Hour": 60, "1Day": 390}


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
        trading_days = limit * _TF_MINUTES.get(tf, 15) / 390
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
                out[sym] = pd.DataFrame(columns=["close"])
                continue
            df = pd.DataFrame(bars)
            df["t"] = pd.to_datetime(df["t"])
            out[sym] = df.set_index("t")[["c"]].rename(columns={"c": "close"})
        return out

    def market_is_open(self) -> bool:
        resp = requests.get(CLOCK_URL, headers=self.headers, timeout=10)
        resp.raise_for_status()
        return bool(resp.json()["is_open"])


class DemoData:
    """Random-walk prices so the dashboard works before you have API keys."""

    BASE = {"QQQ": 480, "SPY": 560, "VOO": 515, "AAPL": 225, "MSFT": 430,
            "NVDA": 130, "AMZN": 190, "TSLA": 250}

    def __init__(self, settings: Settings):
        self.settings = settings

    def get_bars(self, symbols: list[str], limit: int) -> dict[str, pd.DataFrame]:
        minutes = _TF_MINUTES.get(self.settings.timeframe, 15)
        now = pd.Timestamp.now(tz="UTC").floor(f"{minutes}min")
        index = pd.date_range(end=now, periods=limit, freq=f"{minutes}min")
        bucket = int(now.timestamp() // (minutes * 60))
        out = {}
        for sym in symbols:
            # Seeded per symbol so history is stable; a new bar appears each period.
            rng = np.random.default_rng(zlib.crc32(sym.encode()))
            steps = rng.normal(0, 0.004, bucket % 100_000 + limit)[-limit:]
            prices = self.BASE.get(sym, 100) * np.exp(np.cumsum(steps))
            out[sym] = pd.DataFrame({"close": prices}, index=index)
        return out

    def get_history(self, symbols: list[str], start: datetime,
                    timeframe: str = "1Day") -> dict[str, pd.DataFrame]:
        """Daily random walk with a slight upward drift, from `start` until today."""
        index = pd.bdate_range(start=pd.Timestamp(start).tz_convert("UTC").normalize(),
                               end=pd.Timestamp.now(tz="UTC").normalize())
        out = {}
        for sym in symbols:
            rng = np.random.default_rng(zlib.crc32(sym.encode()) + 1)
            steps = rng.normal(0.0004, 0.016, len(index))
            prices = self.BASE.get(sym, 100) * np.exp(np.cumsum(steps) - steps.sum())
            out[sym] = pd.DataFrame({"close": prices}, index=index)
        return out

    def market_is_open(self) -> bool:
        return True


def local_market_is_open(now: datetime | None = None) -> bool:
    """Weekday 9:30-16:00 New York time. Ignores exchange holidays."""
    now = (now or datetime.now(timezone.utc)).astimezone(NY)
    return now.weekday() < 5 and time(9, 30) <= now.time() < time(16, 0)


def make_feed(settings: Settings):
    return AlpacaData(settings) if settings.has_alpaca_keys else DemoData(settings)
