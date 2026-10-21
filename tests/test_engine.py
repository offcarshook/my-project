import pandas as pd

from bot.engine import run_tick
from bot.paper import PaperAccount


class FakeFeed:
    def __init__(self, prices):
        self.prices = prices

    def get_bars(self, symbols, limit):
        idx = pd.date_range("2026-01-01", periods=len(self.prices), freq="15min")
        return {s: pd.DataFrame({"close": self.prices}, index=idx) for s in symbols}


def test_tick_buys_on_cross_and_respects_market_hours(tmp_path):
    acct = PaperAccount(str(tmp_path / "t.db"), starting_cash=1000, slippage_pct=0)
    feed = FakeFeed([10, 9, 8, 7, 6, 5, 20])
    closed = run_tick(feed, acct, ["QQQ"], 2, 4, market_open=False)
    assert closed.signals["QQQ"] == "BUY" and not closed.trades
    opened = run_tick(feed, acct, ["QQQ"], 2, 4, market_open=True)
    assert opened.trades[0]["side"] == "BUY"
    again = run_tick(feed, acct, ["QQQ"], 2, 4, market_open=True)
    assert not again.trades  # same bar doesn't double-buy
