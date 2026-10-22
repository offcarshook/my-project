"""End-to-end backtest: compare a few configs on train/validation, pick one with simple,
robust rules, then run it ONCE on the untouched test period and write a report."""
import copy
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from bot import report
from bot.config import BotConfig
from bot.data import TF_MINUTES
from bot.indicators import ny_index
from bot.montecarlo import daily_trade_returns, simulate_luck
from bot.sim import Simulator
from bot.strategy import EmaCross

FILTERS_ON = {"htf_trend": True, "atr_band": True, "liquidity": True}
COMPLEXITY_PENALTY = 0.15   # t-stat points per enabled filter


def variants(cfg: BotConfig) -> dict[str, BotConfig]:
    """The small, fixed set of configurations to compare. The first one is config.yaml exactly
    as the paper bot will trade it; the others change one idea at a time. Liquidity stays at
    its config.yaml setting everywhere, since the paper bot always applies it."""
    liquidity_on = cfg.strategy.filters.liquidity["enabled"]

    def make(fast, slow, filtered=False, chop=False, atr_mult=None, min_pct=None, rr=None):
        c = copy.deepcopy(cfg)
        c.strategy.fast, c.strategy.slow = fast, slow
        for name in ("htf_trend", "atr_band", "chop"):
            c.strategy.filters.__dict__[name] = {**getattr(c.strategy.filters, name),
                                                 "enabled": filtered and FILTERS_ON.get(name, False)}
        c.strategy.filters.chop["enabled"] = chop
        c.strategy.filters.liquidity["enabled"] = liquidity_on
        c.risk.stop.type = "atr"
        if atr_mult is not None:
            c.risk.stop.atr_mult = atr_mult
        if min_pct is not None:
            c.risk.stop.min_pct = max(min_pct, c.risk.stop.min_pct)
            c.risk.stop.max_pct = max(c.risk.stop.max_pct, c.risk.stop.min_pct)
        if rr is not None:
            c.risk.reward_risk = rr
        return c

    rr = cfg.risk.reward_risk
    # "Wide" = 3x ATR stop with a 0.30% floor and a 1.5R target: fewer stop-outs from 5-minute
    # noise and a smaller cost per R. Position size shrinks with the wider stop, which the risk
    # sweep can make up for by raising risk per trade.
    return {
        "config.yaml as-is": copy.deepcopy(cfg),
        "EMA 9/21": make(9, 21),
        "EMA 10/30": make(10, 30),
        "EMA 20/50": make(20, 50),
        ("EMA 9/21 1.5R" if rr != 1.5 else "EMA 9/21 2R"): make(9, 21, rr=1.5 if rr != 1.5 else 2.0),
        "EMA 9/21 wide stop": make(9, 21, atr_mult=3.0, min_pct=0.30, rr=1.5),
        "EMA 20/50 wide stop": make(20, 50, atr_mult=3.0, min_pct=0.30, rr=1.5),
        "EMA 9/21 filtered": make(9, 21, filtered=True),
        "EMA 20/50 filtered": make(20, 50, filtered=True),
        "EMA 9/21 filtered + chop": make(9, 21, filtered=True, chop=True),
    }


def challenge_windows(sim: Simulator, start, end, cfg: BotConfig) -> pd.DataFrame:
    """Start a fresh challenge every N sessions inside [start, end] and record how each ends."""
    lo, hi = sim.position_of(start), sim.position_of(pd.Timestamp(end) + pd.Timedelta(days=1))
    starts = [i for i in sim.session_starts if lo <= i < hi][::cfg.backtest.challenge_every_n_days]
    rows = []
    for i in starts:
        t0 = sim.index[i]
        deadline = sim.position_of(t0 + pd.Timedelta(days=cfg.challenge.window_days))
        if deadline > hi:
            break
        res = sim.run(lo=i, hi=deadline, challenge=True, stop_when_done=True)
        last = res.equity.index[-1]
        result = res.status if res.status != "ACTIVE" else "TIMEOUT"
        rows.append({"start": t0, "result": result, "reason": res.reason or "out of time",
                     "trading_days": len(set(ny_index(res.equity.index).date)),
                     "calendar_days": (last - t0).days + 1,
                     "return_pct": (res.equity["equity"].iloc[-1] / cfg.backtest.starting_cash - 1) * 100})
    return pd.DataFrame(rows, columns=["start", "result", "reason", "trading_days", "calendar_days", "return_pct"])


