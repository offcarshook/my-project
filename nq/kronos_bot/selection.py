"""Kronos trade selection, volatility-aware sizing and evaluation (pre-registered in PROTOCOL_v2.md).

    python -m nq.kronos_bot.selection --cfd FILE select   --window last30   # TRAIN only -> frozen config
    python -m nq.kronos_bot.selection --cfd FILE validate --window last30   # VALIDATION gate (fast evaluator)
    python -m nq.kronos_bot.selection --cfd FILE bot      --window last30   # Nautilus runs, every period

The fast evaluator (selection and the validation gate) books each session as direction x realized
log return minus the round-trip cost as a return (0 on no-trade days). The Nautilus runs add the
protective stop, risk sizing, leverage cap, guards and fills.
"""
import argparse
import json
import os
import subprocess
import warnings

import numpy as np
import pandas as pd

from nq.kronos_bot.research import COST_PTS, NY, OUT, PERIODS, decision_points, sessions

warnings.filterwarnings("ignore")
WINDOWS = {"last30": {"horizon": "last30", "model": "kronos-mini-ctx2048-n16", "entry": "15:30", "exit": "16:00"},
           "rest_of_day": {"horizon": "rest_of_day", "model": "kronos-mini-ctx512-n16", "entry": "10:00", "exit": "15:45"}}
SPAN = ("2016-11-15", "2025-07-31")
SPLITS = ("train", "validation", "oos")
CONF_GRID = (None, 1 / 16, 2 / 16, 3 / 16, 4 / 16)
MOVE_GRID = (0.0, 0.5, 1.0, 2.0)
MIN_TRADES = 150
BOT = dict(risk_pct=0.5, stop_atr=2.0, max_leverage=3.0, max_contracts=50)
CASH = 50_000.0
STRESS = {"base": 0.0, "+1 tick/side": 1.0, "2x cost": 2.7}      # extra $ per contract per round trip


# ---------------------------------------------------------------------------
# decision frame
# ---------------------------------------------------------------------------
def period_of(dates: pd.Series) -> np.ndarray:
    out = np.full(len(dates), "", dtype=object)
    for name in SPLITS:
        a, b = PERIODS[name]
        out[((dates >= pd.Timestamp(a)) & (dates <= pd.Timestamp(b))).to_numpy()] = name
    return out


def assemble(points, feats: pd.DataFrame, prices: pd.Series, s: pd.DataFrame) -> pd.DataFrame:
    """One row per decision: Kronos features, realized window return, and context known at the
    decision: v20 = mean |realized| of the previous 20 decisions, trend50 = sign of the change in
    the previous close over the 50 sessions before, H3 = previous close -> decision price."""
    df = pd.DataFrame([{"ts": a, "realized": r} for a, _, r in points]).set_index("ts").join(feats, how="inner")
    df["price"] = prices.reindex(df.index).values
    df["date"] = df.index.tz_convert(NY).normalize().tz_localize(None)
    sd = s.reindex(df["date"])
    df["H3"] = np.log(df["price"].values / sd["prev_close"].values)
    df["trend50"] = np.sign(np.log(s["prev_close"] / s["prev_close"].shift(50)).reindex(df["date"]).values)
    df["v20"] = df["realized"].abs().shift(1).rolling(20, min_periods=10).mean()
    df["period"] = period_of(df["date"])
    return df


def load(cfd_path: str, window: str) -> pd.DataFrame:
    from nq.data import load_mt_cfd
    w = WINDOWS[window]
    allc = load_mt_cfd(cfd_path, session="all")
    s = sessions(load_mt_cfd(cfd_path))
    feats = pd.read_parquet(f"{OUT}/cache_{w['model']}_{w['horizon']}.parquet")
    return assemble(decision_points(allc, s, *SPAN, w["horizon"]), feats, allc.close, s)


# ---------------------------------------------------------------------------
# signals, sizing, fast evaluator
# ---------------------------------------------------------------------------
def direction(df: pd.DataFrame, conf=None, move: float = 0.0) -> pd.Series:
    """Sign of the average forecast; 0 where the confidence or move-size filter says stay flat."""
    d = np.sign(df["k_exp_ret"])
    ok = d != 0
    if conf is not None:
        pc = df["k_prob_up"] - 0.5
        ok &= (np.sign(pc) == d) & (pc.abs() >= conf - 1e-12)
    if move > 0:
        ok &= df["k_exp_ret"].abs() * df["price"] >= move * COST_PTS
    return d.where(ok, 0.0)


