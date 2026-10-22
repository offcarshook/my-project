"""ORB research study: diagnose the opening range breakout and test a small, pre-registered set
of one-change variants, on the TRAIN and VALIDATION periods only.

    python research/orb_study.py --config CFG --data DIR --out DIR baseline
    python research/orb_study.py ... diagnose      # where the baseline makes and loses money
    python research/orb_study.py ... variants      # pre-registered one-change variants
    python research/orb_study.py ... robustness    # cost stress, parameter nudges, leave-one-ETF-out
    python research/orb_study.py ... challenge     # prop challenge pass rates with uncertainty
    python research/orb_study.py ... final NAME    # the frozen config on the later periods, once

Every number comes from the same Simulator, costs and risk code the backtest and paper bot use.
The later periods (SEEN: already used to evaluate the original ORB; HOLDOUT: older data, if
provided) are only read by `final`.
"""
import argparse
import copy
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot import report  # noqa: E402
from bot.config import load_config  # noqa: E402
from bot.data import TF_MINUTES, load_local_bars, regular_hours, sessions_from_data  # noqa: E402
from bot.indicators import ny_index  # noqa: E402
from bot.pipeline import challenge_windows, variants  # noqa: E402
from bot.sim import Simulator  # noqa: E402
from bot.costs import CostModel  # noqa: E402
from bot.strategy import build_strategy, relative_volume  # noqa: E402

TRAIN = ("2022-03-21", "2024-03-31")
VAL = ("2024-04-01", "2025-03-31")
SEEN = ("2025-04-01", "2026-10-21")      # used once already to evaluate the original ORB: not untouched
BASELINE = "ORB 15m, range stop, hold"


def period(p):
    return pd.Timestamp(p[0], tz="America/New_York"), pd.Timestamp(p[1], tz="America/New_York") + pd.Timedelta(days=1)


# ---------------------------------------------------------------------------
# data + runs
# ---------------------------------------------------------------------------
def load(cfg_path, data_dir):
    cfg = load_config(cfg_path)
    bars = load_local_bars(data_dir, cfg.watchlist)
    sessions = sessions_from_data(bars)
    bars = {s: regular_hours(df, sessions) for s, df in bars.items()}
    return cfg, bars, sessions


def baseline_cfg(cfg):
    """The original ORB exactly as the first comparison built it."""
    return copy.deepcopy(variants(cfg, ("orb",))[BASELINE])


def simulator(v, bars, sessions):
    bm = TF_MINUTES[v.strategy.timeframe]
    return Simulator(v, bars, build_strategy(v.strategy, v.risk.stop, bm, v.backtest.data_feed), bm, sessions)


def metrics(res, cash, months_from=None):
    m = report.summarize(res, cash)
    t = res.trades
    if len(t):
        gross = t["r"] + t["costs"] / t["risk"]
        m["expectancy_r_gross"] = gross.mean()
        m["avg_hold_min"] = (t["exit_ts"] - t["entry_ts"]).dt.total_seconds().mean() / 60
        mo = report.monthly(t, res.equity)
        if len(mo):
            m.update(month_mean_pct=mo["return_pct"].mean(), month_median_pct=mo["return_pct"].median(),
                     month_best_pct=mo["return_pct"].max(), month_worst_pct=mo["return_pct"].min(),
                     months_positive_pct=(mo["return_pct"] > 0).mean() * 100, months=len(mo))
        days = max(len(set(ny_index(res.equity.index).date)), 1)
        m["annualized_pct"] = ((res.equity["equity"].iloc[-1] / cash) ** (252 / days) - 1) * 100
        # same-candle entries: how many trades were opened together with another one
        m["clustered_pct"] = (t.groupby("entry_ts")["symbol"].transform("size") > 1).mean() * 100
    return m


def run_periods(v, bars, sessions, periods=(("train", TRAIN), ("validation", VAL))):
    sim = simulator(v, bars, sessions)
    cash = v.backtest.starting_cash
    out = {}
    for name, p in periods:
        res = sim.run(*period(p))
        out[name] = (res, metrics(res, cash))
    return sim, out


