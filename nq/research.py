"""NQ research runs: pre-registered variants on TRAIN + VALIDATION only.

    python -m nq.research --data DIR orb
"""
import argparse
import warnings

import pandas as pd

import itertools

import numpy as np

from nq.backtest import COMMISSION_PER_SIDE, run_mean_reversion, run_orb, summarize
from nq.execution import conservative_adjust
from nq.data import load_proxy

warnings.filterwarnings("ignore")
TRAIN = ("2022-03-21", "2024-03-31")
VAL = ("2024-04-01", "2025-03-31")

ORB_VARIANTS = {   # fixed before running; one change each from the baseline
    "baseline 15m range, range stop, hold": {},
    "5m range": {"range_minutes": 5},
    "30m range": {"range_minutes": 30},
    "breakout +0.25 ATR": {"buffer_atr": 0.25},
    "last entry 10:30": {"last_entry": "10:30"},
    "2R target": {"target_r": 2.0},
}


def stage_orb(frame, out=None):
    rows = []
    for name, params in ORB_VARIANTS.items():
        for pname, (a, b) in (("train", TRAIN), ("val", VAL)):
            trades, _ = run_orb(frame, a, b, **params)
            m = summarize(trades)
            rows.append({"variant": name, "period": pname, **m})
            print(f"{name:38s} {pname:5s} trades {m.get('trades', 0):4d} ({m.get('trades_per_day', 0):.2f}/day) "
                  f"R {m.get('expectancy_r', float('nan')):+.3f} t {m.get('r_tstat', float('nan')):+.2f} "
                  f"ret {m.get('net_return_pct', 0):+6.1f}% DD {m.get('max_drawdown_pct', 0):6.1f}% "
                  f"month avg {m.get('month_mean_pct', 0):+.2f}%", flush=True)
    df = pd.DataFrame(rows)
    if out:
        df.to_csv(out, index=False)
    return df


# ---------------------------------------------------------------------------
# Phase 2: mean reversion, chronological walk-forward on the research span only
# ---------------------------------------------------------------------------
RESEARCH_SPAN = ("2022-03-21", "2025-03-31")        # data used before; 2025-04.. is held back here
BLOCKS = [("2022-03-21", "2022-09-30"), ("2022-10-01", "2023-03-31"), ("2023-04-01", "2023-09-30"),
          ("2023-10-01", "2024-03-31"), ("2024-04-01", "2024-09-30"), ("2024-10-01", "2025-03-31")]
TRAIN_BLOCKS = 2                                    # fit on the previous 12 months, test the next 6
GATES = {"no gates": {}, "trend+vol gates": {"er_max": 0.35, "vol_max": 2.0}}
MR_GRID = {f"{sig} k={k} {g}": {"signal": sig, "entry_k": k, **gp}           # fixed before any run
           for sig, k, (g, gp) in itertools.product(("vwap", "zscore", "atr_ema"), (1.5, 2.0, 2.5), GATES.items())}
FROZEN_ORB = {"range_minutes": 5}


def block_of(ts):
    d = ts.tz_convert("America/New_York").strftime("%Y-%m-%d")
    for i, (a, b) in enumerate(BLOCKS):
        if a <= d <= b:
            return i
    return -1


def r_stats(r: pd.Series) -> dict:
    n = len(r)
    return {"n": n, "mean_r": r.mean() if n else np.nan,
            "t": r.mean() / (r.std() / np.sqrt(n)) if n > 2 and r.std() > 0 else np.nan}


