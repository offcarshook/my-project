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
    """The small, fixed set of configurations to compare."""
    def make(fast, slow, filtered=False, chop=False, stop="atr", rr=None):
        c = copy.deepcopy(cfg)
        c.strategy.fast, c.strategy.slow = fast, slow
        for name in ("htf_trend", "atr_band", "liquidity", "chop"):
            c.strategy.filters.__dict__[name] = {**getattr(c.strategy.filters, name),
                                                 "enabled": filtered and FILTERS_ON.get(name, False)}
        c.strategy.filters.chop["enabled"] = chop
        c.risk.stop.type = stop
        if rr is not None:
            c.risk.reward_risk = rr
        return c

    rr = cfg.risk.reward_risk
    return {
        "EMA 9/21": make(9, 21),
        "EMA 10/30": make(10, 30),
        "EMA 20/50": make(20, 50),
        "EMA 9/21 fixed stop": make(9, 21, stop="fixed"),
        "EMA 9/21 1.5R" if rr != 1.5 else "EMA 9/21 2R": make(9, 21, rr=1.5 if rr != 1.5 else 2.0),
        "EMA 9/21 filtered": make(9, 21, filtered=True),
        "EMA 10/30 filtered": make(10, 30, filtered=True),
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


def score(train: dict, val: dict, n_filters: int, min_trades: float) -> tuple[float, str]:
    """Selection score: the weaker of the train/validation t-stats of mean R, minus a penalty
    per filter. Configs losing money (after costs) in either period, or trading too rarely,
    are rejected outright."""
    for name, m in (("train", train), ("validation", val)):
        if not m["trades"]:
            return -np.inf, f"no trades in {name}"
        if m["trades_per_session"] < min_trades:
            return -np.inf, f"too few trades in {name} ({m['trades_per_session']:.1f}/session)"
        if not (m["expectancy_r"] > 0 and m["profit_factor"] > 1):
            return -np.inf, f"loses money after costs in {name}"
    s = min(train["r_tstat"], val["r_tstat"]) - COMPLEXITY_PENALTY * n_filters
    return s, "eligible"


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
                 synthetic: bool = False, log=print) -> Path:
    bar_minutes = TF_MINUTES[cfg.strategy.timeframe]
    cash = cfg.backtest.starting_cash
    feed = cfg.backtest.data_feed
    tr, va, te = _period(cfg.backtest.train), _period(cfg.backtest.validation), _period(cfg.backtest.test)

    rows, sims, details = [], {}, {}
    for name, v in variants(cfg).items():
        log(f"[{name}] simulating train + validation")
        sim = Simulator(v, bars, EmaCross(v.strategy, v.risk.stop, bar_minutes, feed), bar_minutes)
        sims[name] = (v, sim)
        m_tr = report.summarize(sim.run(*tr), cash)
        res_va = sim.run(*va)
        m_va = report.summarize(res_va, cash)
        ch_tr = report.challenge_summary(challenge_windows(sim, tr[0], tr[1] - pd.Timedelta(days=1), v))
        ch_va = report.challenge_summary(challenge_windows(sim, va[0], va[1] - pd.Timedelta(days=1), v))
        n_filters = len([f for f in v.strategy.filters.enabled() if f != "liquidity"])
        s, verdict = score(m_tr, m_va, n_filters, cfg.backtest.min_trades_per_day)
        details[name] = report.per_instrument(res_va.trades, m_va["sessions"], cash)
        for period, m, ch in (("train", m_tr, ch_tr), ("validation", m_va, ch_va)):
            rows.append({"config": name, "period": period, **m, **{f"challenge_{k}": x for k, x in ch.items()},
                         "score": s, "verdict": verdict})

    comparison = pd.DataFrame(rows)
    eligible = comparison[(comparison.period == "validation") & np.isfinite(comparison.score)]
    if len(eligible):
        chosen = eligible.sort_values("score", ascending=False).iloc[0]["config"]
        selection_note = (f"**{chosen}** had the best robust score (weaker of train/validation t-stat of mean R, "
                          f"minus {COMPLEXITY_PENALTY} per filter) among configs profitable after costs in both periods.")
    else:
        chosen = "EMA 9/21"
        selection_note = ("**No configuration was profitable after costs in both train and validation.** "
                          "The baseline EMA 9/21 is evaluated on the test period for information only; "
                          "this is not evidence of an edge.")
    log(f"selected: {chosen}")

    v, sim = sims[chosen]
    log(f"[{chosen}] running the untouched test period once")
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

    mc = None
    if len(test.trades) >= 20:
        mc_trades = test.trades.rename(columns={"exit_ts": "ts"})
        days = daily_trade_returns(mc_trades, test.equity["equity"], cash)
        n_days = max(round(cfg.challenge.window_days * 252 / 365), 1)
        mc = simulate_luck(days, _luck_rules(cfg), n_days=n_days, n_sims=cfg.backtest.monte_carlo_runs)

    out = Path(out_dir) / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    comparison.to_csv(out / "comparison.csv", index=False)
    test.trades.to_csv(out / "test_trades.csv", index=False)
    test.equity.to_csv(out / "test_equity.csv")
    windows.to_csv(out / "test_challenges.csv", index=False)
    inst.to_csv(out / "test_per_instrument.csv")
    months.to_csv(out / "test_monthly.csv")
    for name, df in details.items():
        df.to_csv(out / f"validation_per_instrument_{name.replace(' ', '_').replace('/', '-')}.csv")
    with open(out / "selected_config.json", "w") as fh:
        json.dump({"name": chosen, "strategy": asdict(v.strategy), "risk": asdict(v.risk)}, fh, indent=2)

    md = _markdown(cfg, bars, comparison, chosen, selection_note, m_te, ch_te, inst, months, regime_table, mc,
                   windows, synthetic)
    (out / "report.md").write_text(md)
    log(f"report written to {out / 'report.md'}")
    return out


def _markdown(cfg, bars, comparison, chosen, note, m_te, ch_te, inst, months, regime_table, mc, windows, synthetic):
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

    L += [f"## 2. Untouched test period: {chosen}", ""]
    keys = ["trades", "trades_per_session", "win_rate_pct", "avg_win", "avg_loss", "profit_factor", "net_pnl",
            "net_return_pct", "expectancy", "expectancy_r", "r_tstat", "max_drawdown_pct", "max_daily_drawdown_pct",
            "longest_losing_streak", "exposure_pct", "turnover", "fees", "sessions"]
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
            "p95_window_return_pct": float(np.percentile(r, 95))}})), "",
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
          "* Costs: half the assumed spread + slippage on every market/stop fill, regulatory fees on sells; take-profit "
          "limits need price to trade through by half the spread. Spreads are assumptions, not historical quotes.",
          "* Fills at the next candle's open, stop-first when a candle touches both, stops gapped through fill at the "
          "open, flat by the configured cutoff, no overnight positions.", ""]
    return "\n".join(L)
