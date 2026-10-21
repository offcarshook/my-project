from bot.book import Book, Position
from bot.paper import PaperAccount


def test_save_and_load_round_trip(tmp_path):
    db = str(tmp_path / "t.db")
    acct = PaperAccount(db, starting_cash=1000)
    book = acct.load()
    assert book.cash == 1000 and book.status == "ACTIVE"
    book.cash = 500
    book.positions["AAPL"] = Position(-5, 100.0, 101.0, 95.0)
    book.last_signal_bar = {"AAPL": "bar1"}
    acct.save(book, [{"ts": "t", "symbol": "AAPL", "side": "SHORT", "qty": 5, "price": 100.0,
                      "pnl": None, "reason": "signal"}])

    again = PaperAccount(db).load()
    assert again.cash == 500 and again.positions["AAPL"] == Position(-5, 100.0, 101.0, 95.0)
    assert again.last_signal_bar == {"AAPL": "bar1"}
    assert PaperAccount(db).trades()[0]["side"] == "SHORT"


def test_reset(tmp_path):
    acct = PaperAccount(str(tmp_path / "t.db"), starting_cash=1000)
    book = acct.load()
    book.positions["MSFT"] = Position(1, 100.0)
    acct.save(book, [])
    acct.reset(2000)
    assert acct.load() == Book.new(2000) or acct.load().cash == 2000
    assert not acct.load().positions and not acct.trades()