def vol_mult(df: pd.DataFrame, v_ref: float) -> pd.Series:
    """Exposure multiplier in [0.25, 1]: falls when recent realized volatility is above v_ref."""
    return (v_ref / df["v20"]).clip(0.25, 1.0).fillna(1.0)


def net(df, d, mult=1.0, cost_mult=1.0) -> pd.Series:
    return (d * df["realized"] - COST_PTS * cost_mult / df["price"] * (d != 0)) * mult


def fast_stats(r: pd.Series, d: pd.Series) -> dict:
    t = r[d != 0]
    loss = -t[t < 0].sum()
    return {"trades": int((d != 0).sum()), "days": len(r),
            "win_pct_net": float((t > 0).mean() * 100) if len(t) else np.nan,
            "exp_bp": float(t.mean() * 1e4) if len(t) else np.nan,
            "t": float(t.mean() / (t.std() / np.sqrt(len(t)))) if len(t) > 1 else np.nan,
            "pf": float(t[t > 0].sum() / loss) if loss > 0 else np.nan,
            "sharpe": float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else np.nan,
            "total_pct": float(r.sum() * 100)}


def fast_extra(df, r: pd.Series, d: pd.Series) -> dict:
    """Uncertainty, drawdown and months for the fast evaluator (unit notional, returns summed)."""
    t = r[d != 0]
    half = 1.96 * t.std() / np.sqrt(len(t)) * 1e4 if len(t) > 1 else np.nan
    cum = r.cumsum()
    months = r.groupby(df["date"].dt.to_period("M")).sum()
    return {"exp_bp_ci95_low": t.mean() * 1e4 - half, "exp_bp_ci95_high": t.mean() * 1e4 + half,
            "max_dd_pct": float((cum - cum.cummax().clip(lower=0)).min() * 100),
            "month_mean_pct": months.mean() * 100, "months_positive_pct": (months > 0).mean() * 100,
            "month_worst_pct": months.min() * 100}


def variant_returns(df, v: dict, v_ref: float, cost_mult=1.0):
    d = direction(df, v["conf"], v["move"])
    return net(df, d, vol_mult(df, v_ref) if v.get("sized") else 1.0, cost_mult), d


