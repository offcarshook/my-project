"""One live bot step: fetch prices, compute signals, let the Book trade."""
from dataclasses import dataclass, field
from datetime import datetime, time, timezone

import pandas as pd

from bot.book import Book, ChallengeRules, RiskConfig
from bot.data import NY
from bot.paper import PaperAccount
from bot.strategy import HOLD, add_smas, crossovers


@dataclass
class TickResult:
    market_open: bool
    book: Book | None = None
    bars: dict[str, pd.DataFrame] = field(default_factory=dict)
    prices: dict[str, float] = field(default_factory=dict)
    signals: dict[str, str] = field(default_factory=dict)
    trades: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def run_tick(feed, account: PaperAccount, symbols: list[str], short: int, long: int,
             market_open: bool, risk: RiskConfig | None = None, rules: ChallengeRules | None = None,
             intraday: bool = True, trade: bool = True, now: datetime | None = None) -> TickResult:
    risk, rules = risk or RiskConfig(), rules or ChallengeRules()
    result = TickResult(market_open=market_open, book=account.load())
    try:
        bars = feed.get_bars(symbols, limit=max(long * 3, long + 50))
    except Exception as exc:  # network / auth problems shouldn't crash the dashboard
        result.errors.append(f"Data fetch failed: {exc}")
        return result

    signals = {}
    for sym in symbols:
        df = bars.get(sym)
        if df is None or df.empty:
            result.errors.append(f"No data for {sym}")
            continue
        df = add_smas(df, short, long)
        result.bars[sym] = df
        result.prices[sym] = float(df["close"].iloc[-1])
        signal = crossovers(df).iloc[-1] if len(df) > long else HOLD
        result.signals[sym] = signal
        signals[sym] = (signal, str(df.index[-1]))

    if trade and market_open and result.prices:
        now = (now or datetime.now(timezone.utc)).astimezone(NY)
        eod = risk.flatten_eod and intraday and now.time() >= time(15, 50)
        book = result.book
        result.trades = book.step(now, now.date().isoformat(), result.prices, signals, risk, rules,
                                  len(symbols), eod)
        account.save(book, result.trades)
    return result
