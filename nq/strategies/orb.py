"""Opening range breakout for NQ/MNQ as a NautilusTrader Strategy.

Baseline rules (the ETF study's baseline, so results are comparable):
* range = the first `range_minutes` of the regular session (09:30 New York), all candles present;
* entry = the first COMPLETED candle closing beyond the range (+ optional ATR buffer), market
  order, at most one long and one short per day, no entries after `last_entry`;
* stop = the other side of the range (stop-market, reduce-only), no target by default
  (`target_r` > 0 adds one), everything closed at `flatten_at`;
* size = floor(equity x risk_pct / (stop points x multiplier + per-contract costs)), capped.

Bar timestamps are candle CLOSE times, so every decision uses completed candles only."""
import math
from decimal import Decimal

import pandas as pd

from nautilus_trader.config import StrategyConfig
from nautilus_trader.indicators import AverageTrueRange
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy

NY = "America/New_York"


class OrbConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    range_minutes: int = 15
    last_entry: str = "11:30"         # candle close time; later breakouts are ignored
    flatten_at: str = "15:50"
    risk_pct: float = 0.5
    buffer_atr: float = 0.0
    atr_period: int = 14
    target_r: float = 0.0             # 0 = hold until the stop or flatten time
    min_range_pct: float = 0.15
    max_range_pct: float = 2.5
    max_contracts: int = 20
    cost_per_contract: float = 2.5    # round-trip estimate used only for sizing


class OpeningRangeBreakout(Strategy):
    def __init__(self, config: OrbConfig) -> None:
        super().__init__(config)
        self.instrument = None
        self.atr = AverageTrueRange(config.atr_period)
        self.trades: list[dict] = []          # one row per closed position (for research reports)
        self._day = None
        self._pending: dict | None = None     # entry details until the position opens

    # ---- lifecycle ---------------------------------------------------------
    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        self.register_indicator_for_bars(self.config.bar_type, self.atr)
        self.subscribe_bars(self.config.bar_type)

    def on_stop(self) -> None:
        self.cancel_all_orders(self.config.instrument_id)
        self.close_all_positions(self.config.instrument_id)

    def _reset_day(self, day) -> None:
        self._day, self._hi, self._lo, self._n = day, -math.inf, math.inf, 0
        self._prev_close = None
        self._done = {OrderSide.BUY: False, OrderSide.SELL: False}

    # ---- decisions on each completed candle --------------------------------
    def on_bar(self, bar: Bar) -> None:
        t = pd.Timestamp(bar.ts_event, tz="UTC").tz_convert(NY)
        if t.date() != self._day:
            self._reset_day(t.date())
        hhmm = t.strftime("%H:%M")
        minutes_open = t.hour * 60 + t.minute - 570
        close, high, low = float(bar.close), float(bar.high), float(bar.low)
        need = self.config.range_minutes // 5

        if hhmm >= self.config.flatten_at:
            self.cancel_all_orders(self.config.instrument_id)
            self.close_all_positions(self.config.instrument_id)
            return
        if 0 < minutes_open <= self.config.range_minutes:
            self._hi, self._lo, self._n = max(self._hi, high), min(self._lo, low), self._n + 1
            self._prev_close = close
            return
        if self._n != need or minutes_open <= 0:
            return
        width_pct = (self._hi - self._lo) / close * 100
        if not self.config.min_range_pct <= width_pct <= self.config.max_range_pct:
            return
        buf = self.config.buffer_atr * self.atr.value if self.atr.initialized else 0.0
        up, down = self._hi + buf, self._lo - buf
        prev, self._prev_close = self._prev_close, close
        if prev is None or hhmm > self.config.last_entry or not self.portfolio.is_flat(self.config.instrument_id):
            return
        if close > up and prev <= up and not self._done[OrderSide.BUY]:
            self._enter(OrderSide.BUY, close, self._lo, t)
        elif close < down and prev >= down and not self._done[OrderSide.SELL]:
            self._enter(OrderSide.SELL, close, self._hi, t)

    def _enter(self, side: OrderSide, ref: float, stop: float, t) -> None:
        self._done[side] = True
        mult = float(self.instrument.multiplier)
        stop_pts = abs(ref - stop)
        account = self.portfolio.account(self.config.instrument_id.venue)
        equity = float(account.balance_total(self.instrument.quote_currency))
        qty = int(equity * self.config.risk_pct / 100 // (stop_pts * mult + self.config.cost_per_contract))
        qty = min(qty, self.config.max_contracts)
        if qty < 1:
            return
        self._pending = {"side": side, "stop": stop, "signal_ts": t, "ref": ref}
        self.submit_order(self.order_factory.market(self.config.instrument_id, side, self.instrument.make_qty(qty)))

    # ---- protective orders and the trade log ------------------------------
    def on_position_opened(self, event) -> None:
        p = self._pending or {}
        if not p:
            return
        long = p["side"] == OrderSide.BUY
        entry = float(event.avg_px_open)
        stop = p["stop"]
        if (entry - stop) * (1 if long else -1) <= 0:          # filled through the stop: get out
            self.close_all_positions(self.config.instrument_id)
            return
        qty = self.instrument.make_qty(abs(float(event.quantity)))
        exit_side = OrderSide.SELL if long else OrderSide.BUY
        self.submit_order(self.order_factory.stop_market(
            self.config.instrument_id, exit_side, qty, self.instrument.make_price(stop),
            time_in_force=TimeInForce.GTC, reduce_only=True))
        if self.config.target_r > 0:
            tgt = entry + (entry - stop) * self.config.target_r
            self.submit_order(self.order_factory.limit(
                self.config.instrument_id, exit_side, qty, self.instrument.make_price(tgt), reduce_only=True))
        p.update(entry=entry, qty=float(qty), risk=abs(entry - stop) * float(qty) * float(self.instrument.multiplier),
                 opened=pd.Timestamp(event.ts_event, tz="UTC"))

    def on_position_closed(self, event) -> None:
        self.cancel_all_orders(self.config.instrument_id)
        p, self._pending = self._pending or {}, None
        if "risk" not in p:
            return
        pnl = float(event.realized_pnl)
        closing = self.cache.order(event.closing_order_id)
        kind = {"LIMIT": "target", "STOP_MARKET": "stop"}.get(closing.order_type.name, "market") if closing else "?"
        self.trades.append({"exit_kind": kind, "entry_ts": p["opened"], "exit_ts": pd.Timestamp(event.ts_event, tz="UTC"),
                            "side": "LONG" if p["side"] == OrderSide.BUY else "SHORT", "qty": p["qty"],
                            "entry": p["entry"], "exit": float(event.avg_px_close), "stop": p["stop"],
                            "pnl": pnl, "risk": p["risk"], "r": pnl / p["risk"]})
