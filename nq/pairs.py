"""Phase 3: QQQ-SPY intraday pairs research (ETF data; NOT evidence about NQ-ES futures).

    python -m nq.pairs --data DIR --out nq/results audit      # data alignment / quality
    python -m nq.pairs --data DIR --out nq/results stats      # hedge-ratio stability, stationarity
    python -m nq.pairs --data DIR --out nq/results walkforward

Everything is causal: the hedge ratio for a session is estimated from the previous
`beta_sessions` sessions only; signals use completed candles; both legs fill together at the
next candle's open, and a signal is skipped if either leg has no next candle.
"""
import argparse
import glob
import re
import warnings
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
NY = "America/New_York"
RESEARCH_SPAN = ("2022-03-21", "2025-03-31")
BLOCKS = [("2022-03-21", "2022-09-30"), ("2022-10-01", "2023-03-31"), ("2023-04-01", "2023-09-30"),
          ("2023-10-01", "2024-03-31"), ("2024-04-01", "2024-09-30"), ("2024-10-01", "2025-03-31")]
TRAIN_BLOCKS = 2
# same cost basis as the ETF study: half the full spread + slippage per fill, SEC fee on sells
SPREAD_BPS = {"QQQ": 0.5, "SPY": 0.5}
SLIPPAGE_BPS = 1.0
SEC_FEE_BPS = 0.278


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------
def load_raw(data_dir: str, symbols=("QQQ", "SPY")) -> dict[str, pd.DataFrame]:
    out = {}
    for f in glob.glob(f"{data_dir}/*.parquet"):
        m = re.search(r"(?:^|[-_ ])([A-Z]{1,6})(?=[_.\- ]|$)", f.split("/")[-1])
        if m and m.group(1) in symbols:
            d = pd.read_parquet(f)
            d.columns = [c.lower() for c in d.columns]
            d = d.set_index(pd.DatetimeIndex(d.pop("datetime")).tz_localize(NY)).sort_index()
            out[m.group(1)] = d
    return out


def audit_data(raw: dict[str, pd.DataFrame]) -> dict:
    q, s = raw["QQQ"], raw["SPY"]
    rep = {}
    for sym, d in raw.items():
        hhmm = d.index.strftime("%H:%M")
        rep[f"{sym}_bars"] = len(d)
        rep[f"{sym}_first_last"] = f"{d.index[0]} .. {d.index[-1]}"
        rep[f"{sym}_duplicates"] = int(d.index.duplicated().sum())
        rep[f"{sym}_monotonic"] = bool(d.index.is_monotonic_increasing)
        rep[f"{sym}_outside_0930_1555"] = int(((hhmm < "09:30") | (hhmm > "15:55")).sum())
        rep[f"{sym}_off_5min_grid"] = int((d.index.minute % 5 != 0).sum() + (d.index.second != 0).sum())
        rep[f"{sym}_bad_ohlc"] = int(((d.high < d[["open", "close"]].max(axis=1)) |
                                      (d.low > d[["open", "close"]].min(axis=1)) | (d.low <= 0)).sum())
        rep[f"{sym}_zero_volume"] = int((d.volume <= 0).sum())
        rep[f"{sym}_sources"] = ",".join(sorted(d["source"].astype(str).unique())) if "source" in d else "?"
        per_day = d.groupby(d.index.date).size()
        rep[f"{sym}_sessions"] = len(per_day)
        rep[f"{sym}_sessions_not_78_bars"] = int((per_day != 78).sum())
        rep[f"{sym}_sessions_short_(<78)"] = int((per_day < 78).sum())
    both = q.index.intersection(s.index)
    rep["common_bars"] = len(both)
    rep["QQQ_only_bars"] = len(q.index.difference(s.index))
    rep["SPY_only_bars"] = len(s.index.difference(q.index))
    days_q, days_s = set(q.index.date), set(s.index.date)
    rep["sessions_in_one_only"] = len(days_q ^ days_s)
    # dividend adjustment check: an adjusted series' early prices would sit ~1.3%/yr x years below raw
    rep["SPY_first_open"] = float(s.open.iloc[0])
    rep["QQQ_first_open"] = float(q.open.iloc[0])
    return rep


