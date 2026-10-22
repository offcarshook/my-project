"""Luck simulator: reshuffle a backtest's trading days thousands of times to see the
spread of outcomes, not just the one order history happened to deal."""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from bot.book import ChallengeRules
from bot.data import NY


@dataclass
class LuckResult:
    pass_rate: float
    fail_rate: float
    timeout_rate: float
    days_to_pass: np.ndarray     # trading days, one per passed run
    period_returns: np.ndarray   # % return of each run over the whole window, ignoring the rules
    fail_reasons: dict[str, int]


def daily_trade_returns(trades: pd.DataFrame, equity: pd.Series, starting_cash: float) -> list[np.ndarray]:
    """Each trading day's closed trades as returns on the equity just before that bar.
    Days with no closed trades are kept as empty arrays, so quiet days stay in the mix."""
    days = pd.Index(_ny_dates(equity.index)).unique()
    closed = trades.dropna(subset=["pnl"])
    by_day: dict = {d: [] for d in days}
    if not closed.empty:
        pos = equity.index.searchsorted(pd.DatetimeIndex(closed["ts"]))
        before = np.where(pos > 0, equity.to_numpy()[np.maximum(pos - 1, 0)], starting_cash)
        for day, r in zip(_ny_dates(pd.DatetimeIndex(closed["ts"])), closed["pnl"].to_numpy() / before):
            by_day.setdefault(day, []).append(r)
    return [np.array(by_day[d]) for d in days]


def simulate_luck(day_returns: list[np.ndarray], rules: ChallengeRules, n_days: int = 21,
                  n_sims: int = 5000, seed: int = 0) -> LuckResult:
    """Build `n_sims` random challenges of `n_days` trading days, each day drawn (with
    replacement) from the real ones, and apply the target / daily / total loss rules
    after every closed trade."""
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, len(day_returns), size=(n_sims, n_days))
    target = 1 + rules.profit_target_pct / 100
    floor = 1 - rules.max_drawdown_pct / 100
    daily = rules.max_daily_loss_pct / 100
    passed, failed = 0, {"daily loss limit hit": 0, "max drawdown hit": 0}
    days_to_pass, period = [], np.empty(n_sims)
    for i in range(n_sims):
        eq, done = 1.0, False
        free = 1.0  # same days with no rules, for the "what a month looks like" chart
        for day_n, k in enumerate(picks[i], start=1):
            rets = day_returns[k]
            free *= float(np.prod(1 + rets)) if len(rets) else 1.0
            if done:
                continue
            day_start = eq
            for r in rets:
                eq *= 1 + r
                if eq <= floor:
                    failed["max drawdown hit"] += 1
                    done = True
                elif eq <= day_start * (1 - daily):
                    failed["daily loss limit hit"] += 1
                    done = True
                elif eq >= target:
                    passed += 1
                    days_to_pass.append(day_n)
                    done = True
                if done:
                    break
        period[i] = (free - 1) * 100
    n_failed = sum(failed.values())
    return LuckResult(passed / n_sims, n_failed / n_sims, (n_sims - passed - n_failed) / n_sims,
                      np.array(days_to_pass), period, failed)


def _ny_dates(index: pd.DatetimeIndex):
    return (index.tz_convert(NY) if index.tz is not None else index).date