KEY_COLS = ["trades", "trades_per_session", "win_rate_pct", "avg_win", "avg_loss", "profit_factor",
            "expectancy_r_gross", "expectancy_r", "r_tstat", "cost_r", "net_return_pct", "annualized_pct",
            "month_mean_pct", "month_median_pct", "months_positive_pct", "month_best_pct", "month_worst_pct",
            "max_drawdown_pct", "longest_losing_streak", "avg_hold_min", "clustered_pct"]


def table(rows: dict) -> pd.DataFrame:
    df = pd.DataFrame(rows).T
    return df[[c for c in KEY_COLS if c in df]]


# ---------------------------------------------------------------------------
# trade features (everything known at the signal candle)
# ---------------------------------------------------------------------------
def daily_frame(df):
    local = ny_index(df.index)
    d = df.groupby(local.date).agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                                   close=("close", "last"))
    d.index = pd.to_datetime(d.index)
    prev_close = d["close"].shift(1)
    tr = pd.concat([d["high"] - d["low"], (d["high"] - prev_close).abs(), (d["low"] - prev_close).abs()], axis=1).max(axis=1)
    out = pd.DataFrame(index=d.index)
    out["prev_close"] = prev_close
    out["gap_pct"] = (d["open"] / prev_close - 1) * 100
    out["datr"] = tr.rolling(14, min_periods=10).mean().shift(1)                  # prior days only
    out["trend"] = np.sign(prev_close - d["close"].rolling(20, min_periods=15).mean().shift(1))
    ret = np.log(d["close"]).diff()
    rv = ret.rolling(20, min_periods=15).std().shift(1)
    out["vol_ratio"] = rv / rv.expanding(min_periods=60).median()
    return out


