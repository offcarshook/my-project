"""ORB trade journal + performance report (TradeZella-style fields) for the frozen 5m ORB.

    python -m nq.journal --data DIR [--out nq/results/journal]

Writes one row per trade per period (`orb_journal_<period>.csv`) and `report.md`.
The ORB strategy is NOT modified: the extra fields are computed after the run from the same
candles, using only candles completed at or before each entry decision (no look-ahead), except
MFE/MAE and the mark-to-market curve, which describe what happened while the trade was open.

Data: IEX-only QQQ candles scaled to an MNQ proxy (x41, 0.25 tick). Not NQ futures data.
"""
import argparse
import os
import warnings

import numpy as np
import pandas as pd

from nq.backtest import COMMISSION_PER_SIDE, STARTING_CASH, run_orb
from nq.data import load_proxy

warnings.filterwarnings("ignore")
NY = "America/New_York"
TICK, MULT = 0.25, 2.0
FROZEN = {"range_minutes": 5, "risk_pct": 0.5}
PERIODS = {"train": ("2022-03-21", "2024-03-31"), "validation": ("2024-04-01", "2025-03-31"),
           "later_examined": ("2025-04-01", "2026-10-21")}


# ---------------------------------------------------------------------------
# causal per-session context from the candles (index = candle CLOSE time)
# ---------------------------------------------------------------------------
def session_context(frame: pd.DataFrame, range_minutes: int = 5) -> pd.DataFrame:
    f = frame.copy()
    local = f.index.tz_convert(NY)
    f["day"] = local.date
    f["mins"] = local.hour * 60 + local.minute - 570          # minutes after 09:30 at candle close
    prev_close = f["close"].groupby(f["day"]).shift(1)
    tr = pd.concat([f.high - f.low, (f.high - prev_close).abs(), (f.low - prev_close).abs()], axis=1).max(axis=1)
    f["atr14"] = tr.groupby(f["day"]).transform(lambda x: x.rolling(14, min_periods=14).mean())
    rng = f[(f.mins > 0) & (f.mins <= range_minutes)].groupby("day").agg(or_high=("high", "max"), or_low=("low", "min"))
    daily = f.groupby("day").agg(open=("open", "first"), close=("close", "last"))
    daily["prev_close"] = daily["close"].shift(1)
    ctx = rng.join(daily, how="left")
    ctx["or_width_pts"] = ctx.or_high - ctx.or_low
    ctx["or_width_vs_20d"] = ctx.or_width_pts / ctx.or_width_pts.rolling(20, min_periods=10).mean().shift(1)
    ctx["gap_pts"] = ctx.open - ctx.prev_close
    # prior session's efficiency ratio (|net move| / path) from its candles: trend vs chop, known at the open
    path = f.groupby("day")["close"].apply(lambda x: np.abs(np.diff(x.to_numpy())).sum())
    net = (daily.close - daily.open).abs()
    ctx["prior_day_er"] = (net / path).shift(1).reindex(ctx.index)
    return f, ctx


def enrich(trades: pd.DataFrame, frame: pd.DataFrame, cash=STARTING_CASH) -> pd.DataFrame:
    f, ctx = session_context(frame, FROZEN["range_minutes"])
    rows = []
    for _, t in trades.sort_values("entry_ts").iterrows():
        d = t.entry_ts.tz_convert(NY).date()
        c = ctx.loc[d] if d in ctx.index else None
        sig = f.loc[t.entry_ts]                                   # the breakout candle (fill happens at its close)
        long = t.side == "LONG"
        sign = 1 if long else -1
        level = c.or_high if long else c.or_low
        path = f.loc[(f.index > t.entry_ts) & (f.index <= t.exit_ts)]
        stop_pts = abs(t.entry - t.stop)
        mfe = ((path.high.max() - t.entry) if long else (t.entry - path.low.min())) if len(path) else 0.0
        mae = ((t.entry - path.low.min()) if long else (path.high.max() - t.entry)) if len(path) else 0.0
        commission = 2 * COMMISSION_PER_SIDE * t.qty
        entry_slip = (t.entry - sig.close) * sign * t.qty * MULT
        exit_slip = (TICK * MULT * t.qty) if t.exit_kind in ("stop", "market") else 0.0
        gross = (t.exit - t.entry) * sign * t.qty * MULT + entry_slip   # before any cost
        er = c.prior_day_er if c is not None else np.nan
        rows.append({
            "date": d.isoformat(), "symbol": "MNQ (QQQ proxy)", "side": t.side, "contracts": t.qty,
            "entry_time": t.entry_ts.tz_convert(NY).strftime("%H:%M"), "exit_time": t.exit_ts.tz_convert(NY).strftime("%H:%M"),
            "or_high": c.or_high, "or_low": c.or_low, "or_width_pts": c.or_width_pts,
            "or_width_ticks": c.or_width_pts / TICK, "or_width_vs_20d": c.or_width_vs_20d,
            "breakout_time": t.entry_ts.tz_convert(NY).strftime("%H:%M"),
            "breakout_distance_pts": (sig.close - level) * sign, "atr14_pts": sig.atr14,
            "gap_pts": c.gap_pts, "gap_direction": "with" if np.sign(c.gap_pts) == sign else "against",
            "prior_day_er": er, "prior_day_regime": "trend" if er >= 0.3 else "chop" if er == er else "n/a",
            "order_type": "MARKET", "intended_price": sig.close, "fill_price": t.entry,
            "entry_slippage_ticks": (t.entry - sig.close) * sign / TICK, "partial_fill": False,
            "initial_stop": t.stop, "stop_distance_pts": stop_pts, "target": None,
            "exit_price": t.exit, "exit_reason": {"stop": "stop loss", "market": "end of day"}.get(t.exit_kind, t.exit_kind),
            "hold_minutes": (t.exit_ts - t.entry_ts).total_seconds() / 60,
            "risk_usd": t.risk, "gross_pnl_usd": gross, "commission_usd": commission,
            "slippage_usd": entry_slip + exit_slip, "net_pnl_usd": t.pnl, "r_multiple": t.r,
            "mfe_r": mfe / stop_pts if stop_pts else np.nan, "mae_r": mae / stop_pts if stop_pts else np.nan,
            "entry_ts_utc": t.entry_ts, "exit_ts_utc": t.exit_ts,
        })
    j = pd.DataFrame(rows)
    j["equity_before"] = cash + j.net_pnl_usd.cumsum().shift(fill_value=0.0)
    j["risk_pct_of_equity"] = j.risk_usd / j.equity_before * 100
    return j


