"""Replay the strategy on historical prices, including prop-challenge simulations."""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from bot.book import ACTIVE, Book, ChallengeRules, RiskConfig
from bot.data import NY


@dataclass
class BacktestResult:
    equity: pd.Series          # portfolio value per bar
    trades: pd.DataFrame       # every simulated fill
    metrics: dict


class _Market:
    """Prices and signals as arrays so the simulation loop stays fast."""

    def __init__(self, bars: dict[str, pd.DataFrame], strategy, intraday: bool):
        cols = {k: {} for k in ("close", "high", "low", "signal", "stop_dist")}
        for sym, df in bars.items():
            if df.empty:
                continue
            df = strategy.compute(df)
            cols["close"][sym] = df["close"]
            cols["high"][sym] = df["high"] if "high" in df else df["close"]
            cols["low"][sym] = df["low"] if "low" in df else df["close"]
            cols["signal"][sym] = df["signal"]
            cols["stop_dist"][sym] = df["stop_dist"]
        prices = pd.DataFrame(cols["close"]).sort_index()
        self.index = prices.index
        self.symbols = list(prices.columns)
        self.prices = prices.ffill().to_numpy()
        self.highs = pd.DataFrame(cols["high"]).reindex(self.index).to_numpy()
        self.lows = pd.DataFrame(cols["low"]).reindex(self.index).to_numpy()
        self.signals = pd.DataFrame(cols["signal"]).reindex(self.index).fillna("HOLD").to_numpy()
        self.stops = pd.DataFrame(cols["stop_dist"]).reindex(self.index).to_numpy(dtype=float)
        local = self.index.tz_convert(NY) if self.index.tz is not None else self.index
        self.days = np.array([d.isoformat() for d in local.date])
        self.eod = np.zeros(len(self.index), dtype=bool)
        if intraday and len(self.days):
            self.eod[:-1] = self.days[:-1] != self.days[1:]
            self.eod[-1] = True

    def position(self, ts) -> int:
        ts = pd.Timestamp(ts)
        if ts.tzinfo is None and self.index.tz is not None:
            ts = ts.tz_localize(self.index.tz)
        return int(self.index.searchsorted(ts))

    def simulate(self, book: Book, lo: int, hi: int, risk: RiskConfig, rules: ChallengeRules,
                 stop_when_done: bool = False):
        equity, trades = [], []
        n = len(self.symbols)
        for i in range(lo, hi):
            live = [(k, s) for k, s in enumerate(self.symbols) if not np.isnan(self.prices[i, k])]
            prices = {s: self.prices[i, k] for k, s in live}
            # Only symbols with a real bar this step have a range to check stops against.
            highs = {s: self.highs[i, k] for k, s in live if not np.isnan(self.highs[i, k])}
            lows = {s: self.lows[i, k] for k, s in live if not np.isnan(self.lows[i, k])}
            signals = {s: (self.signals[i, k], i, self.stops[i, k]) for k, s in live
                       if self.signals[i, k] != "HOLD"}
            trades += book.step(self.index[i], self.days[i], prices, signals, risk, rules, n,
                                eod=risk.flatten_eod and self.eod[i], highs=highs, lows=lows)
            equity.append(book.equity(prices))
            if stop_when_done and book.status != ACTIVE:
                break
        return equity, trades


def run_backtest(bars: dict[str, pd.DataFrame], strategy, start, starting_cash: float = 10_000,
                 risk: RiskConfig | None = None, intraday: bool = False) -> BacktestResult:
    """Run the strategy from `start` to the end of the data with no challenge rules.
    `bars` should include warm-up history before `start` so indicators are ready on day one."""
    market = _Market(bars, strategy, intraday)
    lo = market.position(start)
    equity, trades = market.simulate(Book.new(starting_cash), lo, len(market.index),
                                     risk or RiskConfig(), ChallengeRules())
    equity = pd.Series(equity, index=market.index[lo:lo + len(equity)], name="equity")
    trades = pd.DataFrame(trades, columns=["ts", "symbol", "side", "qty", "price", "pnl", "reason"])
    return BacktestResult(equity, trades, summarize(equity, trades, starting_cash))


