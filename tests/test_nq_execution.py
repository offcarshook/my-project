"""Execution-model audit for nq/ (NautilusTrader 1.231.0 bar execution).

A probe strategy submits exact orders at exact candles; each test checks the fill price, fee and
account reconciliation against hand-computed values. Candles are replayed Open->High->Low->Close.
"""
import pandas as pd
import pytest

pytest.importorskip("nautilus_trader")

from nautilus_trader.config import StrategyConfig  # noqa: E402
from nautilus_trader.model.data import BarType  # noqa: E402
from nautilus_trader.model.enums import OrderSide  # noqa: E402
from nautilus_trader.model.identifiers import InstrumentId  # noqa: E402
from nautilus_trader.trading.strategy import Strategy  # noqa: E402

from nq.backtest import COMMISSION_PER_SIDE, run_strategy  # noqa: E402
from nq.data import proxy_frame  # noqa: E402
from nq.execution import conservative_adjust  # noqa: E402

NY = "America/New_York"
TICK, MULT = 0.25, 2.0
STOP_SLIP = TICK * MULT          # $ per contract booked for a stop fill


class ProbeConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    actions: tuple = ()          # ((bar_number, kind, side, price), ...) kind: market | stop | limit
    qty: int = 1
    cost_per_contract: float = 0.0


class Probe(Strategy):
    def __init__(self, config):
        super().__init__(config)
        self.n, self.fills, self.trades = 0, [], []

    def on_start(self):
        self.instrument = self.cache.instrument(self.config.instrument_id)
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar):
        for n, kind, side, price in self.config.actions:
            if n != self.n:
                continue
            side = OrderSide.BUY if side == "buy" else OrderSide.SELL
            q = self.instrument.make_qty(self.config.qty)
            f = self.order_factory
            if kind == "market":
                order = f.market(self.config.instrument_id, side, q)
            elif kind == "stop":
                order = f.stop_market(self.config.instrument_id, side, q, self.instrument.make_price(price))
            else:
                order = f.limit(self.config.instrument_id, side, q, self.instrument.make_price(price))
            self.submit_order(order)
        self.n += 1

    def on_order_filled(self, event):
        order = self.cache.order(event.client_order_id)
        self.fills.append({"kind": order.order_type.name, "side": event.order_side.name, "px": float(event.last_px),
                           "qty": float(event.last_qty), "fee": float(event.commission),
                           "ts": pd.Timestamp(event.ts_event, tz="UTC").tz_convert(NY).strftime("%H:%M")})


def candles(rows):
    """rows of (open, high, low, close), 5-minute candles from 10:00 (candle START times)."""
    idx = pd.date_range("2026-01-05 10:00", periods=len(rows), freq="5min", tz=NY)
    return proxy_frame(pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx).assign(volume=1e5), 1.0)


def run(rows, actions, qty=1):
    holder = {}

    class Recording(Probe):
        def __init__(self, config):
            super().__init__(config)
            holder["s"] = self
    _, equity = run_strategy(Recording, ProbeConfig, candles(rows), actions=tuple(actions), qty=qty)
    return holder["s"].fills, equity


FLAT = (20000, 20001, 19999, 20000)


# ---- market orders ---------------------------------------------------------------------
def test_market_orders_fill_at_the_callback_bar_close_plus_one_tick_and_pay_commission_only():
    fills, _ = run([FLAT, (20000, 20012, 19998, 20010), FLAT, FLAT], [(1, "market", "buy", None), (2, "market", "sell", None)],
                   qty=3)
    buy, sell = fills
    assert buy["kind"] == "MARKET" and buy["px"] == 20010 + TICK          # bar 1 close + 1 tick
    assert sell["px"] == 20000 - TICK                                      # bar 2 close - 1 tick
    assert buy["fee"] == pytest.approx(3 * COMMISSION_PER_SIDE)            # slippage NOT also in the fee
    assert sell["fee"] == pytest.approx(3 * COMMISSION_PER_SIDE)


# ---- stop-market orders -------------------------------------------------------------------
def test_sell_stop_triggered_inside_a_bar_fills_at_trigger_with_slippage_as_cost():
    rows = [FLAT, FLAT, (20000, 20002, 19980, 19985), FLAT]
    fills, _ = run(rows, [(0, "market", "buy", None), (1, "stop", "sell", 19990)])
    stop = fills[1]
    assert stop["kind"] == "STOP_MARKET" and stop["px"] == 19990           # trigger, not the 19980 low
    assert stop["fee"] == pytest.approx(COMMISSION_PER_SIDE + STOP_SLIP)


def test_sell_stop_gapped_through_fills_at_the_open():
    rows = [FLAT, FLAT, (19970, 19975, 19960, 19965), FLAT]
    fills, _ = run(rows, [(0, "market", "buy", None), (1, "stop", "sell", 19990)])
    assert fills[1]["px"] == 19970                                         # the gap open, worse than the trigger


