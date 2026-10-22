"""Intraday portfolio simulator for the Prop Pass strategy.

Execution model (all conservative where there's a choice):
  * Signals come from completed candles and execute at the NEXT candle's open, never at the
    signal candle's close.
  * Market and stop orders pay half the spread plus slippage; take-profit limits fill at the
    limit only once the candle traded through it by half the spread.
  * A stop gapped through at the open fills at the open. If one candle touches both the stop
    and the target, the stop is assumed to have hit first.
  * Everything is closed at `flatten_at`, or 10 minutes before an early close when the exchange
    calendar (`sessions`) is given, or at the last candle of a session that ends early in the data.
    Nothing is held overnight.
  * Position sizing at a candle's open only uses prices known at that open.
  * The profit target is judged on what the account would hold after paying exit costs.
  * Challenge limits are judged on each candle's worst price (low for longs, high for shorts),
    and a breach is filled at that worst price.
"""
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from bot.config import BotConfig
from bot.costs import CostModel
from bot.indicators import ny_index
from bot.risk import FLATTEN_LOCK, TARGET_HIT, OpenPosition, RiskManager, size_position

TRADE_COLUMNS = ["symbol", "side", "qty", "entry_ts", "entry", "stop", "target", "exit_ts", "exit",
                 "reason", "pnl", "fees", "risk", "r", "notional", "costs"]   # costs = spread + slippage + fees


@dataclass
class _Pos:
    symbol: str
    qty: int               # negative = short
    entry: float
    stop: float
    target: float | None
    entry_ts: pd.Timestamp
    risk: float            # dollars at risk at entry
    fees: float            # entry fees
    atr: float
    best: float            # best price since entry (for the trailing stop)
    group: str
    leverage: float
    reserve: float = 0.0   # per-share gap buffer + exit cost reserved at entry (see risk.open_risk)
    cost: float = 0.0      # spread + slippage + fees paid so far


@dataclass
class SimResult:
    trades: pd.DataFrame
    equity: pd.DataFrame            # per candle: equity, worst, balance, exposure, positions
    status: str = "ACTIVE"
    reason: str = ""
    decisions: list = field(default_factory=list)   # risk-manager decisions that changed something


