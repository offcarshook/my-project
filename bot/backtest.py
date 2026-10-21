"""Replay the SMA crossover strategy on historical prices."""
from dataclasses import dataclass

import pandas as pd

from bot.strategy import BUY, SELL, add_smas, crossovers


@dataclass
class BacktestResult:
    equity: pd.Series          # portfolio value per day
    trades: pd.DataFrame       # every simulated fill
    metrics: dict


def run_backtest(bars: dict[str, pd.DataFrame], short: int, long: int, start,
                 starting_cash: float = 10_000, slippage_pct: float = 0.05) -> BacktestResult:
    """Same rules as the live bot: long only, one position per symbol, whole shares,
    budget split evenly across symbols, fills at the signal bar's close plus slippage.

    `bars` should include warm-up history before `start` so the SMAs are ready on day one.
    """
    start = pd.Timestamp(start)
    slip = slippage_pct / 100
    closes, signals = {}, {}
    for sym, df in bars.items():
        if df.empty:
            continue
        df = add_smas(df, short, long)
        closes[sym] = df["close"]
        signals[sym] = crossovers(df)
    prices = pd.DataFrame(closes).sort_index().ffill()
    sigs = pd.DataFrame(signals).reindex(prices.index).fillna("HOLD")
    if start.tzinfo is None and prices.index.tz is not None:
        start = start.tz_localize(prices.index.tz)
    prices, sigs = prices[prices.index >= start], sigs[sigs.index >= start]

    budget = starting_cash / max(len(closes), 1)
    cash, positions, trades, equity = starting_cash, {}, [], []
    for ts, row in prices.iterrows():
        for sym, price in row.items():
            if pd.isna(price):
                continue
            signal = sigs.at[ts, sym]
            if signal == BUY and sym not in positions:
                fill = price * (1 + slip)
                qty = int(min(budget, cash) // fill)
                if qty > 0:
                    cash -= qty * fill
                    positions[sym] = (qty, fill)
                    trades.append({"date": ts, "symbol": sym, "side": "BUY", "qty": qty,
                                   "price": fill, "pnl": None})
            elif signal == SELL and sym in positions:
                qty, cost = positions.pop(sym)
                fill = price * (1 - slip)
                cash += qty * fill
                trades.append({"date": ts, "symbol": sym, "side": "SELL", "qty": qty,
                               "price": fill, "pnl": (fill - cost) * qty})
        held = sum(q * row[s] for s, (q, _) in positions.items() if not pd.isna(row[s]))
        equity.append(cash + held)

    equity = pd.Series(equity, index=prices.index, name="equity")
    trades = pd.DataFrame(trades, columns=["date", "symbol", "side", "qty", "price", "pnl"])
    return BacktestResult(equity, trades, summarize(equity, trades, starting_cash))


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
        return {"total_return_pct": 0.0, "cagr_pct": 0.0, "max_drawdown_pct": 0.0,
                "trades": 0, "closed_trades": 0, "win_rate_pct": 0.0, "final_value": starting_cash}
    final = float(equity.iloc[-1])
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1 / 365.25)
    closed = trades.dropna(subset=["pnl"])
    wins = int((closed["pnl"] > 0).sum())
    return {
        "total_return_pct": (final / starting_cash - 1) * 100,
        "cagr_pct": ((final / starting_cash) ** (1 / years) - 1) * 100,
        "max_drawdown_pct": max_drawdown(equity),
        "trades": len(trades),
        "closed_trades": len(closed),
        "win_rate_pct": wins / len(closed) * 100 if len(closed) else 0.0,
        "final_value": final,
    }
