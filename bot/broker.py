"""Alpaca paper-trading client. It only ever talks to the paper endpoint: any attempt to point
it at a live trading URL raises before a single request is sent."""
import os

import pandas as pd

from bot.config import Settings
from bot.data import AlpacaData
from bot.http import ApiError, request_json

PAPER_URL = "https://paper-api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"


class LiveTradingRefused(RuntimeError):
    pass


class AlpacaPaperBroker:
    def __init__(self, settings: Settings | None = None, data_feed: str = "iex"):
        settings = settings or Settings()
        if not settings.has_alpaca_keys:
            raise RuntimeError("ALPACA_API_KEY / ALPACA_SECRET_KEY are not set (see .env.example)")
        base = os.getenv("ALPACA_BASE_URL", PAPER_URL).rstrip("/")
        if base != PAPER_URL:
            raise LiveTradingRefused(f"refusing to trade against {base}: this bot only uses {PAPER_URL}")
        self.base = PAPER_URL
        self.headers = {"APCA-API-KEY-ID": settings.alpaca_key, "APCA-API-SECRET-KEY": settings.alpaca_secret}
        self.feed = data_feed
        self.data = AlpacaData(settings)

    def _req(self, method: str, path: str, params=None, json=None):
        return request_json(method, f"{self.base}{path}", self.headers, params=params, json=json)

    # ---- account & market ---------------------------------------------------
    def account(self) -> dict:
        return self._req("GET", "/v2/account")

    def clock(self) -> dict:
        return self._req("GET", "/v2/clock")

    def calendar(self, start: str, end: str) -> list[dict]:
        return self._req("GET", "/v2/calendar", params={"start": start, "end": end})

    def asset(self, symbol: str) -> dict:
        return self._req("GET", f"/v2/assets/{symbol}")

    # ---- positions & orders -------------------------------------------------
    def positions(self) -> list[dict]:
        return self._req("GET", "/v2/positions")

    def orders(self, status: str = "all", after: str | None = None, limit: int = 500) -> list[dict]:
        params = {"status": status, "limit": limit, "nested": "true", "direction": "asc"}
        if after:
            params["after"] = after
        return self._req("GET", "/v2/orders", params=params)

    def order_by_client_id(self, client_order_id: str) -> dict | None:
        try:
            return self._req("GET", "/v2/orders:by_client_order_id", params={"client_order_id": client_order_id})
        except ApiError as exc:
            if exc.status == 404:
                return None
            raise

    def submit_order(self, **order) -> dict:
        return self._req("POST", "/v2/orders", json=order)

    def replace_order(self, order_id: str, **changes) -> dict:
        return self._req("PATCH", f"/v2/orders/{order_id}", json=changes)

    def cancel_order(self, order_id: str) -> None:
        self._req("DELETE", f"/v2/orders/{order_id}")

    def cancel_all(self) -> list[dict]:
        return self._req("DELETE", "/v2/orders") or []

    def close_position(self, symbol: str) -> dict:
        return self._req("DELETE", f"/v2/positions/{symbol}")

    def close_all(self) -> list[dict]:
        """Close every position (cancelling their orders). Alpaca answers 207 Multi-Status with one
        {symbol, status, body} item per position, and some can fail while others succeed."""
        return self._req("DELETE", "/v2/positions", params={"cancel_orders": "true"}) or []

    # ---- market data --------------------------------------------------------
    def bars(self, symbols: list[str], start, timeframe: str) -> dict[str, pd.DataFrame]:
        return self.data.get_history(symbols, start, timeframe, feed=self.feed)

    def latest_quote(self, symbol: str) -> dict | None:
        try:
            payload = request_json("GET", f"{DATA_URL}/v2/stocks/{symbol}/quotes/latest", self.headers,
                                   params={"feed": self.feed})
        except ApiError:
            return None
        q = (payload or {}).get("quote") or {}
        return {"bid": q.get("bp"), "ask": q.get("ap")} if q else None
