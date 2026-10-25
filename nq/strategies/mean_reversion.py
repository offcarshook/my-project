"""Intraday mean reversion for NQ/MNQ as a NautilusTrader Strategy.

Signal families (all computed causally from completed candles of the current session):
* "vwap":    deviation = (close - session VWAP) / ATR
* "zscore":  deviation = (close - SMA_n) / stdev_n of the session's closes
* "atr_ema": deviation = (close - EMA_n) / ATR

Entry: a completed candle where the deviation crosses back inside -k (long) or +k (short) after
being beyond it, i.e. the stretch has started to revert. Target = the mean (VWAP / SMA / EMA)
fixed at entry; skipped if it is closer than `min_rr` x the stop. Stop = `stop_atr` x ATR
beyond the entry; time exit after `max_hold_bars`; everything flat at `flatten_at`.

Trend / volatility gates (0 = off):
* er_max:  skip when the 12-candle efficiency ratio is above this (a clean directional move);
* vol_max: skip when ATR is more than this multiple of its median over the last 390 candles.

ATR is session-aware (the first candle's range ignores the overnight gap)."""
import math
from collections import deque

import numpy as np
import pandas as pd

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy

NY = "America/New_York"


class MeanReversionConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    signal: str = "vwap"              # vwap | zscore | atr_ema
    entry_k: float = 2.0
    lookback: int = 20                # SMA/stdev/EMA length (zscore, atr_ema)
    atr_period: int = 14
    stop_atr: float = 1.5
    min_rr: float = 0.75
    max_hold_bars: int = 12
    first_entry: str = "10:00"
    last_entry: str = "15:00"
    flatten_at: str = "15:50"
    max_trades_per_day: int = 4
    er_max: float = 0.0
    vol_max: float = 0.0
    risk_pct: float = 0.5
    max_contracts: int = 20
    cost_per_contract: float = 2.5


