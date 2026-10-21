"""Settings loaded from environment variables / .env file."""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

DEFAULT_WATCHLIST = ["QQQ", "SPY", "VOO", "AAPL", "MSFT", "NVDA", "AMZN", "TSLA"]


def _list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if not raw:
        return list(default)
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


@dataclass
class Settings:
    alpaca_key: str = field(default_factory=lambda: os.getenv("ALPACA_API_KEY", ""))
    alpaca_secret: str = field(default_factory=lambda: os.getenv("ALPACA_SECRET_KEY", ""))
    data_feed: str = field(default_factory=lambda: os.getenv("ALPACA_DATA_FEED", "iex"))
    watchlist: list[str] = field(default_factory=lambda: _list("WATCHLIST", DEFAULT_WATCHLIST))
    timeframe: str = field(default_factory=lambda: os.getenv("TIMEFRAME", "15Min"))
    short_window: int = field(default_factory=lambda: int(os.getenv("SHORT_WINDOW", "20")))
    long_window: int = field(default_factory=lambda: int(os.getenv("LONG_WINDOW", "50")))
    starting_cash: float = field(default_factory=lambda: float(os.getenv("STARTING_CASH", "10000")))
    slippage_pct: float = field(default_factory=lambda: float(os.getenv("SLIPPAGE_PCT", "0.05")))
    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", "paper_trades.db"))

    @property
    def has_alpaca_keys(self) -> bool:
        return bool(self.alpaca_key and self.alpaca_secret)