# ---------------------------------------------------------------------------
# selection on TRAIN
# ---------------------------------------------------------------------------
def grid_table(tr: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for conf in CONF_GRID:
        for move in MOVE_GRID:
            d = direction(tr, conf, move)
            rows.append({"conf": conf, "move": move, "filters": int(conf is not None) + int(move > 0),
                         **fast_stats(net(tr, d), d)})
    return pd.DataFrame(rows)


def select(df: pd.DataFrame) -> dict:
    """Every choice from TRAIN rows only (PROTOCOL_v2.md, 'Selection')."""
    tr = df[df["period"] == "train"]
    g = grid_table(tr)
    elig = g[g["trades"] >= MIN_TRADES]

    def best(rows):
        rows = rows.dropna(subset=["sharpe"]).sort_values(["sharpe", "filters"], ascending=[False, True])
        return None if rows.empty else {"conf": None if pd.isna(rows.iloc[0]["conf"]) else float(rows.iloc[0]["conf"]),
                                        "move": float(rows.iloc[0]["move"])}

    b = float((tr["realized"] * tr["k_exp_ret"]).sum() / (tr["k_exp_ret"] ** 2).sum())
    v_ref = float(tr["v20"].median())
    variants = {"A": {"conf": None, "move": 0.0}, "B": best(elig[elig["conf"].notna() & (elig["move"] == 0)]),
                "C": best(elig[elig["move"] > 0])}
    variants["C-EV"] = {"conf": (variants["B"] or {}).get("conf"), "move": 1 / b if b > 0 else float("inf")}
    train = {}
    for name, v in variants.items():
        if v is not None:
            r, d = variant_returns(tr, v, v_ref)
            train[name] = fast_stats(r, d)
    cands = [n for n in ("B", "C", "C-EV") if n in train and train[n]["trades"] >= MIN_TRADES
             and not np.isnan(train[n]["sharpe"])]
    s_name = max(cands, key=lambda n: train[n]["sharpe"]) if cands else "A"
    variants["S"] = dict(variants[s_name], source=s_name)
    variants["D"] = dict(variants[s_name], source=s_name, sized=True)
    for name in ("S", "D"):
        r, d = variant_returns(tr, variants[name], v_ref)
        train[name] = fast_stats(r, d)
    final = "D" if train["D"]["sharpe"] >= train["S"]["sharpe"] else "S"
    ic = float(tr[["k_exp_ret", "realized"]].corr(method="spearman").iloc[0, 1])
    rejected = bool(ic <= 0 or not (g["sharpe"].max() > 0))
    return {"variants": variants, "final": final, "ev_slope_b": b, "v_ref": v_ref,
            "v20_terciles": [float(tr["v20"].quantile(1 / 3)), float(tr["v20"].quantile(2 / 3))],
            "train_ic": ic, "train_diagnostic_rejected": rejected, "train": train, "grid": g}


def diagnostics(df: pd.DataFrame, frozen: dict, period: str = "train") -> str:
    """Do the Kronos outputs carry different information? Is the edge drift / long bias? Does the
    volatility regime change the edge? (Reported; never used to change a rule.)"""
    from scipy.stats import spearmanr
    p = df[df["period"] == period].copy()
    p["k_prob_c"] = p["k_prob_up"] - 0.5
    p["k_snr"] = p["k_exp_ret"] / p["k_disp"].replace(0, np.nan)
    cols = ["k_exp_ret", "k_median_ret", "k_prob_c", "k_snr"]
    out = [f"== {period}: {len(p)} decisions"]
    out.append("IC (Spearman vs realized): " + ", ".join(f"{c} {spearmanr(p[c], p['realized'], nan_policy='omit')[0]:+.3f}" for c in cols))
    out.append("rank correlation between outputs:\n" + p[cols].corr(method="spearman").round(2).to_string())
    rk = p[cols + ["realized"]].rank()
    for c in ("k_median_ret", "k_prob_c", "k_snr"):                # incremental value over the mean
        beta = np.polyfit(rk["k_exp_ret"], rk[c], 1)
        resid = rk[c] - np.polyval(beta, rk["k_exp_ret"])
        out.append(f"IC of {c} after removing what k_exp_ret explains: {np.corrcoef(resid, rk['realized'])[0, 1]:+.3f}")
    d = direction(p)
    r = net(p, d)
    up = (p["realized"] > 0).mean() * 100
    out.append(f"window finished up on {up:.1f}% of days; Kronos went long on {(d > 0).mean() * 100:.1f}%")
    for name, mask in (("Kronos longs", d > 0), ("Kronos shorts", d < 0)):
        t = r[mask]
        out.append(f"{name}: n {len(t)}, hit {(t > 0).mean() * 100:.1f}%, net {t.mean() * 1e4:+.2f} bp, "
                   f"t {t.mean() / (t.std() / np.sqrt(len(t))):+.2f}")
    al = pd.Series(1.0, index=p.index)
    out.append("always long: " + _fmt(fast_stats(net(p, al), al)))
    h3 = np.sign(p["H3"])
    out.append("H3 (existing baseline): " + _fmt(fast_stats(net(p, h3), h3)))
    for tv in (1, -1):
        m = p["trend50"] == tv
        out.append(f"A in {'up' if tv > 0 else 'down'}-trend regime: " + _fmt(fast_stats(r[m], d[m])))
    s = frozen["variants"]["S"]
    ds = direction(p, s["conf"], s["move"])
    rs = net(p, ds)
    lo, hi = frozen["v20_terciles"]
    for name, m in (("low", p["v20"] <= lo), ("mid", (p["v20"] > lo) & (p["v20"] <= hi)), ("high", p["v20"] > hi)):
        out.append(f"S ({s['source']}) in {name}-volatility tercile: " + _fmt(fast_stats(rs[m], ds[m])))
    return "\n".join(out)


def _fmt(st: dict) -> str:
    return (f"trades {st['trades']}, net win {st['win_pct_net']:.1f}%, net {st['exp_bp']:+.2f} bp/trade (t {st['t']:+.2f}), "
            f"PF {st['pf']:.2f}, Sharpe {st['sharpe']:+.2f}")


# ---------------------------------------------------------------------------
# Nautilus evaluation
# ---------------------------------------------------------------------------
def bot_signals(df, v: dict, v_ref: float) -> tuple:
    d = direction(df, v["conf"], v["move"])
    m = vol_mult(df, v_ref) if v.get("sized") else pd.Series(1.0, index=df.index)
    return tuple((dt.strftime("%Y-%m-%d"), int(x), float(mm)) for dt, x, mm in zip(df["date"], d, m) if x != 0)


def trade_returns(trades: pd.DataFrame, extra_cost: float = 0.0, cash: float = CASH) -> pd.DataFrame:
    """Per-trade return on the equity before the trade, optionally with extra cost per contract."""
    t = trades.sort_values("exit_ts").copy()
    t["pnl"] = t["pnl"] - extra_cost * t["qty"]
    t["r"] = t["pnl"] / t["risk"]
    t["ret"] = t["pnl"] / (cash + t["pnl"].cumsum().shift(fill_value=0.0))
    t["date"] = t["entry_ts"].dt.tz_convert(NY).dt.tz_localize(None).dt.normalize()
    return t


def bot_stats(t: pd.DataFrame, start: str, end: str) -> dict:
    p = t[(t["date"] >= pd.Timestamp(start)) & (t["date"] <= pd.Timestamp(end))]
    months = pd.period_range(start, end, freq="M")
    if p.empty:
        return {"trades": 0}
    curve = (1 + p["ret"]).cumprod()
    peak = curve.cummax().clip(lower=1.0)
    mret = p.groupby(p["date"].dt.to_period("M"))["ret"].apply(lambda x: (1 + x).prod() - 1).reindex(months, fill_value=0.0)
    wins, losses = p["pnl"][p["pnl"] > 0], p["pnl"][p["pnl"] < 0]
    top = p["pnl"].nlargest(max(1, int(round(len(p) * 0.05)))).sum()
    rest = p.drop(p["pnl"].nlargest(10).index)
    return {"trades": len(p), "net_return_pct": (curve.iloc[-1] - 1) * 100,
            "month_mean_pct": mret.mean() * 100, "month_median_pct": mret.median() * 100,
            "months_positive_pct": (mret > 0).mean() * 100, "month_worst_pct": mret.min() * 100,
            "month_best_pct": mret.max() * 100, "max_dd_pct": ((curve - peak) / peak).min() * 100,
            "exp_r": p["r"].mean(), "t_r": p["r"].mean() / (p["r"].std() / np.sqrt(len(p))),
            "exp_usd": p["pnl"].mean(), "win_pct": (p["pnl"] > 0).mean() * 100,
            "win_loss_ratio": wins.mean() / -losses.mean() if len(losses) and len(wins) else np.nan,
            "pf": wins.sum() / -losses.sum() if len(losses) else np.nan,
            "top5pct_share_of_net": top / p["pnl"].sum() if p["pnl"].sum() > 0 else np.nan,
            "net_ex_best10_pct": ((1 + rest["ret"]).prod() - 1) * 100}


def run_bot(cfd_path: str, df: pd.DataFrame, window: str, frozen: dict, names, guards: bool = False):
    from nq.backtest import run_strategy
    from nq.data import load_mt_cfd
    from nq.kronos_bot.strategy import SessionSignalBot, SessionSignalConfig
    w = WINDOWS[window]
    frame = load_mt_cfd(cfd_path)
    holder = {}

    class Rec(SessionSignalBot):
        def __init__(self, config):
            super().__init__(config)
            holder["s"] = self
    out = {}
    for name in names:
        if name == "H3":
            sig = tuple((dt.strftime("%Y-%m-%d"), int(np.sign(x)), 1.0) for dt, x in zip(df["date"], df["H3"]) if np.sign(x) != 0)
        else:
            sig = bot_signals(df, frozen["variants"][name], frozen["v_ref"])
        lim = {} if guards else {"max_daily_loss_pct": 100.0, "max_total_dd_pct": 100.0}
        trades, _ = run_strategy(Rec, SessionSignalConfig, frame, *SPAN, bar_minutes=15, signals=sig,
                                 entry_time=w["entry"], exit_time=w["exit"], **BOT, **lim)
        out[name] = (trades, holder["s"].events)
    return out


def oos_gate(trades: pd.DataFrame) -> dict:
    """PROTOCOL_v2.md OOS gate, on guard-free Nautilus trades of one variant."""
    base, stressed = trade_returns(trades), trade_returns(trades, STRESS["+1 tick/side"])
    st = bot_stats(base, *PERIODS["oos"])
    years = [bot_stats(base, f"{y}-01-01", min(f"{y}-12-31", PERIODS["oos"][1])).get("net_return_pct", 0) for y in (2022, 2023, 2024, 2025)]
    checks = {"exp_r > 0 and t >= 1.65": st.get("exp_r", 0) > 0 and st.get("t_r", 0) >= 1.65,
              ">= 2 of 4 years positive": sum(y > 0 for y in years) >= 2,
              "exp_r > 0 at +1 tick/side": bot_stats(stressed, *PERIODS["oos"]).get("exp_r", 0) > 0}
    return {"checks": checks, "years_pct": years, "passed": all(checks.values())}


def report_bot(df, runs: dict, frozen: dict, tag: str) -> pd.DataFrame:
    rows, years = [], []
    lo, hi = frozen["v20_terciles"]
    ctx = df.set_index("date")[["trend50", "v20"]]
    for name, (trades, _) in runs.items():
        if trades.empty:
            continue
        for stress, extra in STRESS.items():
            t = trade_returns(trades, extra)
            for per in SPLITS:
                rows.append({"variant": name, "cost": stress, "period": per, **bot_stats(t, *PERIODS[per])})
            if stress == "base":
                for y in range(2016, 2026):
                    st = bot_stats(t, max(f"{y}-01-01", SPAN[0]), min(f"{y}-12-31", SPAN[1]))
                    years.append({"variant": name, "year": y, "net_return_pct": st.get("net_return_pct"),
                                  "trades": st["trades"], "exp_r": st.get("exp_r"), "max_dd_pct": st.get("max_dd_pct")})
                c = ctx.reindex(t["date"])
                t["trend"] = np.where(c["trend50"].values > 0, "up-trend", "down-trend")
                t["vol"] = pd.cut(c["v20"].values, [-np.inf, lo, hi, np.inf], labels=["low vol", "mid vol", "high vol"])
                t["period"] = period_of(t["date"])
                for col in ("trend", "vol"):
                    g = t.groupby(["period", col], observed=True)["r"].agg(["count", "mean"])
                    g.reset_index().assign(variant=name).to_csv(f"{OUT}/v2_{tag}_regime_{col}_{name}.csv", index=False)
    table = pd.DataFrame(rows)
    table.to_csv(f"{OUT}/v2_{tag}_bot_periods.csv", index=False)
    pd.DataFrame(years).pivot(index="year", columns="variant", values="net_return_pct").to_csv(f"{OUT}/v2_{tag}_bot_years.csv")
    return table


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def frozen_path(window: str) -> str:
    return f"{OUT}/v2_{window}_frozen.json"


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--cfd", required=True)
    p.add_argument("stage", choices=["select", "validate", "bot"])
    p.add_argument("--window", default="last30", choices=list(WINDOWS))
    a = p.parse_args(argv)
    df = load(a.cfd, a.window)
    tag = a.window
    if a.stage == "select":
        if os.path.exists(frozen_path(a.window)):
            raise SystemExit(f"{frozen_path(a.window)} exists: thresholds are frozen (delete it deliberately to redo)")
        sel = select(df)
        sel["grid"].to_csv(f"{OUT}/v2_{tag}_train_grid.csv", index=False)
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
        frozen = {k: v for k, v in sel.items() if k != "grid"}
        frozen.update(window=a.window, model=WINDOWS[a.window]["model"], train_period=PERIODS["train"], code_commit=commit,
                      train_rows=int((df["period"] == "train").sum()))
        with open(frozen_path(a.window), "w") as f:
            json.dump(frozen, f, indent=2, default=str)
        diag = diagnostics(df, frozen, "train")
        with open(f"{OUT}/v2_{tag}_train_diagnostics.txt", "w") as f:
            f.write(diag + "\n")
        print(sel["grid"].round(3).to_string())
        print(json.dumps({k: frozen[k] for k in ("variants", "final", "ev_slope_b", "v_ref", "train_ic",
                                                 "train_diagnostic_rejected")}, indent=1, default=str))
        print(pd.DataFrame(frozen["train"]).T.round(3).to_string())
        print(diag)
        return
    frozen = json.load(open(frozen_path(a.window)))
    once = {"validate": f"{OUT}/v2_{tag}_validation.csv", "bot": f"{OUT}/v2_{tag}_bot_periods.csv"}[a.stage]
    if os.path.exists(once):
        raise SystemExit(f"{once} exists: this evaluation is run once (results are preserved, not overwritten)")
    if a.stage == "validate":
        va = df[df["period"] == "validation"]
        rows, years = {}, []
        for name, v in frozen["variants"].items():
            if v is None:
                continue
            r, d = variant_returns(va, v, frozen["v_ref"])
            rows[name] = fast_stats(r, d) | fast_extra(va, r, d)
            for y in sorted(va["date"].dt.year.unique()):
                m = (va["date"].dt.year == y).to_numpy()
                years.append({"variant": name, "year": int(y), **fast_stats(r[m], d[m])})
        h3 = np.sign(va["H3"])
        rows["H3"] = fast_stats(net(va, h3), h3) | fast_extra(va, net(va, h3), h3)
        t = pd.DataFrame(rows).T
        t.to_csv(once)
        pd.DataFrame(years).to_csv(f"{OUT}/v2_{tag}_validation_years.csv", index=False)
        f = rows[frozen["final"]]
        checks = {"expectancy > 0": f["exp_bp"] > 0, "profit factor > 1": f["pf"] > 1, ">= 40 trades": f["trades"] >= 40}
        passed = all(checks.values())
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
        with open(f"{OUT}/v2_{tag}_validation_gate.json", "w") as fh:
            json.dump({"final_variant": frozen["final"], "checks": {k: bool(v) for k, v in checks.items()},
                       "passed": bool(passed), "code_commit": commit, "period": PERIODS["validation"]}, fh, indent=2)
        print(t.round(3).to_string())
        print(pd.DataFrame(years).set_index(["variant", "year"])[["trades", "exp_bp", "t", "pf"]].round(2).to_string())
        print(f"final variant {frozen['final']}: VALIDATION gate {'PASSED' if passed else 'FAILED'} {checks}")
        diag = diagnostics(df, frozen, "validation")
        with open(f"{OUT}/v2_{tag}_validation_diagnostics.txt", "w") as fh:
            fh.write(diag + "\n")
        print(diag)
        return
    names = ["H3", "A", "B", "C", "C-EV", "S", "D"]
    names = [n for n in names if n == "H3" or frozen["variants"].get(n)]
    runs = run_bot(a.cfd, df, a.window, frozen, names)
    table = report_bot(df, runs, frozen, tag)
    guarded = run_bot(a.cfd, df, a.window, frozen, [frozen["final"]], guards=True)[frozen["final"]]
    gt = trade_returns(guarded[0])
    g = pd.DataFrame([{"period": per, **bot_stats(gt, *PERIODS[per])} for per in SPLITS])
    g.to_csv(f"{OUT}/v2_{tag}_final_guards_on.csv", index=False)
    pd.DataFrame(guarded[1]).to_csv(f"{OUT}/v2_{tag}_final_guard_events.csv", index=False)
    for name, (trades, _) in runs.items():
        trades.to_csv(f"{OUT}/v2_{tag}_trades_{name}.csv", index=False)
    cols = ["variant", "period", "trades", "net_return_pct", "month_mean_pct", "pf", "exp_r", "t_r", "max_dd_pct"]
    print(table[table["cost"] == "base"][cols].round(3).to_string())
    print("\nfinal variant with guards on:\n" + g.round(3).to_string())
    gates = {name: oos_gate(trades) for name, (trades, _) in runs.items() if not trades.empty}
    with open(f"{OUT}/v2_{tag}_oos_gates.json", "w") as f:
        json.dump(gates, f, indent=2)
    for name, gt_ in gates.items():
        print(f"OOS gate {name}: {'PASSED' if gt_['passed'] else 'FAILED'} {gt_['checks']} years {np.round(gt_['years_pct'], 2)}")


if __name__ == "__main__":
    main()
