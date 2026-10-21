"""Replay the strategy on historical prices, including prop-challenge simulations."""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from bot.book import ACTIVE, Book, ChallengeRules, RiskConfig
from bot.data import NY
from bot.strategy import add_smas, crossovers


@dataclass
class BacktestResult:
    equity: pd.Series          # portfolio value per bar
    trades: pd.DataFrame       # every simulated fill
    metrics: dict


class _Market:
    """Prices and signals as arrays so the simulation loop stays fast."""

    def __init__(self, bars: dict[str, pd.DataFrame], short: int, long: int, intraday: bool):
        closes, signals = {}, {}
        for sym, df in bars.items():
            if df.empty:
                continue
            df = add_smas(df, short, long)
            closes[sym] = df["close"]
            signals[sym] = crossovers(df)
        prices = pd.DataFrame(closes).sort_index()
        self.index = prices.index
        self.symbols = list(prices.columns)
        self.prices = prices.ffill().to_numpy()
        self.signals = pd.DataFrame(signals).reindex(self.index).fillna("HOLD").to_numpy()
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
            row = self.prices[i]
            prices = {s: p for s, p in zip(self.symbols, row) if not np.isnan(p)}
            signals = {s: (sig, i) for s, sig in zip(self.symbols, self.signals[i])}
            trades += book.step(self.index[i], self.days[i], prices, signals, risk, rules, n,
                                eod=risk.flatten_eod and self.eod[i])
            equity.append(book.equity(prices))
            if stop_when_done and book.status != ACTIVE:
                break
        return equity, trades


def run_backtest(bars: dict[str, pd.DataFrame], short: int, long: int, start,
                 starting_cash: float = 10_000, risk: RiskConfig | None = None,
                 intraday: bool = False) -> BacktestResult:
    """Run the strategy from `start` to the end of the data with no challenge rules.
    `bars` should include warm-up history before `start` so the SMAs are ready on day one."""
    market = _Market(bars, short, long, intraday)
    lo = market.position(start)
    equity, trades = market.simulate(Book.new(starting_cash), lo, len(market.index),
                                     risk or RiskConfig(), ChallengeRules())
    equity = pd.Series(equity, index=market.index[lo:lo + len(equity)], name="equity")
    trades = pd.DataFrame(trades, columns=["ts", "symbol", "side", "qty", "price", "pnl", "reason"])
    return BacktestResult(equity, trades, summarize(equity, trades, starting_cash))


def run_challenges(bars: dict[str, pd.DataFrame], short: int, long: int, start,
                   starting_cash: float, risk: RiskConfig, rules: ChallengeRules,
                   intraday: bool = False, window_days: int = 30, every_n_days: int = 5) -> pd.DataFrame:
    """Start a fresh prop challenge every `every_n_days` trading days and record how each
    one ends: PASSED (target hit), FAILED (a loss limit hit) or TIMEOUT (ran out of days)."""
    market = _Market(bars, short, long, intraday)
    rules = ChallengeRules(**{**rules.__dict__, "enabled": True, "stop_on_pass": True})
    lo = market.position(start)
    first_bars = [i for i in range(lo, len(market.index)) if i == lo or market.days[i] != market.days[i - 1]]
    end_ts = market.index[-1] if len(market.index) else None
    rows = []
    for k, i in enumerate(first_bars[::every_n_days]):
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
                "trades": 0, "closed_trades": 0, "win_rate_pct": 0.0, "final_value": starting_cash}
    final = float(equity.iloc[-1])
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1 / 365.25)
    growth = max(final / starting_cash, 0)
    closed = trades.dropna(subset=["pnl"])
    wins = int((closed["pnl"] > 0).sum())
    return {
        "total_return_pct": (final / starting_cash - 1) * 100,
        "cagr_pct": (growth ** (1 / years) - 1) * 100,
        "monthly_pct": (growth ** (1 / (years * 12)) - 1) * 100,
        "max_drawdown_pct": max_drawdown(equity),
        "trades": len(trades),
        "closed_trades": len(closed),
        "win_rate_pct": wins / len(closed) * 100 if len(closed) else 0.0,
        "final_value": final,
    }