def aligned(raw, span=None) -> pd.DataFrame:
    """Regular-hours candles where BOTH symbols have a candle (timestamps = candle start)."""
    q, s = raw["QQQ"], raw["SPY"]
    idx = q.index.intersection(s.index)
    hhmm = idx.strftime("%H:%M")
    idx = idx[(hhmm >= "09:30") & (hhmm <= "15:55")]
    df = pd.DataFrame({"q_open": q.open.reindex(idx), "q_close": q.close.reindex(idx),
                       "s_open": s.open.reindex(idx), "s_close": s.close.reindex(idx)}, index=idx)
    if span:
        df = df[(df.index >= pd.Timestamp(span[0], tz=NY)) &
                (df.index < pd.Timestamp(span[1], tz=NY) + pd.Timedelta(days=1))]
    df["day"] = df.index.date
    return df


# ---------------------------------------------------------------------------
# relationship statistics
# ---------------------------------------------------------------------------
def daily_betas(df: pd.DataFrame, beta_sessions: int = 20) -> pd.Series:
    """Hedge ratio per session from the PREVIOUS `beta_sessions` sessions' 5-minute log returns
    (OLS of QQQ returns on SPY returns, intraday returns only, no overnight)."""
    rq = np.log(df.q_close).groupby(df.day).diff()
    rs = np.log(df.s_close).groupby(df.day).diff()
    days = sorted(df.day.unique())
    cov = (rq * rs).groupby(df.day).sum()
    var = (rs * rs).groupby(df.day).sum()
    num = cov.rolling(beta_sessions).sum().shift(1)
    den = var.rolling(beta_sessions).sum().shift(1)
    return (num / den).reindex(days)


def relationship_stats(df: pd.DataFrame, beta_sessions=20) -> dict:
    from statsmodels.tsa.stattools import adfuller, coint
    beta = daily_betas(df, beta_sessions).dropna()
    rq = np.log(df.q_close).groupby(df.day).diff()
    rs = np.log(df.s_close).groupby(df.day).diff()
    out = {"corr_5min_returns": float(rq.corr(rs))}
    qtr = pd.Series(beta.values, index=pd.to_datetime(beta.index)).groupby(pd.Grouper(freq="QE"))
    out["beta_by_quarter"] = qtr.agg(["mean", "min", "max"]).round(3)
    out["beta_mean"], out["beta_std"] = float(beta.mean()), float(beta.std())
    out["beta_day_to_day_abs_change_median"] = float(beta.diff().abs().median())
    # intraday spread from the open with the causal hedge ratio: is it mean-reverting within the day?
    spread = intraday_spread(df, beta)
    pvals, half_lives = [], []
    for d, g in spread.dropna().groupby(df.loc[spread.dropna().index, "day"]):
        x = g.to_numpy()
        if len(x) < 60:
            continue
        pvals.append(adfuller(x, maxlag=1, regression="c", autolag=None)[1])
        b = np.polyfit(x[:-1], np.diff(x), 1)[0]
        half_lives.append(-np.log(2) / np.log(1 + b) if -1 < b < 0 else np.inf)
    pvals, half_lives = np.array(pvals), np.array(half_lives)
    out["sessions_tested"] = len(pvals)
    out["sessions_adf_reject_5pct"] = float((pvals < 0.05).mean())
    out["expected_false_rejects"] = 0.05
    out["median_intraday_half_life_bars"] = float(np.median(half_lives))
    # multi-day: Engle-Granger on daily closes, rolling 6-month windows
    daily = df.groupby("day")[["q_close", "s_close"]].last()
    daily.index = pd.to_datetime(daily.index)
    eg = []
    for a, b in BLOCKS:
        w = daily.loc[a:b]
        if len(w) > 60:
            eg.append({"window": f"{a}..{b}", "eg_pvalue": coint(np.log(w.q_close), np.log(w.s_close))[1]})
    out["engle_granger_6m"] = pd.DataFrame(eg)
    return out


