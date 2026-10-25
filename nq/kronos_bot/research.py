"""Kronos bot research stages (see PROTOCOL.md for the periods each stage may use).

    python -m nq.kronos_bot.research --cfd FILE signals      # DEV: literature signals, no Kronos

Signals are evaluated per session on 15-minute candles (index = candle CLOSE, New York):
* H1 market intraday momentum (Gao, Han, Li, Zhou 2018): sign(prev 16:00 close -> 10:00 close)
* H3 hedging-demand momentum (Baltussen, Da, Lammers, Martens 2021): sign(prev close -> 15:30)
Both trade the last half hour: in at the 15:30 close, out at the 16:00 close. Early-close
sessions are skipped. Costs per round trip, MNQ: 2 ticks slippage + 2 x $0.85 commission
= 0.5 + 0.85 = 1.35 index points per contract.
"""
import argparse
import os
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
NY = "America/New_York"
COST_PTS = 2 * 0.25 + 2 * 0.85 / 2.0
DEV = ("2016-11-15", "2021-12-31")
WF = ("2022-01-01", "2025-07-31")
RESERVED = ("2025-08-01", "2026-10-21")
OUT = "nq/kronos_bot/results"


def sessions(frame: pd.DataFrame) -> pd.DataFrame:
    """One row per regular session: previous close, 10:00 / 15:30 / 16:00 closes."""
    loc = frame.index.tz_convert(NY)
    f = frame.assign(day=loc.date, hhmm=loc.strftime("%H:%M"))
    last = f.groupby("day")["close"].last()
    piv = f[f.hhmm.isin(["10:00", "15:30", "16:00"])].pivot_table(index="day", columns="hhmm", values="close")
    s = piv.join(last.rename("last_close"))
    s["prev_close"] = s["last_close"].shift(1)
    s = s.dropna(subset=["10:00", "15:30", "16:00", "prev_close"])
    s["r_first"] = np.log(s["10:00"] / s["prev_close"])
    s["r_to_1530"] = np.log(s["15:30"] / s["prev_close"])
    s["last30_pts"] = s["16:00"] - s["15:30"]
    s.index = pd.to_datetime(s.index)
    return s


def evaluate(s: pd.DataFrame, signal: pd.Series, name: str) -> dict:
    sig = np.sign(signal)
    gross = sig * s.last30_pts
    net = gross - COST_PTS * (sig != 0)
    traded = sig != 0
    g, n = gross[traded], net[traded]
    return {"signal": name, "days": int(traded.sum()), "hit_rate_pct": float((g > 0).mean() * 100),
            "gross_pts": float(g.mean()), "net_pts": float(n.mean()),
            "t_gross": float(g.mean() / (g.std() / np.sqrt(len(g)))), "t_net": float(n.mean() / (n.std() / np.sqrt(len(n)))),
            "avg_abs_move_pts": float(s.last30_pts[traded].abs().mean())}


def stage_signals(cfd_path: str):
    from nq.data import load_mt_cfd
    f = load_mt_cfd(cfd_path)
    s = sessions(f)
    dev = s.loc[DEV[0]:DEV[1]]
    rows = [evaluate(dev, dev.r_first, "H1 prev close->10:00"),
            evaluate(dev, dev.r_to_1530, "H3 prev close->15:30"),
            evaluate(dev, pd.Series(1.0, index=dev.index), "always long (drift baseline)"),
            evaluate(dev, pd.Series(np.random.default_rng(0).choice([-1.0, 1.0], len(dev)), index=dev.index),
                     "random sign (seed 0)")]
    table = pd.DataFrame(rows).set_index("signal")
    by_year = pd.DataFrame({name: [evaluate(g, g[col], name)["net_pts"] for _, g in dev.groupby(dev.index.year)]
                            for name, col in (("H1", "r_first"), ("H3", "r_to_1530"))},
                           index=sorted(set(dev.index.year)))
    os.makedirs(OUT, exist_ok=True)
    table.to_csv(f"{OUT}/dev_signals.csv")
    by_year.to_csv(f"{OUT}/dev_signals_by_year.csv")
    print(table.round(3).to_string())
    print("\nnet points per trade by year:\n" + by_year.round(2).to_string())
    return table


# ---------------------------------------------------------------------------
# Kronos forecast skill vs simple baselines (DEV only)
# ---------------------------------------------------------------------------
HORIZONS = {"last30": ("15:30", "16:00"), "rest_of_day": ("10:00", "15:45")}   # fixed before any forecast


def decision_points(frame_all: pd.DataFrame, s: pd.DataFrame, start: str, end: str, horizon: str):
    """(decision timestamp, number of candles to forecast, realized log return) per DEV session."""
    t0, t1 = HORIZONS[horizon]
    out = []
    loc = frame_all.index.tz_convert(NY)
    for day in s.loc[start:end].index:
        d = day.strftime("%Y-%m-%d")
        a = pd.Timestamp(f"{d} {t0}", tz=NY)
        b = pd.Timestamp(f"{d} {t1}", tz=NY)
        if a not in frame_all.index or b not in frame_all.index:
            continue
        n = frame_all.index.get_loc(b) - frame_all.index.get_loc(a)
        out.append((a, n, float(np.log(frame_all.close.loc[b] / frame_all.close.loc[a]))))
    return out


