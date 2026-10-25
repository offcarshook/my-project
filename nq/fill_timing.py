"""Sensitivity of results to WHEN a market entry fills.

The backtests fill a market order sent from on_bar at that bar's close (+1 tick). On these
IEX-built candles the next bar's open often differs from that close by several ticks, so this
re-prices each market ENTRY at the next bar's open (+1 tick) instead, keeping the stop and target
levels, and reports how expectancy changes. Trades whose next open is already past the stop are
counted as immediate stop-outs at that open. Approximate: it does not re-simulate later fills."""
import argparse
import warnings

import numpy as np
import pandas as pd

from nq.backtest import COMMISSION_PER_SIDE, run_mean_reversion, run_orb
from nq.data import load_proxy

warnings.filterwarnings("ignore")
TICK, MULT = 0.25, 2.0


def reprice_entries(trades: pd.DataFrame, frame: pd.DataFrame) -> pd.DataFrame:
    t = trades.copy()
    pos = frame.index.get_indexer(t.entry_ts)
    ok = (pos >= 0) & (pos + 1 < len(frame))
    nxt_open = np.full(len(t), np.nan)
    nxt_open[ok] = frame["open"].to_numpy()[pos[ok] + 1]
    d = np.where(t.side == "LONG", 1.0, -1.0)
    new_entry = nxt_open + d * TICK
    t["entry_shift_ticks"] = (new_entry - t.entry) * d / TICK          # + = worse for the trade
    gross_old = (t.exit - t.entry) * d * t.qty * MULT
    costs = gross_old - t.pnl
    through_stop = (t.stop - new_entry) * d >= 0
    exit_px = np.where(through_stop, new_entry, t.exit)
    pnl = (exit_px - new_entry) * d * t.qty * MULT - costs
    risk = np.abs(new_entry - t.stop) * t.qty * MULT
    t["pnl_next_open"] = pnl
    t["r_next_open"] = np.where(through_stop, pnl / t.risk, pnl / np.where(risk > 0, risk, np.nan))
    return t[ok]


def report(name, trades, frame):
    t = reprice_entries(trades, frame)
    return {"strategy": name, "trades": len(t), "entry_shift_median_ticks": t.entry_shift_ticks.median(),
            "entry_shift_mean_ticks": t.entry_shift_ticks.mean(), "r_close_fill": t.r.mean(),
            "r_next_open_fill": t.r_next_open.mean()}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--out")
    a = p.parse_args(argv)
    f = load_proxy(a.data)["MNQ"]
    rows = []
    for label, (s, e) in {"train": ("2022-03-21", "2024-03-31"), "validation": ("2024-04-01", "2025-03-31"),
                          "late (examined)": ("2025-04-01", "2026-10-21")}.items():
        t, _ = run_orb(f, s, e, range_minutes=5)
        rows.append({"period": label, **report("ORB 5m (frozen)", t, f)})
        t, _ = run_orb(f, s, e)
        rows.append({"period": label, **report("ORB 15m baseline", t, f)})
    t, _ = run_mean_reversion(f, "2022-03-21", "2025-03-31", signal="vwap", entry_k=2.0, er_max=0.35, vol_max=2.0)
    rows.append({"period": "research span", **report("MR vwap k=2 gates", t, f)})
    df = pd.DataFrame(rows)
    print(df.round(3).to_string(index=False))
    if a.out:
        df.to_csv(a.out, index=False)


if __name__ == "__main__":
    main()
