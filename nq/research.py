"""NQ research runs: pre-registered variants on TRAIN + VALIDATION only.

    python -m nq.research --data DIR orb
"""
import argparse
import warnings

import pandas as pd

from nq.backtest import run_orb, summarize
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


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--out")
    p.add_argument("stage", choices=["orb"])
    a = p.parse_args(argv)
    frame = load_proxy(a.data, ("MNQ",))["MNQ"]
    stage_orb(frame, a.out)


if __name__ == "__main__":
    main()