def score(train: dict, val: dict, n_filters: int, min_trades: float, max_trades: float = np.inf) -> tuple[float, str]:
    """Selection score: the weaker of the train/validation t-stats of mean R, minus a penalty
    per filter. Configs losing money (after costs) in either period, or trading outside the
    wanted trades-per-session band, are rejected outright."""
    for name, m in (("train", train), ("validation", val)):
        if not m["trades"]:
            return -np.inf, f"no trades in {name}"
        if m["trades_per_session"] < min_trades:
            return -np.inf, f"too few trades in {name} ({m['trades_per_session']:.1f}/session)"
        if m["trades_per_session"] > max_trades:
            return -np.inf, f"too many trades in {name} ({m['trades_per_session']:.1f}/session)"
        if not (m["expectancy_r"] > 0 and m["profit_factor"] > 1):
            return -np.inf, f"loses money after costs in {name}"
    s = min(train["r_tstat"], val["r_tstat"]) - COMPLEXITY_PENALTY * n_filters
    return s, "eligible"


def luck_baseline(trades: pd.DataFrame, equity: pd.Series, cash: float, cfg: BotConfig, seed: int = 0):
    """Monte Carlo pass rates for the real trades and for the same trades with their average
    edge removed (every trade's return shifted so the mean is zero). The gap between the two is
    how much of the pass rate comes from the edge rather than from variance."""
    days = daily_trade_returns(trades.rename(columns={"exit_ts": "ts"}), equity, cash)
    flat = np.concatenate([d for d in days if len(d)]) if any(len(d) for d in days) else np.array([0.0])
    centred = [d - flat.mean() for d in days]
    n_days = max(round(cfg.challenge.window_days * 252 / 365), 1)
    runs = cfg.backtest.monte_carlo_runs
    real = simulate_luck(days, _luck_rules(cfg), n_days=n_days, n_sims=runs, seed=seed)
    zero = simulate_luck(centred, _luck_rules(cfg), n_days=n_days, n_sims=runs, seed=seed)
    return real, zero


def choose_risk(rows: list[dict], default: float, max_fail_pct: float, min_margin: float) -> tuple[float, str]:
    """Pick the risk level with the best worst-case (train vs validation) pass rate, among
    levels that fail no more than `max_fail_pct` in either period and beat the zero-edge luck
    baseline by at least `min_margin` points. Otherwise keep the configured risk."""
    ok = [r for r in rows if max(r["train_fail_pct"], r["validation_fail_pct"]) <= max_fail_pct
          and r["edge_margin_pct"] >= min_margin]
    if not ok:
        return default, (f"No risk level beat the luck baseline by {min_margin:g} points with at most "
                         f"{max_fail_pct:g}% blown challenges, so risk stays at {default:g}%.")
    best = max(ok, key=lambda r: (min(r["train_pass_pct"], r["validation_pass_pct"]), -r["risk_pct"]))
    return best["risk_pct"], (f"{best['risk_pct']:g}% risk had the best worst-case pass rate "
                              f"({min(best['train_pass_pct'], best['validation_pass_pct']):.0f}%) while beating the "
                              f"luck baseline by {best['edge_margin_pct']:.0f} points.")


def sweep_risk(v: BotConfig, sim: Simulator, tr, va, cash: float, log=print) -> pd.DataFrame:
    """Challenge outcomes for each risk level in backtest.risk_grid, on train and validation only."""
    rows = []
    original = sim.cfg
    try:
        for r in v.backtest.risk_grid:
            c = copy.deepcopy(v)
            c.risk.risk_per_trade_pct = r
            c.risk.max_gross_exposure = max(c.risk.max_gross_exposure, v.backtest.sweep_max_gross_exposure)
            sim.cfg = c
            log(f"  risk {r:g}%")
            ch_tr = report.challenge_summary(challenge_windows(sim, tr[0], tr[1] - pd.Timedelta(days=1), c))
            ch_va = report.challenge_summary(challenge_windows(sim, va[0], va[1] - pd.Timedelta(days=1), c))
            res_va = sim.run(*va)
            row = {"risk_pct": r, "train_pass_pct": ch_tr.get("pass_pct", 0), "train_fail_pct": ch_tr.get("fail_pct", 0),
                   "validation_pass_pct": ch_va.get("pass_pct", 0), "validation_fail_pct": ch_va.get("fail_pct", 0),
                   "mc_pass_pct": np.nan, "zero_edge_pass_pct": np.nan, "edge_margin_pct": -np.inf}
            if len(res_va.trades) >= 20:
                real, zero = luck_baseline(res_va.trades, res_va.equity["equity"], cash, c)
                row.update(mc_pass_pct=real.pass_rate * 100, zero_edge_pass_pct=zero.pass_rate * 100,
                           edge_margin_pct=(real.pass_rate - zero.pass_rate) * 100)
            rows.append(row)
    finally:
        sim.cfg = original
    return pd.DataFrame(rows)


