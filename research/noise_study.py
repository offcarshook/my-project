"""Noise-area intraday momentum study, same discipline as the ORB study: three variants fixed in
advance, picked on TRAIN + VALIDATION only, robustness checked, frozen, then run ONCE on the
2025-04 -> 2026-10 period (seen before by ORB, never by this strategy).

    python research/noise_study.py --config CFG --data DIR --out DIR variants
    python research/noise_study.py ... robustness NAME
    python research/noise_study.py ... final        # reads research/frozen_noise.json
"""
import argparse
import copy
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orb_study import (SEEN, challenge_rows, load, metrics, period, run_periods, short_metrics, simulator,  # noqa: E402
                       table, with_costs)

from bot import report  # noqa: E402
from bot.indicators import ny_index  # noqa: E402
from bot.pipeline import variants  # noqa: E402


def base_cfg(cfg):
    """config.yaml with the EMA-only filters off (liquidity as configured), hold to the exit rule."""
    v = copy.deepcopy(variants(cfg, ("orb",))["ORB 15m, range stop, hold"])
    v.strategy.type = "noise_breakout"
    v.risk.reward_risk = 0
    return v


def variant_set(cfg):
    def make(**params):
        v = base_cfg(cfg)
        v.strategy.noise.update(params)
        return v
    return {"N1 paper rules (14d band, 30-min checks, VWAP)": make(),
            "N2 wider band (1.25x)": make(band_mult=1.25),
            "N3 hourly checks": make(check_minutes=60)}


def stage_variants(cfg, bars, sessions, out):
    rows, best = {}, None
    for name, v in variant_set(cfg).items():
        _, res = run_periods(v, bars, sessions)
        m_tr, m_va = res["train"][1], res["validation"][1]
        rows[f"{name} | train"], rows[f"{name} | validation"] = m_tr, m_va
        ok = all(m["trades"] and m["expectancy_r"] > 0 and m["profit_factor"] > 1 and m["trades_per_session"] >= 1
                 for m in (m_tr, m_va))
        weak = min(m_tr["r_tstat"], m_va["r_tstat"])
        print(f"{name:48s} train {m_tr['expectancy_r']:+.3f}R ({m_tr['trades_per_session']:.1f}/day, "
              f"{m_tr['net_return_pct']:+.1f}%)  val {m_va['expectancy_r']:+.3f}R ({m_va['net_return_pct']:+.1f}%)  "
              f"eligible={ok} weak t={weak:.2f}", flush=True)
        if ok and (best is None or weak > best[1]):
            best = (name, weak)
    tab = table(rows)
    tab.to_csv(out / "noise_variants.csv")
    print(tab.T.round(3).to_string())
    print(f"\nbest eligible: {best}")


def stage_robustness(cfg, bars, sessions, out, name):
    v = variant_set(cfg)[name]
    rows = [{"test": "as is", **short_metrics(v, bars, sessions)}]
    for mult in (1.5, 2.0):
        rows.append({"test": f"costs x{mult:g}", **short_metrics(with_costs(v, mult), bars, sessions)})
    for key, value in (("lookback", 10), ("lookback", 18), ("band_mult", v.strategy.noise["band_mult"] * 0.85),
                       ("band_mult", v.strategy.noise["band_mult"] * 1.15)):
        n = copy.deepcopy(v)
        n.strategy.noise[key] = value
        rows.append({"test": f"{key}={value:g}", **short_metrics(n, bars, sessions)})
    for sym in bars:
        rows.append({"test": f"without {sym}", **short_metrics(v, {s: b for s, b in bars.items() if s != sym}, sessions)})
    df = pd.DataFrame(rows)
    df.to_csv(out / "noise_robustness.csv", index=False)
    print(df.round(3).to_string(index=False))
    ch = pd.DataFrame(challenge_rows(name, v, bars, sessions, (("train", ("2022-03-21", "2024-03-31")),
                                                               ("validation", ("2024-04-01", "2025-03-31")))))
    ch.to_csv(out / "noise_challenge.csv", index=False)
    print(ch.round(2).to_string(index=False))


def stage_final(cfg, bars, sessions, out, frozen_path):
    frozen = json.loads(Path(frozen_path).read_text())
    v = variant_set(cfg)[frozen["candidate"]]
    from orb_study import challenge_cfg
    c = challenge_cfg(v, frozen["risk_pct"])
    sim = simulator(c, bars, sessions)
    res = sim.run(*period(SEEN))
    m = metrics(res, c.backtest.starting_cash)
    inst = report.per_instrument(res.trades, m["sessions"], c.backtest.starting_cash)
    months = report.monthly(res.trades, res.equity)
    daily = bars[cfg.backtest.regime_symbol]["close"].groupby(ny_index(bars[cfg.backtest.regime_symbol].index).date).last()
    ch = pd.DataFrame(challenge_rows(frozen["candidate"], v, bars, sessions, (("seen", SEEN),), risks=(frozen["risk_pct"],)))
    lines = [f"# Frozen noise-area momentum on {SEEN[0]} -> {SEEN[1]}\n",
             f"Frozen: `{frozen['candidate']}` at {frozen['risk_pct']}% risk (commit {frozen.get('commit', '?')}). "
             "First run of this strategy on this period; ORB had been evaluated on it before.\n",
             "## Strategy metrics\n", report.fmt_table(table({frozen["candidate"]: m}).T.round(3)),
             "## Prop challenges (fresh start every session)\n", report.fmt_table(ch.round(2).set_index("candidate")),
             "## Per ETF\n", report.fmt_table(inst.round(3)), "## By month\n", report.fmt_table(months.round(2)),
             "## By side (R)\n", report.fmt_table(res.trades.groupby("side")["r"].agg(["size", "mean"]).round(3)),
             "## By market regime (labels from past data only)\n",
             report.fmt_table(report.by_regime(res.trades, report.regimes(daily)).round(3))]
    (out / "noise_final.md").write_text("\n".join(lines))
    res.trades.to_csv(out / "noise_final_trades.csv", index=False)
    print("\n".join(lines))


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--data", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("stage", choices=["variants", "robustness", "final"])
    p.add_argument("name", nargs="?")
    p.add_argument("--frozen", default=str(Path(__file__).with_name("frozen_noise.json")))
    a = p.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cfg, bars, sessions = load(a.config, a.data)
    if a.stage == "variants":
        stage_variants(cfg, bars, sessions, out)
    elif a.stage == "robustness":
        stage_robustness(cfg, bars, sessions, out, a.name)
    else:
        stage_final(cfg, bars, sessions, out, a.frozen)


if __name__ == "__main__":
    main()