def intraday_spread(df: pd.DataFrame, beta: pd.Series) -> pd.Series:
    """log-return of QQQ since the session open minus beta x SPY's (0 at the first close)."""
    b = df.day.map(beta)
    lq = np.log(df.q_close) - np.log(df.q_close).groupby(df.day).transform("first")
    ls = np.log(df.s_close) - np.log(df.s_close).groupby(df.day).transform("first")
    return lq - b * ls


# ---------------------------------------------------------------------------
# backtest
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PairConfig:
    name: str
    hedge: str = "rolling"        # rolling (causal OLS beta) | dollar (beta = 1)
    zscore: str = "window"        # window: (s - MA_L) / SD_L | open: s / (sigma_r * sqrt(n))
    window: int = 12
    entry_z: float = 2.0
    exit_z: float = 0.0
    stop_z: float = 3.5
    max_bars: int = 12
    first_entry: str = "10:30"
    last_entry: str = "15:00"
    flatten_at: str = "15:50"
    beta_sessions: int = 20


# fixed before any backtest; every one of these is reported
CONFIGS = [
    PairConfig("naive: dollar-neutral, 1h window z, k=2", hedge="dollar"),
    PairConfig("rolling beta, 1h window z, k=2"),
    PairConfig("rolling beta, 1h window z, k=2.5", entry_z=2.5, stop_z=4.0),
    PairConfig("rolling beta, 2h window z, k=2", window=24),
    PairConfig("rolling beta, open-anchored z, k=2", zscore="open"),
    PairConfig("rolling beta, open-anchored z, k=2.5", zscore="open", entry_z=2.5, stop_z=4.0),
]


def signals(df: pd.DataFrame, cfg: PairConfig) -> pd.DataFrame:
    beta = daily_betas(df, cfg.beta_sessions) if cfg.hedge == "rolling" else pd.Series(
        1.0, index=sorted(df.day.unique()))
    s = intraday_spread(df, beta)
    out = pd.DataFrame({"spread": s, "beta": df.day.map(beta)}, index=df.index)
    if cfg.zscore == "window":
        g = s.groupby(df.day)
        mu = g.transform(lambda x: x.rolling(cfg.window).mean())
        sd = g.transform(lambda x: x.rolling(cfg.window).std())
        out["z"] = (s - mu) / sd
    else:
        r = s.groupby(df.day).diff()
        daily_var = (r * r).groupby(df.day).sum() / r.groupby(df.day).count()
        sigma = np.sqrt(daily_var.rolling(cfg.beta_sessions).mean().shift(1))     # previous sessions
        n = df.groupby("day").cumcount()
        out["z"] = s / (df.day.map(sigma) * np.sqrt(n.clip(lower=1)))
    return out


def leg_cost_bps(sym: str, is_sell: bool, spread_mult=1.0, slip=SLIPPAGE_BPS) -> float:
    return SPREAD_BPS[sym] * spread_mult / 2 + slip + (SEC_FEE_BPS if is_sell else 0.0)


