"""Performance metrics and report tables for simulator results."""
import numpy as np
import pandas as pd

from bot.indicators import ny_index


def _sessions(equity: pd.DataFrame) -> int:
    return max(len(set(ny_index(equity.index).date)), 1) if len(equity) else 1


def longest_losing_streak(pnl: pd.Series) -> int:
    best = run = 0
    for v in pnl:
        run = run + 1 if v <= 0 else 0
        best = max(best, run)
    return best


def trade_stats(trades: pd.DataFrame, sessions: int, starting_cash: float) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0, "trades_per_session": 0.0, "win_rate_pct": np.nan, "avg_win": np.nan,
                "avg_loss": np.nan, "profit_factor": np.nan, "net_pnl": 0.0, "net_return_pct": 0.0,
                "expectancy": np.nan, "expectancy_r": np.nan, "r_tstat": np.nan, "longest_losing_streak": 0,
                "fees": 0.0, "cost_r": np.nan}
    pnl, r = trades["pnl"], trades["r"].dropna()
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    gross_loss = -losses.sum()
    return {
        "trades": n,
        "trades_per_session": n / sessions,
        "win_rate_pct": len(wins) / n * 100,
        "avg_win": wins.mean() if len(wins) else 0.0,
        "avg_loss": losses.mean() if len(losses) else 0.0,
        "profit_factor": wins.sum() / gross_loss if gross_loss > 0 else np.inf,
        "net_pnl": pnl.sum(),
        "net_return_pct": pnl.sum() / starting_cash * 100,
        "expectancy": pnl.mean(),
        "expectancy_r": r.mean() if len(r) else np.nan,
        # mean R divided by its standard error: a rough "is this distinguishable from zero" score
        "r_tstat": r.mean() / r.std(ddof=1) * np.sqrt(len(r)) if len(r) > 2 and r.std(ddof=1) > 0 else np.nan,
        "longest_losing_streak": longest_losing_streak(pnl),
        "fees": trades["fees"].sum(),
        "cost_r": (trades["costs"] / trades["risk"]).mean() if "costs" in trades and "risk" in trades else np.nan,
    }


def curve_stats(equity: pd.DataFrame, trades: pd.DataFrame, starting_cash: float) -> dict:
    if equity.empty:
        return {"max_drawdown_pct": 0.0, "max_daily_drawdown_pct": 0.0, "exposure_pct": 0.0, "turnover": 0.0}
    worst = equity["worst"]
    peak = equity["equity"].cummax().clip(lower=starting_cash)
    max_dd = ((worst - peak) / peak).min() * 100
    days = pd.Series(ny_index(equity.index).date, index=equity.index)
    day_start = equity["equity"].groupby(days).transform("first")
    prev_close = equity["equity"].groupby(days).last().shift().reindex(days.values).to_numpy()
    start_ref = np.where(np.isnan(prev_close), day_start, prev_close)
    max_daily = ((worst - start_ref) / start_ref).min() * 100
    return {
        "max_drawdown_pct": max_dd,
        "max_daily_drawdown_pct": max_daily,
        "exposure_pct": (equity["positions"] > 0).mean() * 100,
        "turnover": trades["notional"].sum() / equity["equity"].mean() if len(trades) else 0.0,
    }


def summarize(res, starting_cash: float) -> dict:
    sessions = _sessions(res.equity)
    return {**trade_stats(res.trades, sessions, starting_cash), **curve_stats(res.equity, res.trades, starting_cash),
            "sessions": sessions}


def cap_hit_pct(trades: pd.DataFrame, sessions: int, cap: int) -> float:
    """Share of sessions where entries reached the daily trade limit (later signals were dropped)."""
    if trades.empty:
        return 0.0
    per_day = pd.Series(ny_index(pd.DatetimeIndex(trades["entry_ts"])).date).value_counts()
    return (per_day >= cap).sum() / max(sessions, 1) * 100


def per_instrument(trades: pd.DataFrame, sessions: int, starting_cash: float) -> pd.DataFrame:
    rows = {sym: trade_stats(g, sessions, starting_cash) for sym, g in trades.groupby("symbol")}
    cols = ["trades", "trades_per_session", "win_rate_pct", "avg_win", "avg_loss", "profit_factor", "net_pnl",
            "expectancy", "expectancy_r", "longest_losing_streak", "fees"]
    return pd.DataFrame(rows).T[cols].sort_values("net_pnl", ascending=False) if rows else pd.DataFrame(columns=cols)