def run_challenges(bars: dict[str, pd.DataFrame], strategy, start, starting_cash: float,
                   risk: RiskConfig, rules: ChallengeRules, intraday: bool = False,
                   window_days: int = 30, every_n_days: int = 5) -> pd.DataFrame:
    """Start a fresh prop challenge every `every_n_days` trading days and record how each
    one ends: PASSED (target hit), FAILED (a loss limit hit) or TIMEOUT (ran out of days)."""
    market = _Market(bars, strategy, intraday)
    rules = ChallengeRules(**{**rules.__dict__, "enabled": True, "stop_on_pass": True})
    lo = market.position(start)
    first_bars = [i for i in range(lo, len(market.index)) if i == lo or market.days[i] != market.days[i - 1]]
    end_ts = market.index[-1] if len(market.index) else None
    rows = []
    for i in first_bars[::every_n_days]:
        t0 = market.index[i]
        deadline = t0 + pd.Timedelta(days=window_days)
        if deadline > end_ts:
            break
        hi = market.position(deadline)
        book = Book.new(starting_cash)
        equity, _ = market.simulate(book, i, hi, risk, rules, stop_when_done=True)
        finished = market.index[i + len(equity) - 1]
        result = book.status if book.status != ACTIVE else "TIMEOUT"
        rows.append({"start": t0, "result": result, "reason": book.status_reason or "out of time",
                     "days": max((finished - t0).days, 1) if result != "TIMEOUT" else window_days,
                     "return_pct": (equity[-1] / starting_cash - 1) * 100,
                     "worst_pct": (min(equity) / starting_cash - 1) * 100})
    return pd.DataFrame(rows, columns=["start", "result", "reason", "days", "return_pct", "worst_pct"])


def buy_and_hold(close: pd.Series, start, starting_cash: float = 10_000) -> pd.Series:
    """Equity curve for putting all the cash into one symbol on day one (whole shares)."""
    start = pd.Timestamp(start)
    if start.tzinfo is None and close.index.tz is not None:
        start = start.tz_localize(close.index.tz)
    close = close[close.index >= start]
    if close.empty:
        return close
    qty = int(starting_cash // close.iloc[0])
    return (starting_cash - qty * close.iloc[0]) + qty * close


def max_drawdown(equity: pd.Series) -> float:
    """Worst peak-to-trough drop, as a negative percentage."""
    if equity.empty:
        return 0.0
    return float((equity / equity.cummax() - 1).min() * 100)


def summarize(equity: pd.Series, trades: pd.DataFrame, starting_cash: float) -> dict:
    if equity.empty:
        return {"total_return_pct": 0.0, "cagr_pct": 0.0, "monthly_pct": 0.0, "max_drawdown_pct": 0.0,
                "trades": 0, "closed_trades": 0, "win_rate_pct": 0.0, "trades_per_day": 0.0,
                "final_value": starting_cash}
    final = float(equity.iloc[-1])
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1 / 365.25)
    growth = max(final / starting_cash, 0)
    closed = trades.dropna(subset=["pnl"])
    wins = int((closed["pnl"] > 0).sum())
    entries = trades[trades["side"].isin(["BUY", "SHORT"])] if len(trades) else trades
    trading_days = max(len(set(equity.index.date)), 1)
    return {
        "total_return_pct": (final / starting_cash - 1) * 100,
        "cagr_pct": (growth ** (1 / years) - 1) * 100,
        "monthly_pct": (growth ** (1 / (years * 12)) - 1) * 100,
        "max_drawdown_pct": max_drawdown(equity),
        "trades": len(trades),
        "closed_trades": len(closed),
        "win_rate_pct": wins / len(closed) * 100 if len(closed) else 0.0,
        "trades_per_day": len(entries) / trading_days,
        "final_value": final,
    }