def add_features(trades, sim, bars, regime_sym="SPY"):
    if trades.empty:
        return trades
    t = trades.copy()
    daily = {s: daily_frame(df) for s, df in bars.items()}
    mkt = daily.get(regime_sym)
    rows = []
    for _, tr in t.iterrows():
        f = sim.frames[tr["symbol"]]
        i = f.index.get_loc(tr["entry_ts"])
        s = f.iloc[i - 1]                                        # the signal candle
        side = 1 if tr["side"] == "LONG" else -1
        level = s["or_high"] if side > 0 else s["or_low"]
        day = pd.Timestamp(ny_index(pd.DatetimeIndex([tr["entry_ts"]]))[0].date())
        dd = daily[tr["symbol"]].loc[day] if day in daily[tr["symbol"]].index else None
        md = mkt.loc[day] if mkt is not None and day in mkt.index else None
        path = f.loc[tr["entry_ts"]:tr["exit_ts"]]
        stop_dist = abs(tr["entry"] - tr["stop"])
        mfe = ((path["high"].max() - tr["entry"]) if side > 0 else (tr["entry"] - path["low"].min())) / stop_dist
        mae = ((tr["entry"] - path["low"].min()) if side > 0 else (path["high"].max() - tr["entry"])) / stop_dist
        rows.append({
            "entry_hhmm": ny_index(pd.DatetimeIndex([tr["entry_ts"]]))[0].strftime("%H:%M"),
            "dow": day.day_name()[:3],
            "strength_atr": (s["close"] - level) * side / s["atr"],
            "chase_r": (tr["entry"] - level) * side / stop_dist,      # how far past the level the fill was
            "rvol": s.get("rvol", np.nan),
            "width_datr": (s["or_high"] - s["or_low"]) / dd["datr"] if dd is not None else np.nan,
            "gap_with_pct": dd["gap_pct"] * side if dd is not None else np.nan,
            "own_trend": "with" if dd is not None and dd["trend"] == side else "against",
            "mkt_trend": "with" if md is not None and md["trend"] == side else "against",
            "mkt_vol": ("high" if md["vol_ratio"] > 1.2 else "low" if md["vol_ratio"] < 0.8 else "normal")
            if md is not None and not np.isnan(md["vol_ratio"]) else "n/a",
            "mfe_r": mfe, "mae_r": mae,
            "hold_min": (tr["exit_ts"] - tr["entry_ts"]).total_seconds() / 60,
            "fast_fail": tr["reason"] == "stop loss" and len(path) <= 3,
            "r_gross": tr["r"] + tr["costs"] / tr["risk"],
        })
    return pd.concat([t.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


def group_stats(t, col, cuts=None):
    """Mean R after costs per bucket. Numeric columns are cut at the TRAIN terciles (passed in)."""
    key = pd.cut(t[col], cuts, include_lowest=True).astype(str) if cuts is not None else t[col].astype(str)
    g = t.groupby(key)["r"]
    out = pd.DataFrame({"n": g.size(), "mean_r": g.mean(), "t": g.mean() / (g.std() / np.sqrt(g.size())),
                        "win_pct": t.groupby(key)["pnl"].apply(lambda x: (x > 0).mean() * 100)})
    return out


# ---------------------------------------------------------------------------
# stages
# ---------------------------------------------------------------------------
def stage_baseline(cfg, bars, sessions, out):
    v = baseline_cfg(cfg)
    sim, res = run_periods(v, bars, sessions)
    tab = table({f"{BASELINE} | {k}": m for k, (_, m) in res.items()})
    tab.to_csv(out / "baseline.csv")
    for k, (r, _) in res.items():
        r.trades.to_csv(out / f"baseline_trades_{k}.csv", index=False)
    print(tab.T.round(3).to_string())


def stage_diagnose(cfg, bars, sessions, out):
    v = baseline_cfg(cfg)
    sim, res = run_periods(v, bars, sessions)
    for s, f in sim.frames.items():
        f["rvol"] = relative_volume(f)
    feats = {k: add_features(r.trades, sim, bars) for k, (r, _) in res.items()}
    for k, t in feats.items():
        t.to_csv(out / f"diagnose_trades_{k}.csv", index=False)
    tr, va = feats["train"], feats["validation"]
    lines = ["# ORB baseline diagnosis (train vs validation)\n",
             "Mean R per trade after costs. Numeric features are split at the train terciles. A pattern",
             "only counts if it has the same sign in both periods and a plausible reason.\n"]
    tr["etf_side"] = tr["symbol"] + " " + tr["side"]
    va["etf_side"] = va["symbol"] + " " + va["side"]
    for col in ["symbol", "side", "etf_side", "entry_hhmm", "dow", "own_trend", "mkt_trend", "mkt_vol", "reason",
                "fast_fail", "strength_atr", "chase_r", "rvol", "width_datr", "gap_with_pct", "hold_min"]:
        cuts = None
        if pd.api.types.is_float_dtype(tr[col]):
            q = tr[col].quantile([0, 1 / 3, 2 / 3, 1]).to_numpy()
            cuts = np.unique(np.r_[-np.inf, q[1:-1], np.inf])
        if col == "entry_hhmm":
            for t in (tr, va):
                t["entry_bucket"] = np.select([t.entry_hhmm <= "09:50", t.entry_hhmm <= "10:15"],
                                              ["09:50 (first)", "09:55-10:15"], "10:20-11:30")
            col = "entry_bucket"
        a, b = group_stats(tr, col, cuts), group_stats(va, col, cuts)
        both = a.join(b, lsuffix="_train", rsuffix="_val", how="outer")
        lines += [f"## {col}\n", report.fmt_table(both.round(3))]
    for name, t in feats.items():
        lines.append(f"## exits and path ({name})\n")
        lines.append(f"* MFE (best move in R before exit): median {t.mfe_r.median():.2f}, "
                     f"{(t.mfe_r >= 1).mean() * 100:.0f}% reach +1R, {(t.mfe_r >= 2).mean() * 100:.0f}% reach +2R, "
                     f"{(t.mfe_r >= 3).mean() * 100:.0f}% reach +3R")
        lines.append(f"* stopped within 3 candles: {t.fast_fail.mean() * 100:.0f}% of trades")
        lines.append(f"* fill was on average {t.chase_r.mean():.2f}R past the breakout level")
        lines.append(f"* gross {t.r_gross.mean():.3f}R vs net {t.r.mean():.3f}R per trade\n")
    (out / "diagnosis.md").write_text("\n".join(lines))
    print("\n".join(lines))


# ---------------------------------------------------------------------------
# pre-registered one-change variants (fixed before looking at their results)
# ---------------------------------------------------------------------------
def variant_set(cfg):
    base = baseline_cfg(cfg)

    def orb(**params):
        v = copy.deepcopy(base)
        v.strategy.orb.update(params)
        return v

    def tweak(fn):
        v = copy.deepcopy(base)
        fn(v)
        return v

    def flatten_1230(v):
        v.strategy.flatten_at = "12:30"
        v.strategy.entry_end = min(v.strategy.entry_end, "12:30")

    def trailing(v):
        v.risk.trailing.enabled = True

    def target_2r(v):
        v.risk.reward_risk = 2.0

    def one_per_group(v):
        v.risk.max_per_group = 1

    def five_min(v):
        v.strategy.orb["minutes"] = 5
        v.strategy.entry_start = "09:35"

    return {
        "baseline (ORB 15m, range stop, hold)": base,
        "A1 breakout close +0.10 ATR": orb(buffer_atr=0.10),
        "A2 breakout close +0.25 ATR": orb(buffer_atr=0.25),
        "B1 relative volume >= 1.5": orb(min_rvol=1.5),
        "C1 with the daily trend": orb(trend="own"),
        "D1 retest entry (6 candles)": orb(entry="retest"),
        "E1 exit at 12:30": tweak(flatten_1230),
        "E2 trailing stop after +1R": tweak(trailing),
        "E3 ATR stop": orb(stop="atr"),
        "E4 2R target": tweak(target_2r),
        "F1 last entry 10:30": orb(last_entry="10:30"),
        "G1 max 1 per correlation group": tweak(one_per_group),
        "R5 5-minute range": tweak(five_min),
        "R30 30-minute range": orb(minutes=30),
        "R60 60-minute range": orb(minutes=60, last_entry="12:30"),
    }


def shortlist_rule(m_tr, m_va, base_min_t):
    """Profitable after costs in both periods, >= 2 trades/day, and a better weak-period t-stat
    than the baseline. (Costs x1.5 and parameter nudges are checked in `robustness`.)"""
    for m in (m_tr, m_va):
        if not m["trades"] or m["expectancy_r"] <= 0 or m["profit_factor"] <= 1:
            return False, "loses after costs"
        if m["trades_per_session"] < 2:
            return False, f"{m['trades_per_session']:.1f} trades/day"
    weak = min(m_tr["r_tstat"], m_va["r_tstat"])
    return (weak > base_min_t, f"weak-period t {weak:.2f} vs baseline {base_min_t:.2f}")


def stage_variants(cfg, bars, sessions, out):
    rows, verdicts = {}, {}
    base_min_t = None
    for name, v in variant_set(cfg).items():
        _, res = run_periods(v, bars, sessions)
        m_tr, m_va = res["train"][1], res["validation"][1]
        rows[f"{name} | train"], rows[f"{name} | validation"] = m_tr, m_va
        if base_min_t is None:
            base_min_t = min(m_tr["r_tstat"], m_va["r_tstat"])
            verdicts[name] = (False, "baseline")
        else:
            verdicts[name] = shortlist_rule(m_tr, m_va, base_min_t)
        print(f"{name:40s} train {m_tr['expectancy_r']:+.3f}R ({m_tr['trades_per_session']:.1f}/day) "
              f"val {m_va['expectancy_r']:+.3f}R  -> {verdicts[name]}", flush=True)
    tab = table(rows)
    tab.to_csv(out / "variants.csv")
    pd.DataFrame(verdicts, index=["shortlisted", "why"]).T.to_csv(out / "variants_verdicts.csv")
    entry = entry_event_study(cfg, bars)
    entry.to_csv(out / "entry_event_study.csv")
    print(entry.round(3).to_string())


def entry_event_study(cfg, bars, extra_stop_slippage_bps=(0,)):
    """A3: breakout entry on the candle CLOSE (next open, what the bot does) vs a resting STOP
    order at the range level (fills intraday, at the open if it gaps through). Same engine for
    both: first trade per symbol per day, range-opposite stop, exit at the flatten time, stop
    first when a candle touches both, same costs. No portfolio limits, so this isolates the entry
    rule; it is not a full backtest."""
    v = baseline_cfg(cfg)
    costs = CostModel(v.costs, v.spread_bps)
    p, st = v.strategy.orb, v.strategy
    rows = []
    for sym, df in bars.items():
        local = ny_index(df.index)
        frame = df.assign(day=local.date, hhmm=local.strftime("%H:%M"))
        for day, d in frame.groupby("day"):
            rng = d[(d.hhmm >= "09:30") & (d.hhmm < "09:45")]
            if len(rng) != 3:
                continue
            hi, lo = rng.high.max(), rng.low.min()
            mid = d.close.iloc[2]
            if not p["min_range_pct"] <= (hi - lo) / mid * 100 <= p["max_range_pct"]:
                continue
            rest = d[d.hhmm >= "09:45"].reset_index(drop=True)
            flat = rest.index[rest.hhmm >= st.flatten_at]
            if not len(flat):
                continue
            end = flat[0]
            for mode in ["close"] + [f"stop +{x}bp" if x else "stop" for x in extra_stop_slippage_bps]:
                trade = _first_trade(rest, end, hi, lo, mode.split()[0], p["last_entry"])
                if trade is None:
                    continue
                entry_i, side, ref = trade
                extra = float(mode.split("+")[1][:-2]) if "+" in mode else 0.0
                fill = costs.market_fill(sym, side, ref) + side * ref * extra / 10_000
                stop = lo if side > 0 else hi
                dist = (fill - stop) * side
                if dist <= 0:
                    continue
                dist = max(dist, fill * v.risk.stop.min_pct / 100)
                stop = fill - side * dist
                exit_px, reason = None, "end of day"
                for j in range(entry_i, end):
                    bar = rest.iloc[j]
                    if (side > 0 and bar.low <= stop) or (side < 0 and bar.high >= stop):
                        exit_px = costs.market_fill(sym, -side, min(bar.open, stop) if side > 0 else max(bar.open, stop))
                        reason = "stop loss"
                        break
                if exit_px is None:
                    exit_px = costs.market_fill(sym, -side, rest.open.iloc[end])
                gross_exit = rest.open.iloc[end] if reason == "end of day" else stop
                rows.append({"mode": mode, "day": pd.Timestamp(day), "symbol": sym, "side": side,
                             "r": (exit_px - fill) * side / dist, "r_gross": (gross_exit - ref) * side / dist,
                             "reason": reason})
    t = pd.DataFrame(rows)
    out = []
    for name, (a, b) in (("train", TRAIN), ("validation", VAL)):
        sel = t[(t.day >= a) & (t.day <= b)]
        g = sel.groupby("mode")
        out.append(pd.DataFrame({"period": name, "trades": g.size(), "mean_r": g.r.mean(),
                                 "t": g.r.mean() / (g.r.std() / np.sqrt(g.size())),
                                 "mean_r_gross": g.r_gross.mean(), "win_pct": g.r.apply(lambda x: (x > 0).mean() * 100),
                                 "stopped_pct": g.reason.apply(lambda x: (x == "stop loss").mean() * 100)}))
    return pd.concat(out)


def _first_trade(rest, end, hi, lo, mode, last_entry):
    """(candle index of the entry, side, reference price) for the first breakout of the day."""
    for j in range(end):
        bar = rest.iloc[j]
        if mode == "close":
            if j + 1 >= end or rest.hhmm.iloc[j + 1] >= last_entry:
                return None
            prev = rest.close.iloc[j - 1] if j else (hi + lo) / 2
            if bar.close > hi and prev <= hi:
                return j + 1, 1, rest.open.iloc[j + 1]
            if bar.close < lo and prev >= lo:
                return j + 1, -1, rest.open.iloc[j + 1]
        else:
            if bar.hhmm >= last_entry:
                return None
            up, down = bar.high >= hi, bar.low <= lo
            if up and down:
                return None                                   # both sides in one candle: ambiguous, skipped
            if up:
                return j, 1, max(bar.open, hi)
            if down:
                return j, -1, min(bar.open, lo)
    return None


# ---------------------------------------------------------------------------
# robustness of the shortlisted variants
# ---------------------------------------------------------------------------
def candidates(cfg):
    vs = variant_set(cfg)
    combo = copy.deepcopy(vs["A2 breakout close +0.25 ATR"])
    combo.strategy.orb["last_entry"] = "10:30"
    return {"baseline": vs["baseline (ORB 15m, range stop, hold)"],
            "A1 +0.10 ATR": vs["A1 breakout close +0.10 ATR"], "A2 +0.25 ATR": vs["A2 breakout close +0.25 ATR"],
            "F1 last entry 10:30": vs["F1 last entry 10:30"], "A2 + F1": combo,
            "D1 retest (fewer trades)": vs["D1 retest entry (6 candles)"]}


NUDGES = {   # +-25% on the one parameter each candidate changed (+-15 min for times)
    "baseline": [("orb", "min_range_pct", 0.11), ("orb", "min_range_pct", 0.19)],
    "A1 +0.10 ATR": [("orb", "buffer_atr", 0.075), ("orb", "buffer_atr", 0.125)],
    "A2 +0.25 ATR": [("orb", "buffer_atr", 0.19), ("orb", "buffer_atr", 0.31)],
    "F1 last entry 10:30": [("orb", "last_entry", "10:15"), ("orb", "last_entry", "10:45")],
    "A2 + F1": [("orb", "buffer_atr", 0.19), ("orb", "buffer_atr", 0.31), ("orb", "last_entry", "10:15"),
                ("orb", "last_entry", "10:45")],
    "D1 retest (fewer trades)": [("orb", "retest_bars", 4), ("orb", "retest_bars", 8)],
}


def with_costs(v, mult):
    v = copy.deepcopy(v)
    v.costs.slippage_bps *= mult
    v.costs.default_spread_bps *= mult
    for inst in v.instruments.values():
        if inst.spread_bps is not None:
            inst.spread_bps *= mult
    return v


class RandomSide:
    """Same entry candles as the wrapped strategy, coin-flip direction, same stop distance:
    what the breakout's direction is worth."""

    def __init__(self, inner, seed):
        self.inner, self.seed, self.warmup = inner, seed, inner.warmup

    def compute(self, df):
        out = self.inner.compute(df)
        rng = np.random.default_rng(self.seed + int(df["close"].iloc[0] * 100) % 1000)
        on = out["signal"].isin(["BUY", "SELL"]).to_numpy()
        flip = rng.random(len(out)) < 0.5
        side = np.where(out["signal"] == "BUY", 1, -1) * np.where(flip, -1, 1)
        out["signal"] = np.where(on, np.where(side > 0, "BUY", "SELL"), "HOLD")
        out["cross"] = 0
        return out


def short_metrics(v, bars, sessions, sim=None):
    sim = sim or simulator(v, bars, sessions)
    cash = v.backtest.starting_cash
    m = {p: metrics(sim.run(*period(rng)), cash) for p, rng in (("train", TRAIN), ("val", VAL))}
    return {"train_r": m["train"]["expectancy_r"], "val_r": m["val"]["expectancy_r"],
            "train_t": m["train"]["r_tstat"], "val_t": m["val"]["r_tstat"],
            "trades_day": (m["train"]["trades_per_session"] + m["val"]["trades_per_session"]) / 2}


def stage_robustness(cfg, bars, sessions, out):
    rows = []
    for name, v in candidates(cfg).items():
        rows.append({"candidate": name, "test": "as is", **short_metrics(v, bars, sessions)})
        for mult in (1.5, 2.0):
            rows.append({"candidate": name, "test": f"costs x{mult:g}", **short_metrics(with_costs(v, mult), bars, sessions)})
        for section, key, value in NUDGES[name]:
            n = copy.deepcopy(v)
            getattr(n.strategy, section)[key] = value
            rows.append({"candidate": name, "test": f"{key}={value}", **short_metrics(n, bars, sessions)})
        for sym in bars:
            rows.append({"candidate": name, "test": f"without {sym}",
                         **short_metrics(v, {s: b for s, b in bars.items() if s != sym}, sessions)})
        print(pd.DataFrame([r for r in rows if r["candidate"] == name]).round(3).to_string(index=False), flush=True)
    base = candidates(cfg)["baseline"]
    bm = TF_MINUTES[base.strategy.timeframe]
    for seed in range(10):
        strat = RandomSide(build_strategy(base.strategy, base.risk.stop, bm, base.backtest.data_feed), seed)
        sim = Simulator(base, bars, strat, bm, sessions)
        rows.append({"candidate": "random direction", "test": f"seed {seed}", **short_metrics(base, bars, sessions, sim)})
    rnd = pd.DataFrame([r for r in rows if r["candidate"] == "random direction"])
    print(rnd.round(3).to_string(index=False))
    print(f"random direction: train {rnd.train_r.mean():+.3f}R (max {rnd.train_r.max():+.3f}), "
          f"val {rnd.val_r.mean():+.3f}R (max {rnd.val_r.max():+.3f})")
    pd.DataFrame(rows).to_csv(out / "robustness.csv", index=False)
    stress = entry_event_study(cfg, bars, extra_stop_slippage_bps=(0, 1, 2, 3))
    stress.to_csv(out / "entry_event_study_stress.csv")
    print(stress.round(3).to_string())


# ---------------------------------------------------------------------------
# prop challenge mode: pass rates with uncertainty, against luck, identical rules
# ---------------------------------------------------------------------------
CHALLENGE = {"profit_target_pct": 6.0, "max_daily_loss_pct": 5.0, "max_total_loss_pct": 10.0,
             "drawdown_type": "static", "daily_loss_base": "initial", "window_days": 30,
             "min_trading_days": 3, "consistency_pct": 0.0}
RISK_GRID = (0.25, 0.375, 0.5, 0.75)


def challenge_cfg(v, risk):
    v = copy.deepcopy(v)
    for k, x in CHALLENGE.items():
        setattr(v.challenge, k, x)
    v.risk.risk_per_trade_pct = risk
    v.risk.max_gross_exposure = max(v.risk.max_gross_exposure, v.backtest.sweep_max_gross_exposure)
    v.backtest.challenge_every_n_days = 1          # a fresh challenge every session
    return v


def wilson(p, n, z=1.96):
    if n <= 0:
        return np.nan, np.nan
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(centre - half, 0) * 100, min(centre + half, 1) * 100


def challenge_rows(name, v, bars, sessions, periods, risks=RISK_GRID):
    from bot.pipeline import luck_baseline
    rows = []
    for risk in risks:
        c = challenge_cfg(v, risk)
        sim = simulator(c, bars, sessions)
        for pname, rng in periods:
            a, b = period(rng)
            w = challenge_windows(sim, a, b - pd.Timedelta(days=1), c)
            ch = report.challenge_summary(w)
            n = len(w)
            n_eff = len(set(ny_index(sim.index[(sim.index >= a) & (sim.index < b)]).date)) / 21   # non-overlapping windows
            lo_ci, hi_ci = wilson(ch.get("pass_pct", 0) / 100, n_eff)
            res = sim.run(a, b)
            mc = zero = np.nan
            if len(res.trades) >= 20:
                real, z = luck_baseline(res.trades, res.equity["equity"], c.backtest.starting_cash, c)
                mc, zero = real.pass_rate * 100, z.pass_rate * 100
            rows.append({"candidate": name, "risk_pct": risk, "period": pname, "windows": n, "independent_windows": n_eff,
                         "pass_pct": ch.get("pass_pct", 0), "pass_ci95": f"{lo_ci:.0f}-{hi_ci:.0f}",
                         "fail_pct": ch.get("fail_pct", 0), "timeout_pct": ch.get("timeout_pct", 0),
                         "median_days_to_pass": ch.get("median_days_to_pass", np.nan),
                         "mc_pass_pct": mc, "zero_edge_pass_pct": zero, "edge_margin_pct": mc - zero})
            print({k: (round(x, 2) if isinstance(x, float) else x) for k, x in rows[-1].items()}, flush=True)
    return rows


def stage_challenge(cfg, bars, sessions, out, names=("baseline", "A2 + F1")):
    cands = candidates(cfg)
    rows = []
    for name in names:
        rows += challenge_rows(name, cands[name], bars, sessions, (("train", TRAIN), ("validation", VAL)))
    df = pd.DataFrame(rows)
    df.to_csv(out / "challenge.csv", index=False)
    print(df.round(2).to_string(index=False))


def stage_final(cfg, bars, sessions, out, frozen_path):
    """The frozen config (and the baseline, same rules) on the SEEN period, once."""
    frozen = json.loads(Path(frozen_path).read_text())
    cands = candidates(cfg)
    lines = [f"# Frozen ORB on {SEEN[0]} -> {SEEN[1]} (seen before: not an untouched test)\n",
             f"Frozen: `{frozen['candidate']}` at {frozen['risk_pct']}% risk, frozen in commit {frozen.get('commit', '?')}.\n"]
    rows, tables = {}, []
    for name, risk in ((frozen["candidate"], frozen["risk_pct"]), ("baseline", frozen["risk_pct"])):
        v = challenge_cfg(cands[name], risk)
        sim = simulator(v, bars, sessions)
        res = sim.run(*period(SEEN))
        m = metrics(res, v.backtest.starting_cash)
        rows[f"{name} @ {risk}%"] = m
        inst = report.per_instrument(res.trades, m["sessions"], v.backtest.starting_cash)
        months = report.monthly(res.trades, res.equity)
        tables += [f"## {name}: per ETF\n", report.fmt_table(inst.round(3)),
                   f"## {name}: by month\n", report.fmt_table(months.round(2))]
        side = res.trades.groupby("side")["r"].agg(["size", "mean"])
        tables += [f"## {name}: by side (R)\n", report.fmt_table(side.round(3))]
        regime_sym = cfg.backtest.regime_symbol
        if regime_sym in bars:
            daily = bars[regime_sym]["close"].groupby(ny_index(bars[regime_sym].index).date).last()
            tables += [f"## {name}: by market regime (labels from past data only)\n",
                       report.fmt_table(report.by_regime(res.trades, report.regimes(daily)).round(3))]
        res.trades.to_csv(out / f"final_trades_{name.replace(' ', '_')}.csv", index=False)
    ch = pd.DataFrame(challenge_rows(frozen["candidate"], cands[frozen["candidate"]], bars, sessions, (("seen", SEEN),),
                                     risks=(frozen["risk_pct"],))
                      + challenge_rows("baseline", cands["baseline"], bars, sessions, (("seen", SEEN),),
                                       risks=(frozen["risk_pct"],)))
    lines += ["## Strategy metrics\n", report.fmt_table(table(rows).T.round(3)),
              "## Prop challenges (fresh start every session)\n", report.fmt_table(ch.round(2).set_index("candidate"))]
    lines += tables
    (out / "final.md").write_text("\n".join(lines))
    ch.to_csv(out / "final_challenge.csv", index=False)
    print("\n".join(lines))


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--data", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("stage", choices=["baseline", "diagnose", "variants", "robustness", "challenge", "final"])
    p.add_argument("--frozen", default=str(Path(__file__).with_name("frozen_orb.json")))
    p.add_argument("--names", default="baseline,A2 + F1", help="challenge: candidates to compare")
    a = p.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cfg, bars, sessions = load(a.config, a.data)
    if a.stage == "final":
        stage_final(cfg, bars, sessions, out, a.frozen)
    elif a.stage == "challenge":
        stage_challenge(cfg, bars, sessions, out, tuple(a.names.split(",")))
    else:
        {"baseline": stage_baseline, "diagnose": stage_diagnose, "variants": stage_variants,
         "robustness": stage_robustness}[a.stage](cfg, bars, sessions, out)


if __name__ == "__main__":
    main()