def monthly(trades: pd.DataFrame, equity: pd.DataFrame) -> pd.DataFrame:
    if equity.empty:
        return pd.DataFrame()
    eq = equity["equity"].copy()
    eq.index = ny_index(eq.index).tz_localize(None)
    month_end = eq.resample("ME").last()
    ret = month_end.pct_change()
    ret.iloc[0] = month_end.iloc[0] / eq.iloc[0] - 1
    out = pd.DataFrame({"return_pct": ret * 100})
    if len(trades):
        t = trades.copy()
        t["month"] = ny_index(pd.DatetimeIndex(t["exit_ts"])).tz_localize(None).to_period("M").to_timestamp("M")
        g = t.groupby("month")
        out = out.join(pd.DataFrame({"trades": g.size(), "win_rate_pct": g["pnl"].apply(lambda p: (p > 0).mean() * 100),
                                     "net_pnl": g["pnl"].sum()}), how="left")
    out.index = out.index.strftime("%Y-%m")
    return out


def regimes(daily_close: pd.Series, lookback: int = 20, trend_pct: float = 3.0, high_vol_mult: float = 1.5) -> pd.DataFrame:
    """Label each session by the regime symbol's trailing behaviour, known before that session:
    trend = up / down / sideways from the prior `lookback`-day return; vol = high when the prior
    `lookback`-day volatility is over `high_vol_mult` x the median of all volatility readings up
    to that day (expanding, so no future data). Labels are for reporting only."""
    ret = daily_close.pct_change()
    trailing = (daily_close / daily_close.shift(lookback) - 1).shift(1) * 100
    vol = ret.rolling(lookback).std().shift(1)
    trend = np.where(trailing > trend_pct, "trending up", np.where(trailing < -trend_pct, "declining", "sideways"))
    high = vol > high_vol_mult * vol.expanding(min_periods=lookback).median()
    out = pd.DataFrame({"trend": trend, "volatility": np.where(high, "high vol", "normal vol")}, index=daily_close.index)
    return out[trailing.notna()]


def by_regime(trades: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    if trades.empty or labels.empty:
        return pd.DataFrame()
    t = trades.copy()
    t["day"] = ny_index(pd.DatetimeIndex(t["entry_ts"])).date
    t = t.join(labels, on="day", how="inner")
    rows = []
    for col in ("trend", "volatility"):
        for name, g in t.groupby(col):
            pnl = g["pnl"]
            losses = -pnl[pnl <= 0].sum()
            rows.append({"regime": name, "sessions": labels[col].eq(name).sum(), "trades": len(g),
                         "win_rate_pct": (pnl > 0).mean() * 100, "expectancy_r": g["r"].mean(),
                         "profit_factor": pnl[pnl > 0].sum() / losses if losses > 0 else np.inf,
                         "net_pnl": pnl.sum()})
    return pd.DataFrame(rows).set_index("regime")


def challenge_summary(windows: pd.DataFrame) -> dict:
    if windows.empty:
        return {"challenges": 0}
    n = len(windows)
    passed, failed = windows[windows.result == "PASSED"], windows[windows.result == "FAILED"]
    return {
        "challenges": n,
        "pass_pct": len(passed) / n * 100,
        "fail_pct": len(failed) / n * 100,
        "daily_breach_pct": failed.reason.str.contains("daily").sum() / n * 100,
        "total_breach_pct": failed.reason.str.contains("total").sum() / n * 100,
        "timeout_pct": (windows.result == "TIMEOUT").sum() / n * 100,
        "avg_days_to_pass": passed.trading_days.mean() if len(passed) else np.nan,
        "median_days_to_pass": passed.trading_days.median() if len(passed) else np.nan,
        "avg_days_to_fail": failed.trading_days.mean() if len(failed) else np.nan,
        "median_days_to_fail": failed.trading_days.median() if len(failed) else np.nan,
    }


def fmt_table(df: pd.DataFrame, floatfmt: str = ".2f") -> str:
    """Small markdown table formatter (no extra dependency)."""
    if df is None or df.empty:
        return "_no data_\n"
    df = df.copy()
    headers = [str(df.index.name or "")] + [str(c) for c in df.columns]
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for idx, row in df.iterrows():
        cells = [str(idx)]
        for v in row:
            if isinstance(v, (float, np.floating)):
                if np.isnan(v):
                    cells.append("—")
                elif np.isinf(v):
                    cells.append("∞" if v > 0 else "rejected")
                elif float(v).is_integer() and abs(v) >= 1:
                    cells.append(f"{int(v):,}")
                else:
                    cells.append(format(v, floatfmt))
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"
