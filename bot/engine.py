"""One live bot step: fetch prices, compute signals, let the Book trade."""
from dataclasses import dataclass, field
from datetime import datetime, time, timezone

import pandas as pd

from bot.book import Book, ChallengeRules, RiskConfig
from bot.data import NY, TF_MINUTES
from bot.paper import PaperAccount
from bot.strategy import HOLD


@dataclass
class TickResult:
    market_open: bool
    book: Book | None = None
    bars: dict[str, pd.DataFrame] = field(default_factory=dict)
    prices: dict[str, float] = field(default_factory=dict)
    signals: dict[str, str] = field(default_factory=dict)
    trades: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def run_tick(feed, account: PaperAccount, symbols: list[str], strategy, market_open: bool,
             risk: RiskConfig | None = None, rules: ChallengeRules | None = None,
             timeframe: str = "5Min", trade: bool = True, now: datetime | None = None) -> TickResult:
    risk, rules = risk or RiskConfig(), rules or ChallengeRules()
    now = (now or datetime.now(timezone.utc)).astimezone(NY)
    result = TickResult(market_open=market_open, book=account.load())
    try:
        bars = feed.get_bars(symbols, limit=max(strategy.warmup * 3, strategy.warmup + 100))
    except Exception as exc:  # network / auth problems shouldn't crash the dashboard
        result.errors.append(f"Data fetch failed: {exc}")
        return result

    # Only finished bars produce signals, so a half-built candle can't trigger a trade.
    bar_len = pd.Timedelta(days=1) if timeframe == "1Day" else pd.Timedelta(minutes=TF_MINUTES.get(timeframe, 5))
    signals = {}
    for sym in symbols:
        df = bars.get(sym)
        if df is None or df.empty:
            result.errors.append(f"No data for {sym}")
            continue
        price = float(df["close"].iloc[-1])
        starts = df.index if df.index.tz is not None else df.index.tz_localize("UTC")
        df = df[starts + bar_len <= now]
        if df.empty:
            continue
        df = strategy.compute(df)
        result.bars[sym] = df
        result.prices[sym] = price
        signal = df["signal"].iloc[-1] if len(df) > strategy.warmup else HOLD
        result.signals[sym] = signal
        signals[sym] = (signal, str(df.index[-1]), df["stop_dist"].iloc[-1])

    if trade and market_open and result.prices:
        eod = risk.flatten_eod and timeframe != "1Day" and now.time() >= time(15, 50)
        book = result.book
        result.trades = book.step(now, now.date().isoformat(), result.prices, signals, risk, rules,
                                  len(symbols), eod)
        account.save(book, result.trades)
    return result
