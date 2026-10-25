"""Execution model for the NQ research backtests (NautilusTrader 1.231.0).

How the installed engine fills orders on bar data (verified in backtest/engine.pyx):
* Each bar is replayed as four trade prices in the fixed order Open -> High -> Low -> Close
  (`bar_adaptive_high_low_ordering=False`). A market order sent from `on_bar` fills at the last
  replayed price, i.e. that bar's close.
* `FillModel(prob_slippage=p)` slips EVERY fill on an L1 book by a tick, limit orders included
  (engine.pyx `if self.book_type == BookType.L1_MBP and self._fill_model.is_slipped()`), so it
  can't model "market and stop orders slip, limits don't".
* `FillModel.get_orderbook_for_fill_simulation(instrument, order, best_bid, best_ask)` is called
  for market-style and limit fills and may return a simulated book per ORDER.

* Stop-market orders: the engine fills at the TRIGGER price when the bar's replay moves through
  it, and at the market (the open) when a bar gaps past it, using an internal `_fill_at_market`
  flag. The fill-model hook can't see that flag, so overriding the book for stops would fill them
  at the replayed high/low instead (verified: a stop at 19950 filled at the 19940 low).

So:
* MARKET orders: ConservativeFillModel returns a book with liquidity `slippage_ticks` beyond the
  touch, so the fill PRICE carries the slippage.
* STOP_MARKET orders: engine logic (trigger price, or the open on a gap); their slippage is
  charged as a separately-reported cost by CommissionAndStopSlippage.
* LIMIT orders: engine logic, filled at the limit price, and with prob_fill_on_limit=0 only when
  the market trades through the limit (a touch doesn't fill). No slippage. When a bar gaps past
  a resting limit the engine still fills at the limit, not the better open (no price improvement:
  conservative).
* Commissions: per contract on every fill (CommissionAndStopSlippage).
Each trade's slippage is therefore counted exactly once: in the price (market) or in the fee
(stop), never both.

Ambiguous bars: when one bar reaches both a long position's target and its stop, the fixed
O->H->L->C replay fills the target first (optimistic). `conservative_adjust` re-books those exits
as stop-outs (for shorts the replay already hits the stop first)."""
import pandas as pd

from nautilus_trader.backtest.models import FeeModel, FillModel
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import BookOrder
from nautilus_trader.model.enums import BookType, OrderSide, OrderType
from nautilus_trader.model.objects import Money, Quantity

PRICE_SLIPPED = {OrderType.MARKET}          # slippage in the fill price
FEE_SLIPPED = {OrderType.STOP_MARKET}        # slippage as a cost (keeps the engine's trigger/gap logic)


class ConservativeFillModel(FillModel):
    def __init__(self, slippage_ticks: int = 1):
        super().__init__(prob_fill_on_limit=0.0, prob_slippage=0.0, random_seed=1)
        self.slippage_ticks = slippage_ticks

    def get_orderbook_for_fill_simulation(self, instrument, order, best_bid, best_ask):
        if order.order_type not in PRICE_SLIPPED or self.slippage_ticks == 0:
            return None                                   # stops and limits: standard engine logic
        offset = instrument.price_increment.as_double() * self.slippage_ticks
        book = OrderBook(instrument_id=instrument.id, book_type=BookType.L2_MBP)
        size = Quantity(1_000_000, instrument.size_precision)
        book.add(BookOrder(OrderSide.BUY, instrument.make_price(best_bid.as_double() - offset), size, 1), 0, 0)
        book.add(BookOrder(OrderSide.SELL, instrument.make_price(best_ask.as_double() + offset), size, 2), 0, 0)
        return book


class CommissionAndStopSlippage(FeeModel):
    """Commission per contract on every fill, plus `slippage_ticks` x tick value per contract on
    stop-market fills only (market-order slippage is already in the fill price)."""

    def __init__(self, commission_per_side: float, slippage_ticks: int = 1):
        super().__init__()
        self.commission, self.ticks = commission_per_side, slippage_ticks

    def get_commission(self, order, fill_qty, fill_px, instrument):
        qty = float(fill_qty)
        cost = self.commission * qty
        if order.order_type in FEE_SLIPPED:
            cost += self.ticks * instrument.price_increment.as_double() * float(instrument.multiplier) * qty
        return Money(cost, instrument.quote_currency)


def conservative_adjust(trades: pd.DataFrame, bars: pd.DataFrame, multiplier: float, commission_per_side: float,
                        slippage_ticks: int = 1, tick: float = 0.25) -> pd.DataFrame:
    """Re-book target exits whose exit bar also reached the stop as stop-outs: exit at the stop
    price, commissions both sides plus the stop's slippage cost (as the engine would have charged).
    `bars` is indexed by bar CLOSE time, like the engine's bars."""
    if trades.empty or "exit_kind" not in trades:
        return trades.assign(ambiguous=False)
    t = trades.copy()
    t["ambiguous"] = False
    for i, tr in t[t.exit_kind == "target"].iterrows():
        bar = bars.loc[tr.exit_ts] if tr.exit_ts in bars.index else None
        if bar is None:
            continue
        long = tr.side == "LONG"
        if (long and bar.low <= tr.stop) or (not long and bar.high >= tr.stop):
            exit_px = tr.stop
            cost = (2 * commission_per_side + slippage_ticks * tick * multiplier) * tr.qty
            pnl = (exit_px - tr.entry) * (1 if long else -1) * tr.qty * multiplier - cost
            t.loc[i, ["exit", "pnl", "r", "exit_kind", "ambiguous"]] = [exit_px, pnl, pnl / tr.risk, "stop", True]
    return t
