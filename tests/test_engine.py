from datetime import datetime, timezone

import pandas as pd

from bot.book import RiskConfig
from bot.engine import run_tick
from bot.paper import PaperAccount
from bot.strategy import SmaCross

NOON = datetime(2026, 1, 5, 17, 0, tzinfo=timezone.utc)   # 12:00 New York
LATE = datetime(2026, 1, 5, 20, 55, tzinfo=timezone.utc)  # 15:55 New York


class FakeFeed:
    def __init__(self, prices):
        self.prices = prices

    def get_bars(self, symbols, limit):
        idx = pd.date_range("2026-01-01", periods=len(self.prices), freq="15min")
        return {s: pd.DataFrame({"close": self.prices}, index=idx) for s in symbols}


CROSS_UP = [10, 9, 8, 7, 6, 5, 20]
SMA = SmaCross(2, 4)


def test_tick_buys_on_cross_and_respects_market_hours(tmp_path):
    acct = PaperAccount(str(tmp_path / "t.db"), starting_cash=1000)
    feed = FakeFeed(CROSS_UP)
    closed = run_tick(feed, acct, ["QQQ"], SMA, market_open=False, now=NOON)
    assert closed.signals["QQQ"] == "BUY" and not closed.trades
    opened = run_tick(feed, acct, ["QQQ"], SMA, market_open=True, now=NOON)
    assert opened.trades[0]["side"] == "BUY"
    again = run_tick(feed, acct, ["QQQ"], SMA, market_open=True, now=NOON)
    assert not again.trades  # same bar doesn't double-buy
    assert acct.load().positions["QQQ"].qty > 0


def test_flatten_near_close(tmp_path):
    acct = PaperAccount(str(tmp_path / "t.db"), starting_cash=1000)
    risk = RiskConfig(flatten_eod=True)
    run_tick(FakeFeed(CROSS_UP), acct, ["QQQ"], SMA, True, risk, now=NOON)
    late = run_tick(FakeFeed(CROSS_UP), acct, ["QQQ"], SMA, True, risk, now=LATE)
    assert late.trades[0]["reason"] == "end of day"
    assert not acct.load().positions


def test_unfinished_bar_is_ignored(tmp_path):
    acct = PaperAccount(str(tmp_path / "t.db"), starting_cash=1000)
    feed = FakeFeed(CROSS_UP)  # last bar starts 2026-01-01 01:30 UTC
    during = datetime(2026, 1, 1, 1, 35, tzinfo=timezone.utc)
    res = run_tick(feed, acct, ["QQQ"], SMA, True, now=during, timeframe="15Min")
    assert res.signals["QQQ"] != "BUY" and not res.trades