def _period(p):
    return pd.Timestamp(p[0], tz="America/New_York"), pd.Timestamp(p[1], tz="America/New_York") + pd.Timedelta(days=1)


def _luck_rules(cfg: BotConfig):
    c = cfg.challenge

    class Rules:  # the subset monte carlo needs
        profit_target_pct = c.profit_target_pct
        max_daily_loss_pct = c.max_daily_loss_pct
        max_drawdown_pct = c.max_total_loss_pct
    return Rules()


def run_pipeline(cfg: BotConfig, bars: dict[str, pd.DataFrame], out_dir: str = "reports",
                 synthetic: bool = False, log=print, sessions: dict | None = None) -> Path:
    bar_minutes = TF_MINUTES[cfg.strategy.timeframe]
    cash = cfg.backtest.starting_cash
    feed = cfg.backtest.data_feed
    tr, va, te = _period(cfg.backtest.train), _period(cfg.backtest.validation), _period(cfg.backtest.test)

    rows, sims, details = [], {}, {}
    for name, v in variants(cfg).items():
        log(f"[{name}] simulating train + validation")
        sim = Simulator(v, bars, EmaCross(v.strategy, v.risk.stop, bar_minutes, feed), bar_minutes, sessions)
        sims[name] = (v, sim)
        m_tr = report.summarize(sim.run(*tr), cash)
        res_va = sim.run(*va)
        m_va = report.summarize(res_va, cash)
        ch_tr = report.challenge_summary(challenge_windows(sim, tr[0], tr[1] - pd.Timedelta(days=1), v))
        ch_va = report.challenge_summary(challenge_windows(sim, va[0], va[1] - pd.Timedelta(days=1), v))
        n_filters = len([f for f in v.strategy.filters.enabled() if f != "liquidity"])
        s, verdict = score(m_tr, m_va, n_filters, cfg.backtest.min_trades_per_day, cfg.backtest.max_trades_per_day)
        details[name] = report.per_instrument(res_va.trades, m_va["sessions"], cash)
        for period, m, ch in (("train", m_tr, ch_tr), ("validation", m_va, ch_va)):
            rows.append({"config": name, "period": period, **m, **{f"challenge_{k}": x for k, x in ch.items()},
                         "score": s, "verdict": verdict})

    comparison = pd.DataFrame(rows)
    eligible = comparison[(comparison.period == "validation") & np.isfinite(comparison.score)]
    sweep, risk_note = pd.DataFrame(), ""
    if len(eligible):
        chosen = eligible.sort_values("score", ascending=False).iloc[0]["config"]
        selection_note = (f"**{chosen}** had the best robust score (weaker of train/validation t-stat of mean R, "
                          f"minus {COMPLEXITY_PENALTY} per filter) among configs profitable after costs in both periods "
                          f"and trading {cfg.backtest.min_trades_per_day:g}-{cfg.backtest.max_trades_per_day:g} times per session.")
    else:
        chosen = "config.yaml as-is"
        selection_note = ("**No configuration was profitable after costs in both train and validation.** "
                          "Your config.yaml is evaluated on the test period for information only; "
                          "this is not evidence of an edge.")
    log(f"selected: {chosen}")
    v, sim = sims[chosen]
    if len(eligible):
        log(f"[{chosen}] risk-per-trade sweep on train + validation")
        sweep = sweep_risk(v, sim, tr, va, cash, log)
        risk, risk_note = choose_risk(sweep.to_dict("records"), v.risk.risk_per_trade_pct,
                                      cfg.backtest.max_fail_pct, cfg.backtest.min_edge_margin_pct)
        if risk != v.risk.risk_per_trade_pct:
            v = copy.deepcopy(v)
            v.risk.risk_per_trade_pct = risk
            v.risk.max_gross_exposure = max(v.risk.max_gross_exposure, cfg.backtest.sweep_max_gross_exposure)
            sim.cfg = v
    else:
        risk_note = "Skipped: there's no edge to size up, and a higher risk would only add variance."

    log(f"[{chosen} @ {v.risk.risk_per_trade_pct:g}% risk] running the untouched test period once")
    test = sim.run(*te)
    m_te = report.summarize(test, cash)
    windows = challenge_windows(sim, te[0], te[1] - pd.Timedelta(days=1), v)
    ch_te = report.challenge_summary(windows)
    inst = report.per_instrument(test.trades, m_te["sessions"], cash)
    months = report.monthly(test.trades, test.equity)
    regime_sym = cfg.backtest.regime_symbol
    labels = pd.DataFrame()
    if regime_sym in bars and not bars[regime_sym].empty:
        daily = bars[regime_sym]["close"].groupby(ny_index(bars[regime_sym].index).date).last()
        labels = report.regimes(daily)
        test_days = set(ny_index(test.equity.index).date)
        labels = labels[labels.index.isin(test_days)]
    regime_table = report.by_regime(test.trades, labels)

    mc = mc_zero = None
    if len(test.trades) >= 20:
        mc, mc_zero = luck_baseline(test.trades, test.equity["equity"], cash, v)
    m_te["cap_hit_pct"] = report.cap_hit_pct(test.trades, m_te["sessions"], v.risk.max_trades_per_day)

    out = Path(out_dir) / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    comparison.to_csv(out / "comparison.csv", index=False)
    sweep.to_csv(out / "risk_sweep.csv", index=False)
    test.trades.to_csv(out / "test_trades.csv", index=False)
    test.equity.to_csv(out / "test_equity.csv")
    windows.to_csv(out / "test_challenges.csv", index=False)
    inst.to_csv(out / "test_per_instrument.csv")
    months.to_csv(out / "test_monthly.csv")
    for name, df in details.items():
        df.to_csv(out / f"validation_per_instrument_{name.replace(' ', '_').replace('/', '-')}.csv")
    with open(out / "selected_config.json", "w") as fh:
        json.dump({"name": chosen, "edge_found": bool(len(eligible)), "strategy": asdict(v.strategy),
                   "risk": asdict(v.risk)}, fh, indent=2)

    md = _markdown(cfg, bars, comparison, chosen, selection_note, m_te, ch_te, inst, months, regime_table, mc,
                   windows, synthetic, sweep=sweep, risk_note=risk_note, chosen_cfg=v, mc_zero=mc_zero)
    (out / "report.md").write_text(md)
    log(f"report written to {out / 'report.md'}")
    return out


