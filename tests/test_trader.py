import copy
import json
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from bot.broker import AlpacaPaperBroker, LiveTradingRefused
from bot.config import Settings, load_config
from bot.http import ApiError
from bot.strategy import EmaCross
from bot.trader import KILL_FILE, Trader, emergency_stop

NY = "America/New_York"


def make_cfg(tmp_path):
    c = copy.deepcopy(load_config("config.yaml"))
    c.watchlist = ["QQQ"]
    c.strategy.fast, c.strategy.slow = 3, 6
    c.paper.state_dir, c.paper.log_dir = str(tmp_path / "state"), str(tmp_path / "logs")
    c.challenge.safety_buffer_pct = 0
    return c


def candles(start, closes):
    idx = pd.date_range(start, periods=len(closes), freq="5min", tz=NY).tz_convert("UTC")
    c = np.asarray(closes, float)
    o = np.concatenate([[c[0]], c[:-1]])
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.05, "low": np.minimum(o, c) - 0.05, "close": c,
                         "volume": 1e5}, index=idx)


PRIOR = candles("2026-01-02 09:30", np.linspace(400, 395, 78))
TODAY = candles("2026-01-05 09:30", list(np.linspace(395, 390, 20)) + list(np.linspace(390.5, 398, 30)))
BARS = pd.concat([PRIOR, TODAY])


def first_buy_time(cfg):
    df = EmaCross(cfg.strategy, cfg.risk.stop, 5, "iex").compute(BARS)
    t = df.index[df["signal"] == "BUY"][0]
    return t + pd.Timedelta(minutes=6)   # one minute after the signal candle closed


class FakeBroker:
    def __init__(self, equity=100_000.0):
        self.equity, self.pos, self.orders_list, self.ids = equity, {}, [], set()
        self.closed_all = 0
        self.now = None
        self.next_close = "2026-01-05T16:00:00-05:00"
        self.open = True

    def account(self):
        return {"equity": str(self.equity), "buying_power": str(self.equity * 4)}

    def clock(self):
        return {"is_open": self.open, "next_close": self.next_close}

    def asset(self, sym):
        return {"tradable": True, "shortable": True, "easy_to_borrow": True}

    def positions(self):
        return [{"symbol": s, "qty": str(q), "avg_entry_price": "394.0", "unrealized_pl": "0"} for s, q in self.pos.items()]

    def orders(self, status="all", after=None, limit=500):
        return [o for o in self.orders_list if status in ("all", "closed") or o["status"] == "new"]

    def order_by_client_id(self, cid):
        return next((o for o in self.orders_list if o["client_order_id"] == cid), None)

    def submit_order(self, **o):
        if o["client_order_id"] in self.ids:
            raise ApiError(422, "client_order_id must be unique")
        self.ids.add(o["client_order_id"])
        qty = int(o["qty"]) * (1 if o["side"] == "buy" else -1)
        self.pos[o["symbol"]] = qty
        rec = {**o, "id": f"o{len(self.orders_list)}", "status": "filled", "filled_qty": o["qty"],
               "filled_avg_price": "394.0", "type": "market", "legs": []}
        self.orders_list.append(rec)
        return rec

    def cancel_order(self, oid):
        pass

    def cancel_all(self):
        pass

    def close_position(self, sym):
        self.pos.pop(sym, None)

    def close_all(self):
        self.closed_all += 1
        self.pos = {}

    def bars(self, symbols, start, timeframe):
        return {s: BARS[BARS.index <= self.now] for s in symbols}

    def latest_quote(self, sym):
        return {"bid": 393.99, "ask": 394.01}


def trader_at(tmp_path, when, broker=None, cfg=None):
    cfg = cfg or make_cfg(tmp_path)
    broker = broker or FakeBroker()
    broker.now = pd.Timestamp(when)
    t = Trader(cfg, broker, now=lambda: pd.Timestamp(when).to_pydatetime())
    t.startup()
    return t, broker


def events(tmp_path):
    out = []
    for f in sorted((tmp_path / "logs").glob("*.jsonl")):
        out += [json.loads(line) for line in f.read_text().splitlines()]
    return out


# ---- order duplication ------------------------------------------------------
def test_one_order_per_signal_even_after_restart(tmp_path):
    when = first_buy_time(make_cfg(tmp_path))
    t, broker = trader_at(tmp_path, when)
    assert t.step() == "ok"
    assert len(broker.orders_list) == 1
    o = broker.orders_list[0]
    assert o["order_class"] == "bracket" and o["side"] == "buy"
    assert float(o["stop_loss"]["stop_price"]) < 394 < float(o["take_profit"]["limit_price"])
    t.step()
    assert len(broker.orders_list) == 1           # same candle: nothing new

    t2, _ = trader_at(tmp_path, when, broker=broker)
    t2.step()
    assert len(broker.orders_list) == 1           # restart: state remembers the candle

    # even if the saved state were lost, the deterministic client_order_id blocks a resubmit
    (tmp_path / "state" / "trader_state.json").unlink()
    broker.pos = {}
    t3, _ = trader_at(tmp_path, when, broker=broker)
    t3.step()
    assert len(broker.orders_list) == 1
    assert any(e["type"] == "duplicate_order_prevented" for e in events(tmp_path))


def test_order_rejection_is_logged_and_rolled_back(tmp_path):
    class Rejecting(FakeBroker):
        def submit_order(self, **o):
            raise ApiError(403, "insufficient buying power")
    t, broker = trader_at(tmp_path, first_buy_time(make_cfg(tmp_path)), broker=Rejecting())
    t.step()
    assert t.state["positions"] == {} and t.state["trades_today"] == 0
    assert any(e["type"] == "order_rejected" for e in events(tmp_path))


