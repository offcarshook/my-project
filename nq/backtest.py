"""Run NQ/MNQ strategies through NautilusTrader's BacktestEngine.

    python -m nq.backtest --data DIR --start 2022-03-21 --end 2024-03-31 [--range 15 ...]

Costs: per-contract commission + exchange fees (PerContractFeeModel) and one tick of slippage
on every market/stop fill (FillModel prob_slippage=1; in bar mode NautilusTrader applies the same
tick to limit fills, a deliberate conservative offset to touch-fills). A market order sent on a candle's close
fills at that close plus one tick (the next trade after the close; on 5-minute NQ candles the next
open is typically within a tick or two of it). Margin account, netting OMS."""
import argparse
from decimal import Decimal

import numpy as np
import pandas as pd

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FillModel, PerContractFeeModel
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.objects import Money

from nq.data import bar_type, load_proxy, to_bars
from nq.instruments import VENUE, micro_future
from nq.strategies.mean_reversion import MeanReversion, MeanReversionConfig
from nq.strategies.orb import OpeningRangeBreakout, OrbConfig

COMMISSION_PER_SIDE = 0.85     # USD per micro contract per side (broker + CME + NFA, conservative)
STARTING_CASH = 50_000


def run_strategy(strategy_cls, config_cls, frame: pd.DataFrame, start=None, end=None, cash=STARTING_CASH,
                 slippage_ticks=True, **params):
    """Backtest one strategy on one proxy-MNQ frame between start/end (New York dates), with the
    same venue, fees, slippage and margin account for every strategy. Returns
    (trades DataFrame, account equity Series)."""
    inst = micro_future("MNQ")
    sel = frame
    if start is not None:
        sel = sel[sel.index >= pd.Timestamp(start, tz="America/New_York")]
    if end is not None:
        sel = sel[sel.index < pd.Timestamp(end, tz="America/New_York") + pd.Timedelta(days=1)]
    engine = BacktestEngine(BacktestEngineConfig(trader_id=TraderId("NQ-001"),
                                                 logging=LoggingConfig(log_level="ERROR")))
    engine.add_venue(VENUE, OmsType.NETTING, AccountType.MARGIN, [Money(cash, USD)], base_currency=USD,
                     default_leverage=Decimal(1),
                     fill_model=FillModel(prob_slippage=1.0 if slippage_ticks else 0.0, random_seed=1),
                     fee_model=PerContractFeeModel(Money(COMMISSION_PER_SIDE, USD)))
    engine.add_instrument(inst)
    engine.add_data(to_bars(sel, inst))
    strat = strategy_cls(config_cls(instrument_id=inst.id, bar_type=bar_type(inst),
                                    cost_per_contract=2 * COMMISSION_PER_SIDE + 2 * 0.5, **params))
    engine.add_strategy(strat)
    engine.run()
    trades = pd.DataFrame(strat.trades)
    account = engine.trader.generate_account_report(VENUE)
    equity = account["total"].astype(float) if len(account) else pd.Series(dtype=float)
    engine.dispose()
    return trades, equity


def run_orb(frame: pd.DataFrame, start=None, end=None, **params):
    return run_strategy(OpeningRangeBreakout, OrbConfig, frame, start, end, **params)


def run_mean_reversion(frame: pd.DataFrame, start=None, end=None, **params):
    return run_strategy(MeanReversion, MeanReversionConfig, frame, start, end, **params)


def summarize(trades: pd.DataFrame, cash=STARTING_CASH) -> dict:
    if trades.empty:
        return {"trades": 0}
    t = trades.sort_values("exit_ts")
    days = t["exit_ts"].dt.tz_convert("America/New_York").dt.date
    eq = cash + t["pnl"].cumsum()
    month = t.groupby(t["exit_ts"].dt.tz_convert("America/New_York").dt.to_period("M"))["pnl"].sum()
    start_eq = (cash + t["pnl"].cumsum().shift(fill_value=0)).groupby(
        t["exit_ts"].dt.tz_convert("America/New_York").dt.to_period("M")).first()
    mret = month / start_eq * 100
    r = t["r"]
    return {"trades": len(t), "trades_per_day": len(t) / max(days.nunique(), 1),
            "win_pct": (t.pnl > 0).mean() * 100, "profit_factor": t.pnl[t.pnl > 0].sum() / -t.pnl[t.pnl < 0].sum(),
            "expectancy_r": r.mean(), "r_tstat": r.mean() / (r.std() / np.sqrt(len(r))),
            "net_return_pct": (eq.iloc[-1] / cash - 1) * 100,
            "max_drawdown_pct": ((eq - eq.cummax().clip(lower=cash)) / eq.cummax().clip(lower=cash)).min() * 100,
            "month_mean_pct": mret.mean(), "month_median_pct": mret.median(),
            "months_positive_pct": (mret > 0).mean() * 100, "month_worst_pct": mret.min(), "month_best_pct": mret.max()}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--range", type=int, default=15)
    p.add_argument("--risk", type=float, default=0.5)
    a = p.parse_args(argv)
    frame = load_proxy(a.data, ("MNQ",))["MNQ"]
    trades, _ = run_orb(frame, a.start, a.end, range_minutes=a.range, risk_pct=a.risk)
    for k, v in summarize(trades).items():
        print(f"{k:20s} {v:,.3f}" if isinstance(v, float) else f"{k:20s} {v}")


if __name__ == "__main__":
    main()