def skill_table(df: pd.DataFrame, price_col: str) -> pd.DataFrame:
    """Direction and volatility skill of each predictor column against the realized return."""
    from scipy.stats import spearmanr
    rows = []
    real, absr = df["realized"], df["realized"].abs()
    cost = COST_PTS / df[price_col]                                # round-trip cost as a return
    for col in [c for c in df.columns if c.startswith(("dir_", "k_exp_ret", "k_median_ret"))]:
        sig = np.sign(df[col])
        traded = sig != 0
        gross = (sig * real)[traded]
        rows.append({"predictor": col, "kind": "direction", "n": int(traded.sum()),
                     "hit_pct": float((gross > 0).mean() * 100), "ic_spearman": float(spearmanr(df[col], real)[0]),
                     "gross_bp": float(gross.mean() * 1e4), "net_bp": float((gross - cost[traded]).mean() * 1e4),
                     "t_net": float((gross - cost[traded]).mean() / ((gross - cost[traded]).std() / np.sqrt(traded.sum())))})
    for col in [c for c in df.columns if c.startswith(("vol_", "k_disp", "k_range"))]:
        rows.append({"predictor": col, "kind": "volatility", "n": int(df[col].notna().sum()),
                     "ic_spearman": float(spearmanr(df[col], absr, nan_policy="omit")[0])})
    return pd.DataFrame(rows).set_index("predictor")


def stage_kronos_skill(cfd_path: str, size: str, horizon: str, n_samples: int, limit: int | None, tiny: bool,
                       context: int | None = None):
    from nq.data import load_mt_cfd
    from nq.kronos_bot.forecaster import KronosForecaster, cached_features
    regular = load_mt_cfd(cfd_path)
    allc = load_mt_cfd(cfd_path, session="all")
    s = sessions(regular)
    pts = decision_points(allc, s, *DEV, horizon)
    if limit:
        pts = pts[:limit]
    if tiny:                                                    # plumbing smoke test only
        import sys
        sys.path.insert(0, "tests")
        from test_kronos_forecaster import tiny as make_tiny
        k = make_tiny()
        cache = f"{OUT}/cache_TINY_{horizon}.parquet"
    else:
        k = KronosForecaster(size, n_samples=n_samples, context=context)
        cache = f"{OUT}/cache_{k.name}_{horizon}.parquet"
    horizon_n = pts[0][1]
    feats = cached_features(k, allc, [p[0] for p in pts if p[1] == horizon_n], horizon_n, cache)
    df = pd.DataFrame([{"ts": a, "realized": r} for a, _, r in pts]).set_index("ts").join(feats, how="inner")
    day = df.index.tz_convert(NY).normalize().tz_localize(None)
    df["price"] = allc.close.reindex(df.index).values
    # baselines (all known at the decision time)
    sd = s.reindex(day)
    df["dir_H1_first_30min"] = sd["r_first"].values
    df["dir_H3_prev_close_to_now"] = np.log(df["price"].values / sd["prev_close"].values)
    df["dir_always_long"] = 1.0
    prev_real = df["realized"].shift(1)
    df["vol_prev_day_same_window"] = prev_real.abs()
    df["vol_avg_20d_same_window"] = df["realized"].abs().shift(1).rolling(20, min_periods=10).mean()
    table = skill_table(df, "price")
    tag = "TINY_SMOKE_TEST" if tiny else f"{k.name}_{horizon}"
    table.to_csv(f"{OUT}/kronos_skill_dev_{tag}.csv")
    df.to_csv(f"{OUT}/kronos_forecasts_dev_{tag}.csv")
    print(f"{tag}: {len(df)} decisions\n" + table.round(3).to_string())


PERIODS = {"dev": DEV, "train": ("2016-11-15", "2019-12-31"), "validation": ("2020-01-01", "2021-12-31"), "oos": WF}


def stage_forecast(cfd_path: str, size: str, horizon: str, n_samples: int, context: int | None, period: str):
    """Fill the resumable forecast cache for one period. Prints progress only, never outcomes, so
    forecasts for a not-yet-evaluated period can be computed ahead of time without peeking."""
    from nq.data import load_mt_cfd
    from nq.kronos_bot.forecaster import KronosForecaster, cached_features
    allc = load_mt_cfd(cfd_path, session="all")
    pts = decision_points(allc, sessions(load_mt_cfd(cfd_path)), *PERIODS[period], horizon)
    k = KronosForecaster(size, n_samples=n_samples, context=context)
    n = max({p[1] for p in pts}, key=[p[1] for p in pts].count)          # the usual candle count
    feats = cached_features(k, allc, [p[0] for p in pts if p[1] == n], n, f"{OUT}/cache_{k.name}_{horizon}.parquet")
    print(f"{k.name} {horizon} {period}: cache now holds {len(feats)} forecasts")


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--cfd", required=True)
    p.add_argument("stage", choices=["signals", "kronos-skill", "forecast"])
    p.add_argument("--period", default="oos", choices=list(PERIODS), help="forecast stage only")
    p.add_argument("--size", default="mini", choices=["mini", "small"])
    p.add_argument("--horizon", default="last30", choices=list(HORIZONS))
    p.add_argument("--samples", type=int, default=16)
    p.add_argument("--context", type=int, help="candles of history (default: the checkpoint's maximum)")
    p.add_argument("--limit", type=int)
    p.add_argument("--tiny", action="store_true", help="random tiny model: pipeline smoke test only")
    a = p.parse_args(argv)
    if a.stage == "signals":
        stage_signals(a.cfd)
    elif a.stage == "forecast":
        stage_forecast(a.cfd, a.size, a.horizon, a.samples, a.context, a.period)
    else:
        stage_kronos_skill(a.cfd, a.size, a.horizon, a.samples, a.limit, a.tiny, a.context)


if __name__ == "__main__":
    main()
