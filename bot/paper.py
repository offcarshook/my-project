"""Live paper account: a Book persisted to SQLite between bot ticks."""
import json
import sqlite3
from datetime import datetime, timezone

from bot.book import Book, Position

SCHEMA_VERSION = 3
SCHEMA = """
CREATE TABLE IF NOT EXISTS account (id INTEGER PRIMARY KEY CHECK (id = 1), cash REAL NOT NULL,
    starting_cash REAL NOT NULL, started_at TEXT NOT NULL, day TEXT, day_start_equity REAL, last_equity REAL,
    status TEXT NOT NULL, status_reason TEXT NOT NULL, last_signal_bar TEXT NOT NULL,
    trades_today INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS positions (symbol TEXT PRIMARY KEY, qty REAL NOT NULL,
    entry REAL NOT NULL, stop REAL, target REAL);
CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
    symbol TEXT NOT NULL, side TEXT NOT NULL, qty REAL NOT NULL, price REAL NOT NULL,
    pnl REAL, reason TEXT);
"""


class PaperAccount:
    def __init__(self, db_path: str, starting_cash: float = 10_000):
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        if self.db.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
            self.db.executescript("DROP TABLE IF EXISTS account; DROP TABLE IF EXISTS positions;"
                                  "DROP TABLE IF EXISTS trades;")
            self.db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.db.executescript(SCHEMA)
        if self.db.execute("SELECT 1 FROM account").fetchone() is None:
            self.reset(starting_cash)

    def load(self) -> Book:
        a = self.db.execute("SELECT * FROM account").fetchone()
        positions = {r["symbol"]: Position(int(r["qty"]), r["entry"], r["stop"], r["target"])
                     for r in self.db.execute("SELECT * FROM positions")}
        return Book(cash=a["cash"], starting_cash=a["starting_cash"], positions=positions,
                    day=a["day"], day_start_equity=a["day_start_equity"],
                    last_equity=a["last_equity"], status=a["status"],
                    status_reason=a["status_reason"], last_signal_bar=json.loads(a["last_signal_bar"]),
                    trades_today=a["trades_today"])

    def save(self, book: Book, fills: list[dict]) -> None:
        self.db.execute(
            "UPDATE account SET cash=?, day=?, day_start_equity=?, last_equity=?, status=?, "
            "status_reason=?, last_signal_bar=?, trades_today=?",
            (book.cash, book.day, book.day_start_equity, book.last_equity, book.status, book.status_reason,
             json.dumps(book.last_signal_bar), book.trades_today))
        self.db.execute("DELETE FROM positions")
        self.db.executemany("INSERT INTO positions VALUES (?, ?, ?, ?, ?)",
                            [(s, p.qty, p.entry, p.stop, p.target) for s, p in book.positions.items()])
        self.db.executemany(
            "INSERT INTO trades (ts, symbol, side, qty, price, pnl, reason) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(str(f["ts"]), f["symbol"], f["side"], f["qty"], f["price"], f["pnl"], f["reason"])
             for f in fills])
        self.db.commit()

    @property
    def started_at(self) -> datetime:
        return datetime.fromisoformat(self.db.execute("SELECT started_at FROM account").fetchone()[0])

    def trades(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM trades ORDER BY id DESC")]

    def reset(self, starting_cash: float) -> None:
        self.db.executescript("DELETE FROM positions; DELETE FROM trades; DELETE FROM account;")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.db.execute("INSERT INTO account VALUES (1, ?, ?, ?, NULL, NULL, NULL, 'ACTIVE', '', '{}', 0)",
                        (starting_cash, starting_cash, now))
        self.db.commit()