def test_buy_stop_above_the_market_fills_at_trigger():
    rows = [FLAT, FLAT, (20000, 20020, 19999, 20015), FLAT]
    fills, _ = run(rows, [(1, "stop", "buy", 20010)])
    assert fills[0]["px"] == 20010 and fills[0]["side"] == "BUY"


# ---- limit orders ---------------------------------------------------------------------------
@pytest.mark.parametrize("bar, filled, px", [
    ((20000, 20010, 19999, 20005), False, None),    # high == limit: a touch, no fill
    ((20000, 20012, 19999, 20005), True, 20010),    # trades through: exactly the limit
    ((20020, 20025, 20015, 20018), True, 20010),    # gaps above: still the limit (no price improvement)
])
def test_sell_limit_needs_a_trade_through_and_never_fills_worse(bar, filled, px):
    fills, _ = run([FLAT, FLAT, bar, FLAT], [(0, "market", "buy", None), (1, "limit", "sell", 20010)])
    lim = [f for f in fills if f["kind"] == "LIMIT"]
    assert bool(lim) is filled
    if filled:
        assert lim[0]["px"] == px and lim[0]["px"] >= 20010
        assert lim[0]["fee"] == pytest.approx(COMMISSION_PER_SIDE)        # no slippage on limits


@pytest.mark.parametrize("bar, filled, px", [
    ((20000, 20001, 19990, 19995), False, None),    # low == limit: touch only
    ((20000, 20001, 19988, 19995), True, 19990),
    ((19980, 19985, 19975, 19982), True, 19990),    # gap below: still the limit (no price improvement)
])
def test_buy_limit_needs_a_trade_through_and_never_fills_worse(bar, filled, px):
    fills, _ = run([FLAT, FLAT, bar, FLAT], [(1, "limit", "buy", 19990)])
    assert bool(fills) is filled
    if filled:
        assert fills[0]["px"] == px and fills[0]["px"] <= 19990


# ---- one bar reaching both stop and target ----------------------------------------------------
def test_bar_touching_stop_and_target_engine_order_and_conservative_rebooking():
    rows = [FLAT, FLAT, (20000, 20020, 19970, 20000), FLAT]                # reaches 20010 AND 19980
    fills, _ = run(rows, [(0, "market", "buy", None), (1, "stop", "sell", 19980), (1, "limit", "sell", 20010)])
    first_exit = fills[1]
    assert first_exit["kind"] == "LIMIT" and first_exit["px"] == 20010     # O->H->L->C: target first (optimistic)
    entry = fills[0]["px"]
    trade = pd.DataFrame([{"side": "LONG", "entry": entry, "stop": 19980.0, "target": 20010.0, "qty": 1.0,
                           "exit": 20010.0, "exit_kind": "target", "risk": (entry - 19980) * MULT,
                           "pnl": (20010 - entry) * MULT - 2 * COMMISSION_PER_SIDE,
                           "r": 0.0, "exit_ts": candles(rows).index[2]}])
    adj = conservative_adjust(trade, candles(rows), MULT, COMMISSION_PER_SIDE)
    assert adj.ambiguous.iloc[0] and adj.exit_kind.iloc[0] == "stop" and adj.exit.iloc[0] == 19980
    assert adj.pnl.iloc[0] == pytest.approx((19980 - entry) * MULT - 2 * COMMISSION_PER_SIDE - STOP_SLIP)


def test_short_with_stop_and_target_in_one_bar_hits_the_stop_first():
    rows = [FLAT, FLAT, (20000, 20020, 19970, 20000), FLAT]
    fills, _ = run(rows, [(0, "market", "sell", None), (1, "stop", "buy", 20010), (1, "limit", "buy", 19980)])
    assert fills[1]["kind"] == "STOP_MARKET" and fills[1]["px"] == 20010


# ---- reconciliation: no double counting --------------------------------------------------------
def test_account_equals_fill_prices_minus_fees_exactly():
    rows = [FLAT, (20000, 20012, 19998, 20010), (20010, 20030, 20005, 20025), (20025, 20026, 19990, 19995), FLAT]
    fills, equity = run(rows, [(1, "market", "buy", None), (2, "stop", "sell", 20000)], qty=2)
    cash_flow = sum((f["px"] if f["side"] == "SELL" else -f["px"]) * f["qty"] * MULT for f in fills)
    fees = sum(f["fee"] for f in fills)
    assert float(equity.iloc[-1]) == pytest.approx(50_000 + cash_flow - fees)
    assert fees == pytest.approx(2 * (2 * COMMISSION_PER_SIDE) + 2 * STOP_SLIP)   # one slippage per fill, once