class MeanReversion(Strategy):
    def __init__(self, config: MeanReversionConfig) -> None:
        super().__init__(config)
        if config.signal not in ("vwap", "zscore", "atr_ema"):
            raise ValueError(f"unknown signal {config.signal!r}")
        self.instrument = None
        self.trades: list[dict] = []
        self._tr = deque(maxlen=config.atr_period)
        self._atr_hist = deque(maxlen=390)
        self._day = None
        self._pending: dict | None = None
        self._bars_held = 0

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        self.subscribe_bars(self.config.bar_type)

    def on_stop(self) -> None:
        self.cancel_all_orders(self.config.instrument_id)
        self.close_all_positions(self.config.instrument_id)

    # ---- causal features ----------------------------------------------------
    def _new_day(self, day) -> None:
        self._day, self._pv, self._vol = day, 0.0, 0.0
        self._closes = deque(maxlen=self.config.lookback)
        self._er_closes = deque(maxlen=13)
        self._ema = None
        self._prev_close = None
        self._prev_dev = None
        self._n_today = 0

    def _update(self, bar: Bar):
        h, lo, c, v = float(bar.high), float(bar.low), float(bar.close), float(bar.volume)
        tr = h - lo if self._prev_close is None else max(h - lo, abs(h - self._prev_close), abs(lo - self._prev_close))
        self._tr.append(tr)
        self._prev_close = c
        atr = sum(self._tr) / len(self._tr) if len(self._tr) == self._tr.maxlen else math.nan
        if not math.isnan(atr):
            self._atr_hist.append(atr)
        self._pv += (h + lo + c) / 3 * max(v, 1.0)
        self._vol += max(v, 1.0)
        self._closes.append(c)
        self._er_closes.append(c)
        a = 2 / (self.config.lookback + 1)
        self._ema = c if self._ema is None else a * c + (1 - a) * self._ema
        return c, atr

    def _deviation(self, c: float, atr: float):
        """(deviation, mean level) or (nan, nan) while not ready."""
        s = self.config.signal
        if s == "vwap":
            mean = self._pv / self._vol
            return ((c - mean) / atr if atr > 0 else math.nan), mean
        if len(self._closes) < self._closes.maxlen:
            return math.nan, math.nan
        if s == "zscore":
            arr = np.asarray(self._closes)
            sd = arr.std(ddof=1)
            return ((c - arr.mean()) / sd if sd > 0 else math.nan), float(arr.mean())
        return ((c - self._ema) / atr if atr > 0 else math.nan), self._ema

    def _gates_ok(self, atr: float) -> bool:
        cfg = self.config
        if cfg.er_max > 0:
            if len(self._er_closes) < self._er_closes.maxlen:
                return False
            path = np.abs(np.diff(np.asarray(self._er_closes))).sum()
            er = abs(self._er_closes[-1] - self._er_closes[0]) / path if path > 0 else 0.0
            if er > cfg.er_max:
                return False
        if cfg.vol_max > 0:
            if len(self._atr_hist) < 100:
                return False
            if atr > cfg.vol_max * float(np.median(self._atr_hist)):
                return False
        return True

    # ---- decisions ----------------------------------------------------------
    def on_bar(self, bar: Bar) -> None:
        cfg = self.config
        t = pd.Timestamp(bar.ts_event, tz="UTC").tz_convert(NY)
        if t.date() != self._day:
            self._new_day(t.date())
        hhmm = t.strftime("%H:%M")
        c, atr = self._update(bar)
        dev, mean = self._deviation(c, atr)
        prev_dev, self._prev_dev = self._prev_dev, dev

        if hhmm >= cfg.flatten_at:
            self.cancel_all_orders(cfg.instrument_id)
            self.close_all_positions(cfg.instrument_id)
            return
        if not self.portfolio.is_flat(cfg.instrument_id):
            self._bars_held += 1
            if self._bars_held >= cfg.max_hold_bars:
                self.cancel_all_orders(cfg.instrument_id)
                self.close_all_positions(cfg.instrument_id)
            return
        if (math.isnan(dev) or prev_dev is None or math.isnan(prev_dev) or math.isnan(atr)
                or not cfg.first_entry <= hhmm <= cfg.last_entry or self._n_today >= cfg.max_trades_per_day):
            return
        k = cfg.entry_k
        side = None
        if prev_dev <= -k < dev:
            side, stop = OrderSide.BUY, c - cfg.stop_atr * atr
        elif prev_dev >= k > dev:
            side, stop = OrderSide.SELL, c + cfg.stop_atr * atr
        if side is None or not self._gates_ok(atr):
            return
        if abs(mean - c) < cfg.min_rr * abs(c - stop) or (mean - c) * (1 if side == OrderSide.BUY else -1) <= 0:
            return
        self._enter(side, c, stop, mean)

    def _tick(self, price: float) -> float:
        inc = float(self.instrument.price_increment)
        return round(price / inc) * inc

    def _enter(self, side, ref, stop, target) -> None:
        stop, target = self._tick(stop), self._tick(target)
        if abs(ref - stop) <= 0 or (target - ref) * (1 if side == OrderSide.BUY else -1) <= 0:
            return
        mult = float(self.instrument.multiplier)
        account = self.portfolio.account(self.config.instrument_id.venue)
        equity = float(account.balance_total(self.instrument.quote_currency))
        qty = int(equity * self.config.risk_pct / 100 // (abs(ref - stop) * mult + self.config.cost_per_contract))
        qty = min(qty, self.config.max_contracts)
        if qty < 1:
            return
        self._n_today += 1
        self._pending = {"side": side, "stop": stop, "target": target}
        self.submit_order(self.order_factory.market(self.config.instrument_id, side, self.instrument.make_qty(qty)))

    def on_position_opened(self, event) -> None:
        p = self._pending or {}
        if not p:
            return
        self._bars_held = 0
        long = p["side"] == OrderSide.BUY
        entry = float(event.avg_px_open)
        if (entry - p["stop"]) * (1 if long else -1) <= 0 or (p["target"] - entry) * (1 if long else -1) <= 0:
            self.close_all_positions(self.config.instrument_id)     # filled past the stop or the target
            return
        qty = self.instrument.make_qty(abs(float(event.quantity)))
        exit_side = OrderSide.SELL if long else OrderSide.BUY
        self.submit_order(self.order_factory.stop_market(
            self.config.instrument_id, exit_side, qty, self.instrument.make_price(p["stop"]),
            time_in_force=TimeInForce.GTC, reduce_only=True))
        self.submit_order(self.order_factory.limit(
            self.config.instrument_id, exit_side, qty, self.instrument.make_price(p["target"]), reduce_only=True))
        p.update(entry=entry, qty=float(qty), opened=pd.Timestamp(event.ts_event, tz="UTC"),
                 risk=abs(entry - p["stop"]) * float(qty) * float(self.instrument.multiplier))

    def on_position_closed(self, event) -> None:
        self.cancel_all_orders(self.config.instrument_id)
        p, self._pending = self._pending or {}, None
        if "risk" not in p:
            return
        pnl = float(event.realized_pnl)
        self.trades.append({"entry_ts": p["opened"], "exit_ts": pd.Timestamp(event.ts_event, tz="UTC"),
                            "side": "LONG" if p["side"] == OrderSide.BUY else "SHORT", "qty": p["qty"],
                            "entry": p["entry"], "exit": float(event.avg_px_close), "stop": p["stop"],
                            "target": p["target"], "pnl": pnl, "risk": p["risk"], "r": pnl / p["risk"]})
