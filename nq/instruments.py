"""CME micro index futures as NautilusTrader instruments.

Contract specs (CME): MNQ = $2 x Nasdaq-100 index, MES = $5 x S&P 500, both 0.25-point ticks.
Expiry/activation are placeholders: research runs on a continuous proxy series (see data.py)."""
from decimal import Decimal

import pandas as pd

from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AssetClass
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import FuturesContract
from nautilus_trader.model.objects import Price, Quantity

VENUE = Venue("XCME")
SPECS = {"MNQ": {"multiplier": 2, "underlying": "NQ"}, "MES": {"multiplier": 5, "underlying": "ES"}}


def micro_future(root: str, margin_pct: Decimal = Decimal("0.10")) -> FuturesContract:
    spec = SPECS[root]
    start, end = pd.Timestamp("2000-01-01", tz="UTC").value, pd.Timestamp("2100-01-01", tz="UTC").value
    return FuturesContract(
        instrument_id=InstrumentId(Symbol(f"{root}.PROXY"), VENUE),
        raw_symbol=Symbol(f"{root}.PROXY"),
        asset_class=AssetClass.INDEX,
        currency=USD,
        price_precision=2,
        price_increment=Price.from_str("0.25"),
        multiplier=Quantity.from_int(spec["multiplier"]),
        lot_size=Quantity.from_int(1),
        underlying=spec["underlying"],
        activation_ns=start,
        expiration_ns=end,
        ts_event=start,
        ts_init=start,
        margin_init=margin_pct,
        margin_maint=margin_pct,
        exchange="XCME",
    )