def _markdown(cfg, bars, comparison, chosen, note, m_te, ch_te, inst, months, regime_table, mc, windows, synthetic,
              sweep=None, risk_note="", chosen_cfg=None, mc_zero=None):
    chosen_cfg = chosen_cfg or cfg
    L = []
    if synthetic:
        L += ["# ⚠️ SYNTHETIC DATA: PIPELINE CHECK ONLY",
              "",
              "This report was generated from **randomly generated prices**. It only shows that the "
              "backtest, challenge simulation and reporting run end to end. **None of these numbers say anything "
              "about real-market performance.** Run `python propbot.py backtest` with Alpaca credentials for real results.",
              ""]
    L += ["# Prop Pass backtest report", "",
          f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} · data: "
          f"{'synthetic random walk' if synthetic else 'Alpaca ' + cfg.backtest.data_feed.upper() + ' ' + cfg.strategy.timeframe + ' candles'} · "
          f"{len(bars)} symbols: {', '.join(bars)}", "",
          f"Starting cash ${cfg.backtest.starting_cash:,.0f} · risk {cfg.risk.risk_per_trade_pct}% per trade · "
          f"challenge +{cfg.challenge.profit_target_pct}% / -{cfg.challenge.max_daily_loss_pct}% daily / "
          f"-{cfg.challenge.max_total_loss_pct}% total ({cfg.challenge.drawdown_type}) in {cfg.challenge.window_days} days", "",
          f"Periods: train {cfg.backtest.train[0]} → {cfg.backtest.train[1]} · validation {cfg.backtest.validation[0]} → "
          f"{cfg.backtest.validation[1]} · **test (untouched) {cfg.backtest.test[0]} → {cfg.backtest.test[1]}**", "",
          "## 1. Configuration comparison (train and validation)", ""]
    cols = ["trades_per_session", "win_rate_pct", "profit_factor", "expectancy_r", "r_tstat", "net_return_pct",
            "max_drawdown_pct", "max_daily_drawdown_pct", "challenge_pass_pct", "challenge_fail_pct"]
    for period in ("train", "validation"):
        t = comparison[comparison.period == period].set_index("config")
        L += [f"### {period.title()}", "", report.fmt_table(t[[c for c in cols if c in t]]), ""]
    verdicts = comparison[comparison.period == "validation"].set_index("config")[["score", "verdict"]]
    L += ["### Selection", "", report.fmt_table(verdicts), "", note, ""]
    L += ["### Risk per trade (train + validation only)", ""]
    if sweep is not None and len(sweep):
        L += [report.fmt_table(sweep.set_index("risk_pct")), "",
              "_mc_pass / zero_edge_pass: Monte Carlo pass rates for the validation trades as they are and with their "
              "average edge removed. edge_margin is the difference, i.e. how much of the pass rate isn't just luck._", ""]
    L += [risk_note, ""]

    L += [f"## 2. Untouched test period: {chosen} @ {chosen_cfg.risk.risk_per_trade_pct:g}% risk per trade", ""]
    keys = ["trades", "trades_per_session", "win_rate_pct", "avg_win", "avg_loss", "profit_factor", "net_pnl",
            "net_return_pct", "expectancy", "expectancy_r", "r_tstat", "cost_r", "max_drawdown_pct",
            "max_daily_drawdown_pct", "longest_losing_streak", "exposure_pct", "turnover", "fees", "cap_hit_pct", "sessions"]
    L += [report.fmt_table(pd.DataFrame({"value": {k: m_te.get(k) for k in keys}})), ""]
    L += ["### Simulated challenges (test period)", "",
          report.fmt_table(pd.DataFrame({"value": ch_te})), "",
          "_A fresh challenge starts every "
          f"{cfg.backtest.challenge_every_n_days} sessions; windows overlap, so results are not independent._", ""]
    L += ["### By instrument", "", report.fmt_table(inst), ""]
    L += ["### By month", "", report.fmt_table(months), ""]
    L += [f"### By market regime ({cfg.backtest.regime_symbol}, labels from the prior 20 sessions)", "",
          report.fmt_table(regime_table), ""]

    L += ["## 3. Monte Carlo (reshuffled trading days)", ""]
    if mc is None:
        L += ["_Skipped: fewer than 20 test trades._", ""]
    else:
        r = mc.period_returns
        L += [report.fmt_table(pd.DataFrame({"value": {
            "simulations": len(r), "pass_pct": mc.pass_rate * 100, "fail_pct": mc.fail_rate * 100,
            "timeout_pct": mc.timeout_rate * 100,
            "median_days_to_pass": float(np.median(mc.days_to_pass)) if len(mc.days_to_pass) else np.nan,
            "median_window_return_pct": float(np.median(r)), "p5_window_return_pct": float(np.percentile(r, 5)),
            "p95_window_return_pct": float(np.percentile(r, 95)),
            "zero_edge_pass_pct": mc_zero.pass_rate * 100 if mc_zero else np.nan,
            "edge_pass_margin_pct": (mc.pass_rate - mc_zero.pass_rate) * 100 if mc_zero else np.nan}})), "",
              "_zero_edge_pass: the same trades with their average edge removed. If pass ≈ zero_edge_pass, the pass "
              "rate is luck, not edge._", "",
              "**Assumptions and limits:** whole test-period trading days (with all their trades) are drawn at random "
              "with replacement into challenge-length sequences, so it assumes days are independent and that the "
              "test period represents the future. It keeps intraday trade clustering but ignores regime changes, "
              "uses closed-trade P&L only (open-trade drawdown inside a day is not modelled, which can understate "
              "daily-limit breaches), and applies a static total-loss floor with the daily limit measured from each "
              "day's start. It does not apply the live risk manager's safety buffer or its habit of shrinking positions near "
              "a limit, so it shows more blow-ups (and fewer time-outs) than the full challenge simulation above. "
              "It measures sensitivity to luck in ordering; it cannot create or prove an edge.", ""]

    L += ["## 4. How to read this", "",
          "* A config is only credible if it is profitable after costs in train, validation **and** test, with a "
          "reasonable number of trades. One good period, a high win rate or one challenge reaching +8% is not evidence of an edge.",
          "* Costs: half the assumed spread (never under one cent) + slippage on every market/stop fill, regulatory "
          "fees on sells; take-profit limits need price to trade through by half the spread. Spreads are assumptions, "
          "not historical quotes. `cost_r` is the average trading cost per trade in R: the gross edge has to beat it.",
          "* `cap_hit_pct`: share of sessions that hit the daily trade limit, where later signals were skipped.",
          "* Fills at the next candle's open, stop-first when a candle touches both, stops gapped through fill at the "
          "open, flat by the configured cutoff, no overnight positions.", ""]
    return "\n".join(L)