def backtest(df: pd.DataFrame, cfg: PairConfig, notional: float = 50_000, spread_mult=1.0,
             slip=SLIPPAGE_BPS) -> pd.DataFrame:
    """One trade = long (or short) `notional` of QQQ against beta x notional of SPY the other
    way. Signals on candle closes, both legs fill at the next candle's open."""
    sig = signals(df, cfg)
    t_end = df.index.strftime("%H:%M")       # candle START; a close-time decision acts at the next open
    z, beta = sig.z.to_numpy(), sig.beta.to_numpy()
    qo, so = df.q_open.to_numpy(), df.s_open.to_numpy()
    days = df.day.to_numpy()
    close_hhmm = (df.index + pd.Timedelta(minutes=5)).strftime("%H:%M")
    trades, pos = [], None
    for i in range(len(df) - 1):
        nxt_same_day = days[i + 1] == days[i]
        if pos is not None:
            pos["bars"] += 1
            zi = z[i]
            reason = None
            if close_hhmm[i] >= cfg.flatten_at or not nxt_same_day:
                reason = "end of day"
            elif not np.isnan(zi) and pos["dir"] * zi >= -cfg.exit_z:
                reason = "reverted"
            elif not np.isnan(zi) and abs(zi) >= cfg.stop_z:
                reason = "stop"
            elif pos["bars"] >= cfg.max_bars:
                reason = "time"
            if reason:
                j = i + 1 if nxt_same_day else i
                px_q, px_s = (qo[j], so[j]) if nxt_same_day else (df.q_close.iloc[i], df.s_close.iloc[i])
                trades.append(_close(pos, px_q, px_s, df.index[j], reason, spread_mult, slip))
                pos = None
            continue
        if not nxt_same_day or np.isnan(z[i]) or np.isnan(beta[i]):
            continue
        if not cfg.first_entry <= close_hhmm[i] <= cfg.last_entry:
            continue
        if z[i] >= cfg.entry_z:
            d = -1          # spread rich: short QQQ, long SPY
        elif z[i] <= -cfg.entry_z:
            d = 1
        else:
            continue
        pos = {"dir": d, "beta": beta[i], "entry_ts": df.index[i + 1], "q_in": qo[i + 1], "s_in": so[i + 1],
               "q_qty": notional / qo[i + 1], "s_qty": beta[i] * notional / so[i + 1], "bars": 0,
               "gross_notional": notional * (1 + abs(beta[i])), "z_in": z[i]}
    return pd.DataFrame(trades)


def _close(pos, px_q, px_s, ts, reason, spread_mult, slip):
    d = pos["dir"]
    pnl_q = d * pos["q_qty"] * (px_q - pos["q_in"])
    pnl_s = -d * pos["s_qty"] * (px_s - pos["s_in"])
    gross = pnl_q + pnl_s
    # four fills: QQQ in/out, SPY in/out; sells pay the SEC fee
    q_in_sell, s_in_sell = d < 0, d > 0
    cost = (pos["q_qty"] * pos["q_in"] * leg_cost_bps("QQQ", q_in_sell, spread_mult, slip) +
            pos["q_qty"] * px_q * leg_cost_bps("QQQ", not q_in_sell, spread_mult, slip) +
            pos["s_qty"] * pos["s_in"] * leg_cost_bps("SPY", s_in_sell, spread_mult, slip) +
            pos["s_qty"] * px_s * leg_cost_bps("SPY", not s_in_sell, spread_mult, slip)) / 10_000
    gn = pos["gross_notional"]
    return {"entry_ts": pos["entry_ts"], "exit_ts": ts, "dir": d, "beta": pos["beta"], "z_in": pos["z_in"],
            "bars": pos["bars"], "reason": reason, "gross_usd": gross, "cost_usd": cost, "net_usd": gross - cost,
            "gross_bps": gross / gn * 1e4, "net_bps": (gross - cost) / gn * 1e4}


def stats(t: pd.DataFrame, cash=50_000) -> dict:
    if t.empty:
        return {"trades": 0}
    n = len(t)
    net = t.net_bps
    eq = cash + t.net_usd.cumsum()
    peak = eq.cummax().clip(lower=cash)
    wins, losses = t.net_usd[t.net_usd > 0].sum(), -t.net_usd[t.net_usd < 0].sum()
    days = t.entry_ts.dt.date.nunique()
    return {"trades": n, "trades_per_day": n / max(days, 1), "gross_bps": t.gross_bps.mean(),
            "net_bps": net.mean(), "t_net": net.mean() / (net.std() / np.sqrt(n)) if n > 2 else np.nan,
            "win_pct": (t.net_usd > 0).mean() * 100, "profit_factor": wins / losses if losses else np.nan,
            "net_usd": t.net_usd.sum(), "max_dd_pct": ((eq - peak) / peak).min() * 100,
            "avg_bars": t.bars.mean()}


def block_of(ts):
    d = ts.tz_convert(NY).strftime("%Y-%m-%d")
    for i, (a, b) in enumerate(BLOCKS):
        if a <= d <= b:
            return i
    return -1


