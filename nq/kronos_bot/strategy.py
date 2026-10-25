"""The ONE bot: a once-a-day, time-boxed Nasdaq-100 trade whose direction comes from a signal
(pre-computed causally, e.g. Kronos features or a baseline rule), as a NautilusTrader Strategy.

Rules (identical for every signal source, so comparisons are like-for-like):
* at the close of the `entry_time` candle, if today's signal is non-zero and the bot is flat and
  not halted: market order in the signal's direction;
* size = floor(equity x risk_pct x size_mult / (stop points x multiplier + cost per contract)),
  capped at `max_contracts` and at `max_leverage` x equity of notional;
* protective stop-market `stop_atr` x ATR from the fill (last `atr_period` regular-session
  15-minute candles; a session's first candle uses its own range, so overnight gaps don't count);
* exit at the close of the `exit_time` candle (or 10 minutes before an early close);
* at most one trade per day; a missing entry candle means no trade that day;
* risk guard: if equity falls `max_daily_loss_pct` below the day's starting equity, flatten and
  stop for the day; if it falls `max_total_dd_pct` below the starting balance, flatten and stop
  for good;
* rejected or denied orders are logged and the day is skipped (no retries, no duplicates).
"""
import math
from collections import deque

import pandas as pd

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy

NY = "America/New_York"


class SessionSignalConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    signals: tuple = ()               # ((YYYY-MM-DD, direction -1/0/1, size_mult), ...)
    entry_time: str = "15:30"         # candle CLOSE time (New York)
    exit_time: str = "16:00"
    bar_minutes: int = 15
    risk_pct: float = 0.5
    stop_atr: float = 2.0
    atr_period: int = 14
    max_daily_loss_pct: float = 3.0
    max_total_dd_pct: float = 10.0
    max_contracts: int = 50
    max_leverage: float = 0.0         # cap on notional / equity (0 = no cap)
    cost_per_contract: float = 2.7
    early_closes: tuple = ()