def mtm_curve(journal: pd.DataFrame, frame: pd.DataFrame, cash=STARTING_CASH) -> pd.DataFrame:
    """Per-candle equity INCLUDING the open position, at the candle's worst price (low for longs,
    high for shorts) and at its close."""
    f = frame.loc[journal.entry_ts_utc.min():journal.exit_ts_utc.max()]
    realized = pd.Series(0.0, index=f.index)
    worst, close = pd.Series(0.0, index=f.index), pd.Series(0.0, index=f.index)
    for _, t in journal.iterrows():
        sign = 1 if t.side == "LONG" else -1
        realized.loc[realized.index >= t.exit_ts_utc] += t.net_pnl_usd
        open_ = (f.index >= t.entry_ts_utc) & (f.index < t.exit_ts_utc)
        ref = t.fill_price
        cost_in = t.commission_usd / 2
        w = (f.low if sign > 0 else f.high)[open_]
        worst.loc[open_] += (w - ref) * sign * t.contracts * MULT - cost_in
        close.loc[open_] += (f.close[open_] - ref) * sign * t.contracts * MULT - cost_in
    return pd.DataFrame({"equity_close": cash + realized + close, "equity_worst": cash + realized + worst})


def _dd(series: pd.Series, cash) -> tuple[float, float]:
    peak = series.cummax().clip(lower=cash)
    dd = series - peak
    return float(dd.min()), float((dd / peak).min() * 100)


def metrics(j: pd.DataFrame, curve: pd.DataFrame, cash=STARTING_CASH) -> dict:
    if j.empty:
        return {"trades": 0}
    n = len(j)
    net = j.net_pnl_usd
    wins, losses = net[net > 0], net[net < 0]
    eq = cash + net.cumsum()
    dd_closed_usd, dd_closed_pct = _dd(eq, cash)
    dd_mtm_usd, dd_mtm_pct = _dd(curve.equity_worst, cash)
    daily = net.groupby(j.date).sum()
    dret = daily / (cash + daily.cumsum().shift(fill_value=0))
    ts = pd.to_datetime(j.date)
    month = net.groupby(ts.dt.to_period("M")).sum()
    m_start = eq.shift(fill_value=cash).groupby(ts.dt.to_period("M")).first()
    mret = month / m_start * 100
    year = net.groupby(ts.dt.year).sum()
    weeks = max((ts.max() - ts.min()).days / 7, 1)
    streak = best = 0
    for x in net:
        streak = streak + 1 if x < 0 else 0
        best = max(best, streak)
    net_profit = float(net.sum())
    return {
        "trades": n, "trades_per_week": n / weeks, "trades_per_month": n / max(len(mret), 1),
        "net_pnl_usd": net_profit, "return_pct": net_profit / cash * 100,
        "gross_profit_usd": float(wins.sum()), "gross_loss_usd": float(losses.sum()),
        "avg_pnl_per_trade_usd": float(net.mean()), "win_rate_pct": len(wins) / n * 100,
        "breakeven_rate_pct": float((net.abs() < 1e-9).mean() * 100),
        "avg_winner_usd": float(wins.mean()) if len(wins) else np.nan,
        "avg_loser_usd": float(losses.mean()) if len(losses) else np.nan,
        "payoff_ratio": float(wins.mean() / -losses.mean()) if len(wins) and len(losses) else np.nan,
        "profit_factor": float(wins.sum() / -losses.sum()) if len(losses) else np.nan,
        "expectancy_usd": float(net.mean()), "expectancy_r": float(j.r_multiple.mean()),
        "r_tstat": float(j.r_multiple.mean() / (j.r_multiple.std() / np.sqrt(n))),
        "max_dd_closed_usd": dd_closed_usd, "max_dd_closed_pct": dd_closed_pct,
        "max_dd_intraday_mtm_usd": dd_mtm_usd, "max_dd_intraday_mtm_pct": dd_mtm_pct,
        "return_over_max_dd": net_profit / -dd_mtm_usd if dd_mtm_usd else np.nan,
        "recovery_factor": net_profit / -dd_mtm_usd if dd_mtm_usd else np.nan,
        "longest_losing_streak": best, "largest_loss_usd": float(net.min()),
        "avg_mae_r": float(j.mae_r.mean()), "avg_mfe_r": float(j.mfe_r.mean()),
        "median_risk_pct_of_equity": float(j.risk_pct_of_equity.median()),
        "commission_usd": float(j.commission_usd.sum()), "slippage_usd": float(j.slippage_usd.sum()),
        "costs_per_trade_r": float(((j.commission_usd + j.slippage_usd) / j.risk_usd).mean()),
        "sharpe_daily_ann": float(dret.mean() / dret.std() * np.sqrt(252)) if dret.std() else np.nan,
        "sortino_daily_ann": float(dret.mean() / dret[dret < 0].std() * np.sqrt(252)) if (dret < 0).sum() > 1 else np.nan,
        "months": len(mret), "profitable_months_pct": float((mret > 0).mean() * 100),
        "avg_month_pct": float(mret.mean()), "median_month_pct": float(mret.median()),
        "best_month_pct": float(mret.max()), "worst_month_pct": float(mret.min()),
        "_monthly": mret, "_yearly": year,
    }


