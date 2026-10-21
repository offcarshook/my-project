"""Paper trading account stored in SQLite."""
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS account (id INTEGER PRIMARY KEY CHECK (id = 1), cash REAL NOT NULL,
                                    starting_cash REAL NOT NULL);
CREATE TABLE IF NOT EXISTS positions (symbol TEXT PRIMARY KEY, qty REAL NOT NULL,
                                      avg_price REAL NOT NULL);
CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
                                   symbol TEXT NOT NULL, side TEXT NOT NULL, qty REAL NOT NULL,
                                   price REAL NOT NULL, pnl REAL);
"""


class PaperAccount:
    def __init__(self, db_path: str, starting_cash: float = 10_000, slippage_pct: float = 0.05):
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.slippage = slippage_pct / 100
        if self.db.execute("SELECT 1 FROM account").fetchone() is None:
            self.db.execute("INSERT INTO account VALUES (1, ?, ?)", (starting_cash, starting_cash))
            self.db.commit()

    @property
    def cash(self) -> float:
        return self.db.execute("SELECT cash FROM account").fetchone()["cash"]

    @property
    def starting_cash(self) -> float:
        return self.db.execute("SELECT starting_cash FROM account").fetchone()["starting_cash"]

    def positions(self) -> dict[str, dict]:
        rows = self.db.execute("SELECT * FROM positions").fetchall()
        return {r["symbol"]: {"qty": r["qty"], "avg_price": r["avg_price"]} for r in rows}

    def trades(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM trades ORDER BY id DESC")]

    def equity(self, prices: dict[str, float]) -> float:
        held = sum(p["qty"] * prices.get(s, p["avg_price"]) for s, p in self.positions().items())
        return self.cash + held

    def buy(self, symbol: str, price: float, budget: float) -> dict | None:
        """Buy whole shares worth up to `budget`. Skips if already holding the symbol."""
        if symbol in self.positions():
            return None
        fill = price * (1 + self.slippage)
        qty = int(min(budget, self.cash) // fill)
        if qty <= 0:
            return None
        self.db.execute("UPDATE account SET cash = cash - ?", (qty * fill,))
        self.db.execute("INSERT INTO positions VALUES (?, ?, ?)", (symbol, qty, fill))
        return self._record(symbol, "BUY", qty, fill, None)

    def sell(self, symbol: str, price: float) -> dict | None:
        pos = self.positions().get(symbol)
        if pos is None:
            return None
        fill = price * (1 - self.slippage)
        pnl = (fill - pos["avg_price"]) * pos["qty"]
        self.db.execute("UPDATE account SET cash = cash + ?", (pos["qty"] * fill,))
        self.db.execute("DELETE FROM positions WHERE symbol = ?", (symbol,))
        return self._record(symbol, "SELL", pos["qty"], fill, pnl)

    def reset(self, starting_cash: float) -> None:
        self.db.executescript("DELETE FROM positions; DELETE FROM trades; DELETE FROM account;")
        self.db.execute("INSERT INTO account VALUES (1, ?, ?)", (starting_cash, starting_cash))
        self.db.commit()

    def _record(self, symbol, side, qty, price, pnl) -> dict:
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.db.execute(
            "INSERT INTO trades (ts, symbol, side, qty, price, pnl) VALUES (?, ?, ?, ?, ?, ?)",
            (ts, symbol, side, qty, price, pnl),
        )
        self.db.commit()
        return {"ts": ts, "symbol": symbol, "side": side, "qty": qty, "price": price, "pnl": pnl}