class SessionSignalBot(Strategy):
    def __init__(self, config: SessionSignalConfig) -> None:
        super().__init__(config)
        self.instrument = None
        self.signals = {d: (int(s), float(m)) for d, s, m in config.signals}
        self.trades: list[dict] = []
        self.events: list[dict] = []
        self._tr = deque(maxlen=config.atr_period)
        self._day = None
        self._pending = None
        self._halted_for_good = False
        self._start_equity = None

    # ---- lifecycle -----------------------------------------------------------------
    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        self.subscribe_bars(self.config.bar_type)

    def on_stop(self) -> None:
        self.cancel_all_orders(self.config.instrument_id)
        self.close_all_positions(self.config.instrument_id)

    def _equity(self) -> float:
        account = self.portfolio.account(self.config.instrument_id.venue)
        bal = float(account.balance_total(self.instrument.quote_currency))
        upnl = self.portfolio.unrealized_pnl(self.config.instrument_id)
        return bal + (float(upnl) if upnl is not None else 0.0)

    def _exit_time(self, t) -> str:
        for day, close in self.config.early_closes:
            if day == t.strftime("%Y-%m-%d"):
                h, m = map(int, close.split(":"))
                return min(self.config.exit_time, f"{(h * 60 + m - 10) // 60:02d}:{(h * 60 + m - 10) % 60:02d}")
        return self.config.exit_time

    def _flatten(self, why: str, t) -> None:
        self.cancel_all_orders(self.config.instrument_id)
        if not self.portfolio.is_flat(self.config.instrument_id):
            self.close_all_positions(self.config.instrument_id)
        self.events.append({"ts": t, "event": why})

    # ---- decisions -----------------------------------------------------------------
    def on_bar(self, bar: Bar) -> None:
        cfg = self.config
        t = pd.Timestamp(bar.ts_event, tz="UTC").tz_convert(NY)
        day = t.strftime("%Y-%m-%d")
        if self._start_equity is None:
            self._start_equity = self._equity()
        if day != self._day:                                  # new session
            self._day, self._traded, self._halted_today = day, False, False
            self._prev_close = None
            self._day_equity = self._equity()
        h, lo, c = float(bar.high), float(bar.low), float(bar.close)
        tr = h - lo if self._prev_close is None else max(h - lo, abs(h - self._prev_close), abs(lo - self._prev_close))
        self._tr.append(tr)
        self._prev_close = c
        hhmm = t.strftime("%H:%M")

        eq = self._equity()                                  # risk guard first
        if not self._halted_for_good and eq <= self._start_equity * (1 - cfg.max_total_dd_pct / 100):
            self._halted_for_good = True
            self._flatten("max total drawdown: trading stopped", t)
        elif not self._halted_today and eq <= self._day_equity * (1 - cfg.max_daily_loss_pct / 100):
            self._halted_today = True
            self._flatten("max daily loss: stopped for the day", t)
        if hhmm >= self._exit_time(t):
            if not self.portfolio.is_flat(cfg.instrument_id):
                self._flatten("time exit", t)
            return
        if hhmm != cfg.entry_time or self._traded or self._halted_today or self._halted_for_good:
            return
        sig, mult = self.signals.get(day, (0, 0.0))
        if sig == 0 or mult <= 0 or not self.portfolio.is_flat(cfg.instrument_id) or len(self._tr) < 4:
            return
        atr = sum(self._tr) / len(self._tr)
        stop_pts = max(cfg.stop_atr * atr, 4 * float(self.instrument.price_increment))
        risk_money = eq * cfg.risk_pct / 100 * mult
        qty = int(risk_money // (stop_pts * float(self.instrument.multiplier) + cfg.cost_per_contract))
        qty = min(qty, cfg.max_contracts)
        if cfg.max_leverage > 0:
            qty = min(qty, int(eq * cfg.max_leverage // (c * float(self.instrument.multiplier))))
        self._traded = True                                   # one attempt per day, even if rejected
        if qty < 1:
            self.events.append({"ts": t, "event": "size below one contract"})
            return
        side = OrderSide.BUY if sig > 0 else OrderSide.SELL
        self._pending = {"side": side, "stop_pts": stop_pts, "signal_ts": t, "ref": c}
        self.submit_order(self.order_factory.market(cfg.instrument_id, side, self.instrument.make_qty(qty)))

    # ---- orders, protection, journal -----------------------------------------------
    def on_order_rejected(self, event) -> None:
        self.events.append({"ts": pd.Timestamp(event.ts_event, tz="UTC"), "event": f"order rejected: {event.reason}"})
        self._pending = None

    def on_order_denied(self, event) -> None:
        self.events.append({"ts": pd.Timestamp(event.ts_event, tz="UTC"), "event": f"order denied: {event.reason}"})
        self._pending = None

    def on_position_opened(self, event) -> None:
        p = self._pending
        if not p:
            return
        long = p["side"] == OrderSide.BUY
        entry = float(event.avg_px_open)
        inc = float(self.instrument.price_increment)
        stop = round((entry - p["stop_pts"] if long else entry + p["stop_pts"]) / inc) * inc
        qty = self.instrument.make_qty(abs(float(event.quantity)))
        self.submit_order(self.order_factory.stop_market(
            self.config.instrument_id, OrderSide.SELL if long else OrderSide.BUY, qty,
            self.instrument.make_price(stop), time_in_force=TimeInForce.GTC, reduce_only=True))
        p.update(entry=entry, stop=stop, qty=float(qty), opened=pd.Timestamp(event.ts_event, tz="UTC"),
                 risk=abs(entry - stop) * float(qty) * float(self.instrument.multiplier))

    def on_position_closed(self, event) -> None:
        self.cancel_all_orders(self.config.instrument_id)
        p, self._pending = self._pending or {}, None
        if "risk" not in p:
            return
        pnl = float(event.realized_pnl)
        closing = self.cache.order(event.closing_order_id)
        kind = {"STOP_MARKET": "stop"}.get(closing.order_type.name, "market") if closing else "?"
        self.trades.append({"exit_kind": kind, "entry_ts": p["opened"], "exit_ts": pd.Timestamp(event.ts_event, tz="UTC"),
                            "side": "LONG" if p["side"] == OrderSide.BUY else "SHORT", "qty": p["qty"],
                            "entry": p["entry"], "exit": float(event.avg_px_close), "stop": p["stop"],
                            "pnl": pnl, "risk": p["risk"], "r": pnl / p["risk"] if p["risk"] else math.nan})
