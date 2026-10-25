"""Accounting audit: reconcile a backtest's trade log against NautilusTrader's own account,
monthly returns against the cumulative return, and realized risk against the risk setting.

    python -m nq.audit --data DIR [--start 2025-04-01 --end 2026-10-21]
"""
import argparse
import warnings

import numpy as np
import pandas as pd

from nq.backtest import STARTING_CASH, run_orb, summarize
from nq.data import load_proxy

warnings.filterwarnings("ignore")
NY = "America/New_York"


def audit(trades: pd.DataFrame, account_equity: pd.Series, risk_pct: float, cash=STARTING_CASH) -> dict:
    t = trades.sort_values("exit_ts").reset_index(drop=True)
    eq_before = cash + t["pnl"].cumsum().shift(fill_value=0.0)
    out = {"trades": len(t)}
    # 1. trade log vs the engine's account balance
    out["final_equity_from_trades"] = cash + t["pnl"].sum()
    out["final_equity_from_engine"] = float(account_equity.iloc[-1]) if len(account_equity) else np.nan
    out["equity_gap_usd"] = out["final_equity_from_trades"] - out["final_equity_from_engine"]
    # 2. compounded monthly returns vs cumulative return
    month = t["exit_ts"].dt.tz_convert(NY).dt.strftime("%Y-%m")
    m_ret = t.groupby(month)["pnl"].sum() / eq_before.groupby(month).first()
    out["cumulative_return_pct"] = (out["final_equity_from_trades"] / cash - 1) * 100
    out["compounded_monthly_pct"] = (np.prod(1 + m_ret) - 1) * 100
    out["arithmetic_mean_month_pct"] = m_ret.mean() * 100
    out["geometric_mean_month_pct"] = (np.prod(1 + m_ret) ** (1 / len(m_ret)) - 1) * 100
    # 3. risk actually taken per trade vs the setting (whole contracts round risk DOWN)
    taken = t["risk"] / eq_before * 100
    out["risk_setting_pct"] = risk_pct
    out["risk_taken_median_pct"] = taken.median()
    out["risk_taken_p10_p90_pct"] = f"{taken.quantile(0.1):.2f}-{taken.quantile(0.9):.2f}"
    stops = t[t["r"] < -0.9]
    out["stopped_loss_median_pct_of_equity"] = (-stops["pnl"] / eq_before[stops.index] * 100).median()
    # 4. drawdown on closed-trade equity (intraday open-trade dips are not in this number)
    eq = cash + t["pnl"].cumsum()
    peak = eq.cummax().clip(lower=cash)
    out["max_drawdown_closed_pct"] = ((eq - peak) / peak).min() * 100
    out["summarize_max_drawdown_pct"] = summarize(trades, cash)["max_drawdown_pct"]
    return out


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--start", default="2025-04-01")
    p.add_argument("--end", default="2026-10-21")
    p.add_argument("--range", type=int, default=5)
    p.add_argument("--risk", type=float, default=0.5)
    a = p.parse_args(argv)
    frame = load_proxy(a.data)["MNQ"]
    trades, equity = run_orb(frame, a.start, a.end, range_minutes=a.range, risk_pct=a.risk)
    for k, v in audit(trades, equity, a.risk).items():
        print(f"{k:36s} {v:,.3f}" if isinstance(v, float) else f"{k:36s} {v}")


if __name__ == "__main__":
    main()