# ---- session shutdown ---------------------------------------------------------
def test_flattens_at_cutoff_and_stops_entering(tmp_path):
    t, broker = trader_at(tmp_path, pd.Timestamp("2026-01-05 15:51", tz=NY))
    broker.pos = {"QQQ": 10}
    t.state["positions"]["QQQ"] = {"qty": 10, "entry": 394, "stop": 390, "status": "open"}
    assert t.step() == "after_cutoff" and broker.closed_all == 1 and broker.pos == {}
    assert t.step() == "after_cutoff" and broker.closed_all == 1   # flattens once
    assert broker.orders_list == []


def test_early_close_moves_the_cutoff(tmp_path):
    broker = FakeBroker()
    broker.next_close = "2026-01-05T13:00:00-05:00"
    t, _ = trader_at(tmp_path, pd.Timestamp("2026-01-05 12:51", tz=NY), broker=broker)
    assert t.step() == "after_cutoff"


def test_shutdown_flattens_open_positions(tmp_path):
    t, broker = trader_at(tmp_path, first_buy_time(make_cfg(tmp_path)))
    t.step()
    assert broker.pos
    t.shutdown()
    assert broker.closed_all == 1 and t.state["positions"] == {}


def test_market_closed_does_nothing(tmp_path):
    broker = FakeBroker()
    broker.open = False
    t, _ = trader_at(tmp_path, first_buy_time(make_cfg(tmp_path)), broker=broker)
    assert t.step() == "closed" and broker.orders_list == []


# ---- emergency stop and risk limits ------------------------------------------
def test_kill_switch_flattens_and_locks_until_reset(tmp_path):
    cfg = make_cfg(tmp_path)
    t, broker = trader_at(tmp_path, first_buy_time(cfg), cfg=cfg)
    t.step()
    emergency_stop(cfg)
    assert t.step() == "killed" and broker.closed_all == 1 and t.stop_requested
    (tmp_path / "state" / KILL_FILE).unlink()
    t2, _ = trader_at(tmp_path, first_buy_time(cfg) + timedelta(minutes=5), broker=broker, cfg=cfg)
    assert t2.step() == "locked" and len(broker.orders_list) == 1


def test_daily_loss_breach_flattens_and_locks(tmp_path):
    t, broker = trader_at(tmp_path, first_buy_time(make_cfg(tmp_path)))
    t.step()
    broker.equity = 94_000   # -6% with the position open
    assert t.step() == "locked" and broker.closed_all == 1
    assert t.risk.state.locked and "daily" in t.risk.state.reason


def test_profit_target_stops_trading(tmp_path):
    t, broker = trader_at(tmp_path, first_buy_time(make_cfg(tmp_path)))
    broker.equity = 108_500
    assert t.step() == "passed" and broker.orders_list == []


# ---- reconciliation -----------------------------------------------------------
def test_reconcile_closes_unknown_and_detects_closed_positions(tmp_path):
    t, broker = trader_at(tmp_path, pd.Timestamp("2026-01-05 10:00", tz=NY))
    broker.pos = {"SPY": 5}                                   # not ours
    t.state["positions"]["QQQ"] = {"qty": 10, "entry": 394, "stop": 390, "status": "open"}  # gone at broker
    t.reconcile(broker.positions())
    assert "SPY" not in broker.pos and "QQQ" not in t.state["positions"]
    kinds = [e["type"] for e in events(tmp_path)]
    assert "reconcile_unknown_position" in kinds and "exit_detected" in kinds


def test_status_file_written(tmp_path):
    t, broker = trader_at(tmp_path, first_buy_time(make_cfg(tmp_path)))
    t.step()
    status = json.loads((tmp_path / "state" / "status.json").read_text())
    assert status["equity"] == 100_000 and "distance_to_daily_floor" in status and status["trades_today"] == 1


# ---- paper only ---------------------------------------------------------------
def test_live_trading_url_is_refused(monkeypatch):
    monkeypatch.setenv("ALPACA_BASE_URL", "https://api.alpaca.markets")
    with pytest.raises(LiveTradingRefused):
        AlpacaPaperBroker(Settings(alpaca_key="k", alpaca_secret="s"))


def test_trailing_stop_moves_the_stop_leg_up(tmp_path):
    cfg = make_cfg(tmp_path)
    cfg.risk.trailing.enabled, cfg.risk.trailing.activate_r, cfg.risk.trailing.atr_mult = True, 0.5, 0.5

    class WithStopLeg(FakeBroker):
        replaced = []

        def orders(self, status="all", after=None, limit=500):
            if status == "open":
                return [{"id": "stop1", "symbol": "QQQ", "type": "stop"}]
            return []

        def replace_order(self, oid, **changes):
            self.replaced.append((oid, changes))

    broker = WithStopLeg()
    broker.pos = {"QQQ": 100}
    t, _ = trader_at(tmp_path, pd.Timestamp("2026-01-05 13:41", tz=NY), broker=broker, cfg=cfg)
    t.state["positions"]["QQQ"] = {"qty": 100, "entry": 391.0, "stop": 390.0, "risk": 100.0, "status": "open",
                                   "opened": "2026-01-05T11:30:00-05:00"}
    t.step()
    assert broker.replaced and broker.replaced[0][0] == "stop1"
    assert broker.replaced[0][1]["stop_price"] > 390.0 and t.state["positions"]["QQQ"]["stop"] > 390.0