def stage_mr(frame, out_dir=None, conservative=False):
    """conservative=True re-books target exits on bars that also reached the stop as stop-outs
    (nq/execution.conservative_adjust) before any statistic or walk-forward selection."""
    runs = {}
    for name, params in MR_GRID.items():
        t, _ = run_mean_reversion(frame, *RESEARCH_SPAN, **params)
        if conservative:
            t = conservative_adjust(t, frame, 2.0, COMMISSION_PER_SIDE)
        t["block"] = t["entry_ts"].map(block_of)
        runs[name] = t
        per = [r_stats(t.loc[t.block == b, "r"])["mean_r"] for b in range(len(BLOCKS))]
        print(f"{name:34s} trades {len(t):5d}  R by 6-month block: " + " ".join(f"{x:+.2f}" for x in per), flush=True)
    orb, _ = run_orb(frame, *RESEARCH_SPAN, **FROZEN_ORB)
    orb["block"] = orb["entry_ts"].map(block_of)

    # walk-forward: pick on the previous TRAIN_BLOCKS blocks, trade the next one
    picks, oos = [], []
    for test in range(TRAIN_BLOCKS, len(BLOCKS)):
        train = range(test - TRAIN_BLOCKS, test)
        scored = []
        for name, t in runs.items():
            s = r_stats(t.loc[t.block.isin(train), "r"])
            if s["n"] >= 30 and s["mean_r"] > 0:
                scored.append((s["t"], name))
        pick = max(scored)[1] if scored else None
        test_trades = runs[pick].loc[runs[pick].block == test] if pick else runs[next(iter(runs))].iloc[0:0]
        oos.append(test_trades)
        picks.append({"test_block": f"{BLOCKS[test][0]}..{BLOCKS[test][1]}", "pick": pick or "(none: no trades)",
                      "train_t": max(scored)[0] if scored else np.nan, **{f"oos_{k}": v for k, v in
                                                                        r_stats(test_trades["r"]).items()},
                      **{f"orb_{k}": v for k, v in r_stats(orb.loc[orb.block == test, "r"]).items()}})
    wf = pd.concat(oos) if oos else pd.DataFrame()
    picks = pd.DataFrame(picks)
    print("\nwalk-forward picks:\n" + picks.round(3).to_string(index=False))
    tests = range(TRAIN_BLOCKS, len(BLOCKS))
    print("\nOOS pooled  mean-reversion WF:", {k: round(v, 3) for k, v in r_stats(wf["r"] if len(wf) else pd.Series(dtype=float)).items()},
          "| frozen ORB same blocks:", {k: round(v, 3) for k, v in r_stats(orb.loc[orb.block.isin(tests), "r"]).items()})
    # stability: in how many blocks is each config positive, and its neighbours
    stab = pd.DataFrame({name: [r_stats(t.loc[t.block == b, "r"])["mean_r"] for b in range(len(BLOCKS))]
                         for name, t in runs.items()}).T
    stab.columns = [f"B{b + 1}" for b in range(len(BLOCKS))]
    stab["blocks_positive"] = (stab > 0).sum(axis=1)
    stab["all_r"] = [runs[n]["r"].mean() for n in stab.index]
    stab["all_t"] = [r_stats(runs[n]["r"])["t"] for n in stab.index]
    print("\nstability (mean R per 6-month block):\n" + stab.round(3).sort_values("all_t", ascending=False).to_string())
    last = range(len(BLOCKS) - TRAIN_BLOCKS, len(BLOCKS))           # the 12 months before the next period
    scored = [(r_stats(t.loc[t.block.isin(last), "r"])["t"], n) for n, t in runs.items()
              if r_stats(t.loc[t.block.isin(last), "r"])["n"] >= 30 and t.loc[t.block.isin(last), "r"].mean() > 0]
    final = max(scored)[1] if scored else None
    print(f"\nwalk-forward pick for the next period (trained on the last 12 months): {final}")
    if out_dir:
        picks.to_csv(f"{out_dir}/mr_walkforward_picks.csv", index=False)
        stab.to_csv(f"{out_dir}/mr_stability.csv")
    return picks, stab, final


def stage_reproduce(frame, out_dir):
    """Re-run every reported NQ result under the audited execution model into a NEW folder."""
    import json
    import os

    from nq.audit import audit, execution_stats
    os.makedirs(out_dir, exist_ok=True)
    print("== ORB variants, train + validation ==")
    stage_orb(frame, f"{out_dir}/orb_variants_train_val.csv")
    print("\n== frozen 5m ORB, Apr 2025..Oct 2026 (previously examined) ==")
    trades, equity = run_orb(frame, "2025-04-01", "2026-10-21", range_minutes=5, risk_pct=0.5)
    rep = audit(trades, equity, 0.5)
    for k, v in rep.items():
        print(f"  {k:36s} {v}")
    trades.to_csv(f"{out_dir}/orb_frozen_late_trades.csv", index=False)
    print("\n== mean reversion walk-forward, engine fills ==")
    stage_mr(frame, out_dir=None)
    print("\n== mean reversion walk-forward, conservative (ambiguous bars re-booked as stops) ==")
    picks, stab, final = stage_mr(frame, out_dir=None, conservative=True)
    picks.to_csv(f"{out_dir}/mr_walkforward_picks_conservative.csv", index=False)
    stab.to_csv(f"{out_dir}/mr_stability_conservative.csv")
    print("\n== execution statistics ==")
    ex = execution_stats(frame)
    for k, v in ex.items():
        print(f"  {k:44s} {v}")
    with open(f"{out_dir}/summary.json", "w") as fh:
        json.dump({"orb_frozen_late": {k: (float(v) if isinstance(v, (int, float)) else v) for k, v in rep.items()},
                   "execution": ex}, fh, indent=2, default=str)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--out")
    p.add_argument("stage", choices=["orb", "mr", "reproduce"])
    a = p.parse_args(argv)
    frame = load_proxy(a.data, ("MNQ",))["MNQ"]
    if a.stage == "orb":
        stage_orb(frame, a.out)
    elif a.stage == "mr":
        stage_mr(frame, a.out)
    else:
        stage_reproduce(frame, a.out or "nq/results/execution_audit")


if __name__ == "__main__":
    main()