# ---------------------------------------------------------------------------
# stages
# ---------------------------------------------------------------------------
def stage_audit(raw, out):
    rep = audit_data(raw)
    for k, v in rep.items():
        print(f"{k:34s} {v}")
    pd.Series(rep).to_csv(f"{out}/pairs_data_audit.csv")


def stage_stats(raw, out):
    df = aligned(raw, RESEARCH_SPAN)
    st = relationship_stats(df)
    for k, v in st.items():
        print(f"{k}:\n{v}" if isinstance(v, pd.DataFrame) else f"{k:40s} {v}")
    st["beta_by_quarter"].to_csv(f"{out}/pairs_beta_by_quarter.csv")
    st["engle_granger_6m"].to_csv(f"{out}/pairs_engle_granger.csv", index=False)


def stage_walkforward(raw, out):
    from scipy import stats as sps
    df = aligned(raw, RESEARCH_SPAN)
    runs, rows = {}, []
    for cfg in CONFIGS:
        t = backtest(df, cfg)
        t["block"] = t.entry_ts.map(block_of)
        runs[cfg.name] = t
        row = {"config": cfg.name, **stats(t)}
        for b in range(len(BLOCKS)):
            row[f"B{b + 1}_net_bps"] = t.loc[t.block == b, "net_bps"].mean()
        for label, mult, slip in (("costs x1.5", 1.5, SLIPPAGE_BPS * 1.5), ("half slippage", 1.0, SLIPPAGE_BPS / 2),
                                  ("no slippage", 1.0, 0.0)):
            row[f"net_bps {label}"] = backtest(df, cfg, spread_mult=mult, slip=slip).net_bps.mean()
        rows.append(row)
        print({k: (round(v, 3) if isinstance(v, float) else v) for k, v in row.items()}, flush=True)
    table = pd.DataFrame(rows)
    # multiple testing: Bonferroni over every configuration tried
    k = len(CONFIGS)
    table["p_one_sided"] = 1 - sps.norm.cdf(table.t_net)
    table["bonferroni_p"] = (table.p_one_sided * k).clip(upper=1)
    table.to_csv(f"{out}/pairs_configs.csv", index=False)
    picks = []
    for test in range(TRAIN_BLOCKS, len(BLOCKS)):
        train = range(test - TRAIN_BLOCKS, test)
        scored = [(stats(t[t.block.isin(train)]).get("t_net", -np.inf), n) for n, t in runs.items()
                  if len(t[t.block.isin(train)]) >= 30 and t[t.block.isin(train)].net_bps.mean() > 0]
        pick = max(scored)[1] if scored else None
        tt = runs[pick][runs[pick].block == test] if pick else pd.DataFrame()
        picks.append({"test_block": f"{BLOCKS[test][0]}..{BLOCKS[test][1]}", "pick": pick or "(none: stay flat)",
                      **{f"oos_{k}": v for k, v in stats(tt).items()}})
    picks = pd.DataFrame(picks)
    picks.to_csv(f"{out}/pairs_walkforward.csv", index=False)
    print("\nwalk-forward:\n" + picks.round(3).to_string(index=False))
    # buy-and-hold benchmarks per block (close-to-close)
    daily = df.groupby("day")[["q_close", "s_close"]].last()
    daily.index = pd.to_datetime(daily.index)
    bench = []
    for a, b in BLOCKS:
        w = daily.loc[a:b]
        bench.append({"block": f"{a}..{b}", "QQQ_buy_hold_pct": (w.q_close.iloc[-1] / w.q_close.iloc[0] - 1) * 100,
                      "SPY_buy_hold_pct": (w.s_close.iloc[-1] / w.s_close.iloc[0] - 1) * 100})
    bench = pd.DataFrame(bench)
    bench.to_csv(f"{out}/pairs_benchmarks.csv", index=False)
    print("\nbenchmarks:\n" + bench.round(2).to_string(index=False))


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--out", default="nq/results")
    p.add_argument("stage", choices=["audit", "stats", "walkforward"])
    a = p.parse_args(argv)
    raw = load_raw(a.data)
    {"audit": stage_audit, "stats": stage_stats, "walkforward": stage_walkforward}[a.stage](raw, a.out)


if __name__ == "__main__":
    main()
