"""Trading cost model: half the bid-ask spread plus slippage on market and stop orders,
and per-trade fees (commission, SEC fee and FINRA TAF on sells)."""
from dataclasses import dataclass
from typing import Callable

from bot.config import CostSettings


@dataclass
class CostModel:
    settings: CostSettings
    spread_bps: Callable[[str], float]   # full spread per symbol, in basis points

    def half_spread(self, symbol: str) -> float:
        return self.spread_bps(symbol) / 2 / 10_000

    def market_fill(self, symbol: str, side: int, ref_price: float) -> float:
        """Fill price for a market (or triggered stop) order: side +1 buys, -1 sells."""
        return ref_price * (1 + side * (self.half_spread(symbol) + self.settings.slippage_bps / 10_000))

    def limit_touched(self, symbol: str, side: int, limit: float, high: float, low: float) -> bool:
        """A limit order only counts as filled once the candle traded through it by half the
        spread (candles are trade prices; the other side of the book has to reach the limit)."""
        if side < 0:  # sell limit, e.g. take profit on a long
            return high >= limit * (1 + self.half_spread(symbol))
        return low <= limit * (1 - self.half_spread(symbol))

    def fees(self, qty: float, price: float, is_sell: bool) -> float:
        s = self.settings
        fee = max(s.commission_per_share * qty, s.min_commission if qty else 0.0)
        if is_sell:
            fee += s.sec_fee_rate * qty * price + min(s.taf_per_share * qty, s.taf_max)
        return fee

    def per_share_estimate(self, symbol: str, price: float) -> float:
        """Rough round-trip cost per share, used to keep sizing honest."""
        s = self.settings
        return price * 2 * (self.half_spread(symbol) + s.slippage_bps / 10_000) + 2 * s.commission_per_share \
            + s.sec_fee_rate * price + s.taf_per_share
