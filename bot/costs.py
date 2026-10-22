"""Trading cost model: half the bid-ask spread (never under one cent) plus slippage on market
and stop orders, and per-trade fees (commission, SEC fee and FINRA TAF on sells).

Note: backtest prices are split/dividend-adjusted, so the one-cent floor is applied to adjusted
prices. Close enough for recent data; older bars of reverse-split ETFs will understate it."""
from dataclasses import dataclass
from typing import Callable

from bot.config import CostSettings


@dataclass
class CostModel:
    settings: CostSettings
    spread_bps: Callable[[str], float]   # full spread per symbol, in basis points

    def half_spread_px(self, symbol: str, price: float) -> float:
        """Half the bid-ask spread in dollars. A US stock spread can't be under one cent, which
        matters for low-priced ETFs (one tick on a $9 SQQQ is ~11 bps). A spread of 0 bps
        switches spread costs off entirely (for isolating other effects in tests)."""
        bps = self.spread_bps(symbol)
        return max(price * bps / 2 / 10_000, 0.005) if bps > 0 else 0.0

    def market_fill(self, symbol: str, side: int, ref_price: float) -> float:
        """Fill price for a market (or triggered stop) order: side +1 buys, -1 sells."""
        return ref_price + side * (self.half_spread_px(symbol, ref_price) + ref_price * self.settings.slippage_bps / 10_000)

    def limit_touched(self, symbol: str, side: int, limit: float, high: float, low: float) -> bool:
        """A limit order only counts as filled once the candle traded through it by half the
        spread (candles are trade prices; the other side of the book has to reach the limit)."""
        if side < 0:  # sell limit, e.g. take profit on a long
            return high >= limit + self.half_spread_px(symbol, limit)
        return low <= limit - self.half_spread_px(symbol, limit)

    def fees(self, qty: float, price: float, is_sell: bool) -> float:
        s = self.settings
        fee = max(s.commission_per_share * qty, s.min_commission if qty else 0.0)
        if is_sell:
            fee += s.sec_fee_rate * qty * price + min(s.taf_per_share * qty, s.taf_max)
        return fee

    def per_share_estimate(self, symbol: str, price: float) -> float:
        """Rough round-trip cost per share, used to keep sizing honest."""
        s = self.settings
        return 2 * (self.half_spread_px(symbol, price) + price * s.slippage_bps / 10_000) + 2 * s.commission_per_share \
            + s.sec_fee_rate * price + s.taf_per_share