class Simulator:
    def __init__(self, cfg: BotConfig, bars: dict[str, pd.DataFrame], strategy, bar_minutes: int = 5,
                 sessions: dict | None = None):
        self.cfg = cfg
        self.costs = CostModel(cfg.costs, cfg.spread_bps)
        self.bar_minutes = bar_minutes
        frames = {s: strategy.compute(df) for s, df in bars.items() if df is not None and not df.empty}
        if not frames:
            raise ValueError("no price data to simulate")
        self.index = pd.DatetimeIndex(sorted(set().union(*[f.index for f in frames.values()])))
        self.symbols = list(frames)
        n, m = len(self.index), len(self.symbols)

        def grid(col, fill=np.nan):
            out = np.full((n, m), fill, dtype=float)
            for k, s in enumerate(self.symbols):
                pos = self.index.get_indexer(frames[s].index)
                out[pos, k] = frames[s][col].to_numpy(dtype=float)
            return out

        self.open, self.high, self.low, self.close = grid("open"), grid("high"), grid("low"), grid("close")
        self.atr = grid("atr")
        self.cross = np.nan_to_num(grid("cross", 0.0)).astype(int)
        sig = np.zeros((n, m), dtype=int)
        for k, s in enumerate(self.symbols):
            pos = self.index.get_indexer(frames[s].index)
            sig[pos, k] = np.where(frames[s]["signal"] == "BUY", 1, np.where(frames[s]["signal"] == "SELL", -1, 0))
        self.signal = sig
        self.last_close = pd.DataFrame(self.close).ffill().to_numpy()

        local = ny_index(self.index)
        self.days = np.array([d.isoformat() for d in local.date])
        self.hhmm = np.array(local.strftime("%H:%M"))
        self.last_of_day = np.ones(n, dtype=bool)
        self.last_of_day[:-1] = self.days[:-1] != self.days[1:]
        # Cutoff per session: flatten_at, or 10 minutes before the session's close from the
        # exchange calendar (early closes), the same rule the paper trader uses with the broker clock.
        minutes = local.hour * 60 + local.minute
        fh, fm = map(int, cfg.strategy.flatten_at.split(":"))
        cutoff = np.full(n, fh * 60 + fm)
        if sessions:
            close = np.array([_hhmm_to_min(sessions[d][1]) if d in sessions else 16 * 60 for d in self.days])
            cutoff = np.minimum(cutoff, close - 10)
        self.flatten = minutes >= cutoff
        # Bars where something happens: a raw cross (possible exit) or a filtered signal (entry).
        self.events = {int(i): [int(k) for k in np.nonzero(self.cross[i])[0]]
                       for i in np.nonzero(self.cross.any(axis=1))[0]}
        self.session_starts = [i for i in range(n) if i == 0 or self.days[i] != self.days[i - 1]]

    # ------------------------------------------------------------------
    def position_of(self, ts) -> int:
        ts = pd.Timestamp(ts)
        if ts.tzinfo is None and self.index.tz is not None:
            ts = ts.tz_localize("America/New_York").tz_convert(self.index.tz)
        return int(self.index.searchsorted(ts))

    def run(self, start=None, end=None, challenge: bool = False, starting_cash: float | None = None,
            stop_when_done: bool = True, lo: int | None = None, hi: int | None = None) -> SimResult:
        cfg, costs, st, rk = self.cfg, self.costs, self.cfg.strategy, self.cfg.risk
        if lo is None:
            lo = self.position_of(start) if start is not None else 0
        hi = (self.position_of(end) if end is not None else len(self.index)) if hi is None else hi
        cash0 = cfg.backtest.starting_cash if starting_cash is None else starting_cash
        rm = RiskManager(cfg.challenge, cash0) if challenge else None

        cash, realized = cash0, 0.0
        positions: dict[int, _Pos] = {}
        trades, curve, decisions = [], [], []
        pending: dict[int, tuple[int, int, float]] = {}   # k -> (cross, signal, atr) from the previous candle
        day, trades_today, halted = None, 0, False
        equity_prev = cash0

        def close_pos(k: int, i: int, price: float, reason: str, market: bool = True):
            nonlocal cash, realized
            p = positions.pop(k)
            side = -1 if p.qty > 0 else 1
            fill = costs.market_fill(p.symbol, side, price) if market else price
            fee = costs.fees(abs(p.qty), fill, is_sell=p.qty > 0)
            cash += p.qty * fill - fee
            pnl = (fill - p.entry) * p.qty - p.fees - fee
            realized += pnl
            cost = p.cost + abs(fill - price) * abs(p.qty) + fee
            trades.append([p.symbol, "LONG" if p.qty > 0 else "SHORT", abs(p.qty), p.entry_ts, p.entry, p.stop,
                           p.target, self.index[i], fill, reason, pnl, p.fees + fee, p.risk,
                           pnl / p.risk if p.risk else np.nan, abs(p.qty) * (p.entry + fill), cost])

        def exit_value(i: int) -> float:
            """What the account would hold if everything were closed at this candle's close."""
            lc, value = self.last_close[i], cash
            for k, p in positions.items():
                fill = costs.market_fill(p.symbol, -1 if p.qty > 0 else 1, lc[k])
                value += p.qty * fill - costs.fees(abs(p.qty), fill, is_sell=p.qty > 0)
            return value

        def open_list(i: int) -> list[OpenPosition]:
            # Prices known at this candle's OPEN (entries happen there): never this candle's close.
            out = []
            for k, p in positions.items():
                px = self.open[i, k]
                if np.isnan(px):
                    px = self.last_close[i - 1, k] if i > 0 else p.entry
                out.append(OpenPosition(p.symbol, p.qty, p.entry, p.stop, px, p.group, p.leverage, p.reserve))
            return out

        for i in range(lo, hi):
            t = self.index[i]
            if self.days[i] != day:
                day, trades_today, pending = self.days[i], 0, {}
            o, h, lw, c = self.open[i], self.high[i], self.low[i], self.close[i]

            if not halted:
                if self.flatten[i]:
                    for k in list(positions):
                        if not np.isnan(o[k]):
                            close_pos(k, i, o[k], "end of day")
                    pending = {}
                else:
                    # 1. orders from the previous candle's signals, at this candle's open
                    for k, (cross, sig, atr_k) in list(pending.items()):
                        if np.isnan(o[k]):
                            continue
                        p = positions.get(k)
                        if p is not None and st.exit_on_opposite and cross == (-1 if p.qty > 0 else 1):
                            close_pos(k, i, o[k], "opposite signal")
                    for k, (cross, sig, atr_k) in pending.items():
                        if sig == 0 or np.isnan(o[k]) or k in positions or (sig < 0 and not st.allow_short):
                            continue
                        if rm is not None and not rm.can_open():
                            continue
                        sym = self.symbols[k]
                        inst = cfg.instrument(sym)
                        ref = o[k]
                        stop_dist = _stop_dist(ref, atr_k, rk.stop)
                        room = rm.risk_room(equity_prev) if rm is not None else math.inf
                        res = size_position(sym, sig, ref, stop_dist, equity_prev, open_list(i), rk, inst.group,
                                            inst.leverage, room, trades_today, costs.per_share_estimate(sym, ref))
                        if res.qty < 1:
                            continue
                        fill = costs.market_fill(sym, sig, ref)
                        fee = costs.fees(res.qty, fill, is_sell=sig < 0)
                        cash -= sig * res.qty * fill + fee
                        target = fill + sig * stop_dist * rk.reward_risk if rk.reward_risk else None
                        positions[k] = _Pos(sym, sig * res.qty, fill, fill - sig * stop_dist, target, t,
                                            res.qty * stop_dist, fee, atr_k, fill, inst.group, inst.leverage,
                                            res.details["reserve_per_share"], abs(fill - ref) * res.qty + fee)
                        trades_today += 1
                    pending = {}

                    # 2. stops and targets inside this candle (stop wins a tie)
                    for k in list(positions):
                        p = positions[k]
                        if np.isnan(o[k]):
                            continue
                        if p.qty > 0:
                            if lw[k] <= p.stop:
                                close_pos(k, i, min(o[k], p.stop), "stop loss")
                            elif p.target is not None and costs.limit_touched(p.symbol, -1, p.target, h[k], lw[k]):
                                close_pos(k, i, max(o[k], p.target), "take profit", market=False)
                        else:
                            if h[k] >= p.stop:
                                close_pos(k, i, max(o[k], p.stop), "stop loss")
                            elif p.target is not None and costs.limit_touched(p.symbol, 1, p.target, h[k], lw[k]):
                                close_pos(k, i, min(o[k], p.target), "take profit", market=False)

                    # 3. trailing stop, tightened at the close for the next candle
                    if rk.trailing.enabled:
                        for k, p in positions.items():
                            if np.isnan(c[k]):
                                continue
                            p.best = max(p.best, h[k]) if p.qty > 0 else min(p.best, lw[k])
                            dist = abs(p.entry - p.stop) if p.risk else 0
                            gain = (p.best - p.entry) * (1 if p.qty > 0 else -1)
                            if dist and gain >= rk.trailing.activate_r * dist and not np.isnan(self.atr[i, k]):
                                trail = p.best - np.sign(p.qty) * rk.trailing.atr_mult * self.atr[i, k]
                                p.stop = max(p.stop, trail) if p.qty > 0 else min(p.stop, trail)

                # 4. last candle of the session (early close, or a symbol with no flatten-time candle)
                if self.last_of_day[i]:
                    for k in list(positions):
                        close_pos(k, i, self.last_close[i, k], "end of day")


            # mark to market
            lc = self.last_close[i]
            held = sum(p.qty * lc[k] for k, p in positions.items())
            worst = sum(p.qty * (lw[k] if p.qty > 0 else h[k]) if not np.isnan(lw[k]) else p.qty * lc[k]
                        for k, p in positions.items())
            best = sum(p.qty * (h[k] if p.qty > 0 else lw[k]) if not np.isnan(h[k]) else p.qty * lc[k]
                       for k, p in positions.items())
            equity = cash + held
            worst_equity = cash + worst
            balance = cash0 + realized

            if rm is not None and not halted:
                d = rm.update(t, balance, equity, worst_equity, best_equity=cash + best,
                              target_equity=exit_value(i))
                if d.action == FLATTEN_LOCK:
                    for k in list(positions):
                        p = positions[k]
                        price = lw[k] if p.qty > 0 else h[k]
                        close_pos(k, i, lc[k] if np.isnan(price) else price, "risk limit breached")
                    decisions.append((t, d.action, d.reason))
                    halted = True
                elif d.action == TARGET_HIT and cfg.challenge.stop_at_target:
                    for k in list(positions):
                        close_pos(k, i, lc[k], "profit target reached")
                    decisions.append((t, d.action, d.reason))
                    # The pass only counts if the closed balance made it (it should, since the
                    # target was judged after exit costs); otherwise keep trading.
                    halted = rm.confirm_target(cash0 + realized)
                if halted:
                    equity = worst_equity = cash
                    balance = cash0 + realized

            exposure = sum(abs(p.qty) * lc[k] * abs(p.leverage) for k, p in positions.items())
            curve.append((t, equity, min(worst_equity, equity), balance, exposure, len(positions)))
            equity_prev = equity
            if halted and stop_when_done:
                break

            # 5. queue this candle's signals for the next open (same session, before flatten time)
            if not halted and i + 1 < hi and not self.last_of_day[i] and not self.flatten[i + 1] and i in self.events:
                for k in self.events[i]:
                    pending[k] = (int(self.cross[i, k]), int(self.signal[i, k]), float(self.atr[i, k]))

        equity_df = pd.DataFrame(curve, columns=["ts", "equity", "worst", "balance", "exposure", "positions"])
        equity_df = equity_df.set_index("ts")
        status, reason = (rm.state.status, rm.state.reason) if rm is not None else ("ACTIVE", "")
        return SimResult(pd.DataFrame(trades, columns=TRADE_COLUMNS), equity_df, status, reason, decisions)


def _hhmm_to_min(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _stop_dist(price: float, atr_value: float, stop) -> float:
    if stop.type == "fixed" or np.isnan(atr_value):
        return price * (stop.fixed_pct if stop.type == "fixed" else stop.max_pct) / 100
    return float(np.clip(stop.atr_mult * atr_value, price * stop.min_pct / 100, price * stop.max_pct / 100))
