from bot.paper import PaperAccount


def test_buy_sell_roundtrip(tmp_path):
    acct = PaperAccount(str(tmp_path / "t.db"), starting_cash=1000, slippage_pct=0)
    buy = acct.buy("AAPL", 100, budget=500)
    assert buy["qty"] == 5 and acct.cash == 500
    assert acct.buy("AAPL", 100, budget=500) is None  # one position per symbol
    sell = acct.sell("AAPL", 110)
    assert sell["pnl"] == 50 and acct.cash == 1050
    assert acct.sell("AAPL", 110) is None
    assert len(acct.trades()) == 2


def test_never_spends_more_than_cash(tmp_path):
    acct = PaperAccount(str(tmp_path / "t.db"), starting_cash=150, slippage_pct=0)
    assert acct.buy("QQQ", 100, budget=10_000)["qty"] == 1
    assert acct.buy("SPY", 100, budget=10_000) is None


def test_reset(tmp_path):
    acct = PaperAccount(str(tmp_path / "t.db"), starting_cash=1000)
    acct.buy("MSFT", 100, budget=500)
    acct.reset(2000)
    assert acct.cash == 2000 and not acct.positions() and not acct.trades()
