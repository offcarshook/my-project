"""One bot step: fetch prices, check for crossovers, place paper trades."""
from dataclasses import dataclass, field

import pandas as pd

from bot.paper import PaperAccount
from bot.strategy import BUY, SELL, add_smas, latest_signal


@dataclass
class TickResult:
    market_open: bool
    bars: dict[str, pd.DataFrame] = field(default_factory=dict)
    signals: dict[str, str] = field(default_factory=dict)
    trades: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def run_tick(feed, account: PaperAccount, symbols: list[str], short: int, long: int,
             market_open: bool, trade: bool = True) -> TickResult:
    result = TickResult(market_open=market_open)
    try:
        bars = feed.get_bars(symbols, limit=long * 3)
    except Exception as exc:  # network / auth problems shouldn't crash the dashboard
        result.errors.append(f"Data fetch failed: {exc}")
        return result

    # Split each symbol's budget evenly across the watchlist.
    budget = account.starting_cash / max(len(symbols), 1)
    for sym in symbols:
        df = bars.get(sym)
        if df is None or df.empty:
            result.errors.append(f"No data for {sym}")
            continue
        result.bars[sym] = add_smas(df, short, long)
        signal = latest_signal(df, short, long)
        result.signals[sym] = signal
        if not (trade and market_open):
            continue
        price = float(df["close"].iloc[-1])
        fill = account.buy(sym, price, budget) if signal == BUY else (
            account.sell(sym, price) if signal == SELL else None)
        if fill:
            result.trades.append(fill)
    return result