def breakdown(j: pd.DataFrame, col: str) -> pd.DataFrame:
    g = j.groupby(col)
    return pd.DataFrame({"trades": g.size(), "net_pnl_usd": g.net_pnl_usd.sum(), "win_pct": g.net_pnl_usd.apply(
        lambda x: (x > 0).mean() * 100), "expectancy_r": g.r_multiple.mean()}).round(3)


def fmt(df: pd.DataFrame) -> str:
    cols = [str(c) for c in df.columns]
    lines = ["| " + " | ".join([df.index.name or ""] + cols) + " |", "|" + "---|" * (len(cols) + 1)]
    for idx, row in df.iterrows():
        lines.append("| " + " | ".join([str(idx)] + [f"{v:,.3f}" if isinstance(v, float) else str(v) for v in row]) + " |")
    return "\n".join(lines) + "\n"


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--out", default="nq/results/journal")
    a = p.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    frame = load_proxy(a.data)["MNQ"]
    summary, sections = {}, []
    for name, (s, e) in PERIODS.items():
        trades, equity = run_orb(frame, s, e, **FROZEN)
        j = enrich(trades, frame)
        assert abs(STARTING_CASH + j.net_pnl_usd.sum() - float(equity.iloc[-1])) < 0.01, "journal != engine account"
        curve = mtm_curve(j, frame)
        m = metrics(j, curve)
        j.drop(columns=["entry_ts_utc", "exit_ts_utc"]).to_csv(f"{a.out}/orb_journal_{name}.csv", index=False)
        summary[name] = {k: v for k, v in m.items() if not k.startswith("_")}
        j["entry_hour"] = j.entry_time.str[:2] + ":00"
        j["weekday"] = pd.to_datetime(j.date).dt.day_name().str[:3]
        j["or_width_bucket"] = pd.qcut(j.or_width_vs_20d, 3, labels=["narrow", "normal", "wide"])
        sections.append(f"## {name} ({s} .. {e})\n")
        sections.append("### By year\n" + fmt(m["_yearly"].rename("net_pnl_usd").to_frame().round(2)))
        sections.append("### By month (%)\n" + fmt(m["_monthly"].rename("return_pct").to_frame().round(2)))
        for col, title in (("side", "side"), ("weekday", "day of week"), ("entry_hour", "entry hour (session)"),
                           ("exit_reason", "exit reason"), ("gap_direction", "gap vs trade direction"),
                           ("prior_day_regime", "prior-day trend/chop"), ("or_width_bucket", "range width vs 20 sessions")):
            sections.append(f"### By {title}\n" + fmt(breakdown(j, col)))
    table = pd.DataFrame(summary)
    table.index.name = "metric"
    lines = ["# Frozen 5m ORB: trade journal report (MNQ proxy from IEX-only QQQ; not NQ futures data)\n",
             "Frozen parameters: 5-minute opening range, stop at the other side, hold to 15:50, 0.5% risk, "
             "$50k start; $0.85/contract/side commission; 1 tick slippage on market and stop fills. "
             "`later_examined` was looked at before freezing: not a clean test. **Forward paper test: not "
             "started** (needs new data not used for any decision).\n",
             "Intraday drawdown uses equity marked at each candle's worst price with the open position "
             "included; closed-trade drawdown is shown next to it. Journal and engine account reconcile to "
             "the cent for every period (asserted).\n", "## Summary\n", fmt(table.round(3))] + sections
    with open(f"{a.out}/report.md", "w") as fh:
        fh.write("\n".join(lines))
    print(table.round(3).to_string())


if __name__ == "__main__":
    main()
