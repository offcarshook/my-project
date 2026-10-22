"""Paper-trading loop for the Prop Pass strategy (Alpaca paper account only).

Each poll:
  1. emergency stop file?  -> cancel everything, close everything, lock
  2. market closed?        -> wait (and raise an alarm if anything is still open)
  3. reconcile our positions with the broker's (the broker is the source of truth)
  4. risk manager check    -> flatten + lock on a breach, stop at the target   (always runs)
  5. past the cutoff?      -> flatten, and keep re-checking until the broker is really flat
  6. positions it didn't open -> cancel their orders and close them (if configured)
  7. new completed candle  -> exits on opposite signals, then entries (bracket orders with a
                              deterministic client_order_id, so a restart can't double-submit)
  8. log new fills / rejections, write status.json

Time comes from the broker's clock, not the local machine.
"""
import json
import logging
import math
import os
import signal
import threading
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from bot.config import BotConfig
from bot.costs import CostModel
from bot.data import TF_MINUTES
from bot.http import ApiError
from bot.risk import FLATTEN_LOCK, OK, PASSED, TARGET_HIT, OpenPosition, RiskManager, size_position
from bot.strategy import BUY, SELL, EmaCross, stop_distance

log = logging.getLogger("propbot")
NY = "America/New_York"
KILL_FILE = "KILL"
PID_FILE = "bot.pid"
OPEN_ORDER_STATUSES = {"new", "accepted", "pending_new", "partially_filled", "held", "accepted_for_bidding",
                       "pending_replace", "replaced", "calculated", "done_for_day"}
FINAL_ORDER_STATUSES = {"filled", "canceled", "expired", "rejected"}


class EventLog:
    """Append-only JSON-lines log of every signal, order, fill, rejection, exit and risk decision."""

    def __init__(self, log_dir: str):
        self.dir = Path(log_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def __call__(self, kind: str, **data):
        now = datetime.now(timezone.utc)
        record = {"ts": now.isoformat(timespec="seconds"), "type": kind, **data}
        with open(self.dir / f"events-{now:%Y%m%d}.jsonl", "a") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
        log.info("%s %s", kind, {k: v for k, v in data.items() if k != "order"})


def flat_orders(orders: list[dict]) -> list[dict]:
    """Alpaca nests bracket/OTO legs under their parent's `legs`; return parents and legs alike."""
    out = []
    for o in orders or []:
        out.append(o)
        out.extend(o.get("legs") or [])
    return out


class Trader:
    def __init__(self, cfg: BotConfig, broker, now=None, sleep=None):
        self.cfg, self.broker = cfg, broker
        self.local_now = now or (lambda: datetime.now(timezone.utc))
        self.sleep = sleep or time.sleep
        self.state_dir = Path(cfg.paper.state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.state_dir / "trader_state.json"
        self.event = EventLog(cfg.paper.log_dir)
        self.costs = CostModel(cfg.costs, cfg.spread_bps)
        self.bar_minutes = TF_MINUTES[cfg.strategy.timeframe]
        self.bar_len = pd.Timedelta(minutes=self.bar_minutes)
        self.strategy = EmaCross(cfg.strategy, cfg.risk.stop, self.bar_minutes, cfg.paper.data_feed)
        self.stop_requested = False
        self._wake = threading.Event()
        self.shortable: set[str] = set()
        self.tradable: list[str] = []
        self.last_risk_action = None
        self._closed_alerted = False
        self._load_state()

    # ---- state --------------------------------------------------------------
    def _load_state(self):
        if self.state_path.exists():
            s = json.loads(self.state_path.read_text())
            self.state = s
            self.risk = RiskManager.from_dict(self.cfg.challenge, s["risk"]) if s.get("risk") else None
        else:
            self.state = {"session": None, "processed": {}, "trades_today": 0, "positions": {}, "seen_orders": [],
                          "flattened_today": False, "closed_today": []}
            self.risk = None  # created from the account equity on first start

    def save(self):
        self.state["risk"] = self.risk.to_dict() if self.risk else None
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2, default=str))
        tmp.replace(self.state_path)   # atomic, so a crash can't leave half a file

    # ---- startup ------------------------------------------------------------
    def startup(self):
        acct = self.broker.account()
        equity = float(acct["equity"])
        if self.risk is None:
            self.risk = RiskManager(self.cfg.challenge, equity)
            self.event("challenge_started", initial_balance=equity)
        if not self.cfg.challenge.confirmed_firm_rules:
            self.event("warning", message="challenge.confirmed_firm_rules is false: paper practice only. Check that "
                                          "your firm allows ETFs, leveraged ETFs, shorting and bots, and how it "
                                          "measures drawdown, before any real challenge.")
        self._compare_with_backtest()
        for sym in self.cfg.watchlist:
            try:
                a = self.broker.asset(sym)
            except ApiError as exc:
                self.event("asset_unavailable", symbol=sym, error=str(exc))
                continue
            if not a.get("tradable"):
                self.event("asset_unavailable", symbol=sym, error="not tradable")
                continue
            self.tradable.append(sym)
            if a.get("shortable") and a.get("easy_to_borrow"):
                self.shortable.add(sym)
            elif self.cfg.strategy.allow_short:
                self.event("short_disabled", symbol=sym, reason="not shortable / easy to borrow at the broker")
        self.event("startup", equity=equity, tradable=self.tradable, shortable=sorted(self.shortable),
                   risk_state=self.risk.to_dict())
        self.save()

    def _compare_with_backtest(self, reports_dir: str = "reports"):
        """Warn when the bot is about to trade something other than what the last backtest picked."""
        found = sorted(Path(reports_dir).glob("*/selected_config.json"))
        if not found:
            self.event("warning", message="no backtest report found: run `python propbot.py backtest` first")
            return
        sel = json.loads(found[-1].read_text())
        if not sel.get("edge_found", True):
            self.event("warning", message=f"the last backtest ({found[-1].parent.name}) found no configuration "
                                          "profitable after costs; this is practice only")
        diffs = [k for k in ("fast", "slow", "allow_short", "exit_on_opposite")
                 if sel["strategy"].get(k) != getattr(self.cfg.strategy, k)]
        diffs += [f"filters.{k}" for k, v in asdict(self.cfg.strategy.filters).items()
                  if sel["strategy"].get("filters", {}).get(k, {}).get("enabled") != v["enabled"]]
        diffs += [f"risk.{k}" for k in ("risk_per_trade_pct", "reward_risk", "max_positions", "max_trades_per_day")
                  if sel["risk"].get(k) != getattr(self.cfg.risk, k)]
        diffs += [f"risk.stop.{k}" for k in ("type", "atr_mult", "min_pct", "max_pct")
                  if sel["risk"].get("stop", {}).get(k) != getattr(self.cfg.risk.stop, k)]
        if diffs:
            self.event("warning", message=f"config.yaml differs from the last backtest's selection "
                                          f"'{sel.get('name')}' in: {', '.join(diffs)}. Copy the selected settings "
                                          f"from {found[-1]} into config.yaml if you want to trade what was tested.")

    # ---- broker helpers -----------------------------------------------------
    def _broker_now(self, clock: dict) -> datetime:
        local = self.local_now()
        ts = clock.get("timestamp")
        if not ts:
            return local
        broker = pd.Timestamp(ts).tz_convert("UTC").to_pydatetime()
        skew = abs((pd.Timestamp(local) - pd.Timestamp(broker)).total_seconds())
        if skew > 30 and not getattr(self, "_skew_warned", False):
            self.event("warning", message=f"local clock is {skew:.0f}s off the broker clock; using the broker's")
            self._skew_warned = True
        return broker

    def _open_orders(self, symbol: str | None = None) -> list[dict]:
        orders = flat_orders(self.broker.orders(status="open"))
        return [o for o in orders if o.get("status", "new") in OPEN_ORDER_STATUSES
                and (symbol is None or o.get("symbol") == symbol)]

    def _cancel_symbol_orders(self, sym: str, wait_s: float = 3.0) -> bool:
        """Cancel every open order and bracket leg for `sym` and wait until they're gone, so the
        shares are no longer held for orders when the position is closed."""
        for o in self._open_orders(sym):
            try:
                self.broker.cancel_order(o["id"])
            except ApiError as exc:
                if exc.status != 422:   # 422 = already filled/cancelled: fine
                    self.event("error", where=f"cancel {sym} {o['id']}", error=str(exc))
        waited = 0.0
        while self._open_orders(sym) and waited < wait_s:
            self.sleep(0.5)
            waited += 0.5
        return not self._open_orders(sym)

    def _flatten(self, reason: str) -> bool:
        """Cancel everything and close everything. Returns True only if the broker confirmed every
        close; failed symbols stay in state so the next poll retries them."""
        self.event("flatten", reason=reason, positions=list(self.state["positions"]))
        try:
            self.broker.cancel_all()
            results = self.broker.close_all() or []
        except ApiError as exc:
            self.event("error", where="flatten", error=str(exc))
            raise
        failed = {}
        for item in results if isinstance(results, list) else []:
            status = item.get("status", 200)
            if isinstance(status, int) and status >= 400:
                failed[item.get("symbol")] = item.get("body")
        if failed:
            self.event("flatten_failed", severity="critical", failed=failed,
                       message="some positions could not be closed; retrying every poll")
        self.state["positions"] = {s: p for s, p in self.state["positions"].items() if s in failed}
        for sym in failed:
            self.state["positions"].setdefault(sym, {"qty": 0, "entry": 0, "stop": None, "status": "unknown"})
        self.save()
        return not failed

    def _session_flatten_time(self, now: datetime, clock: dict) -> datetime:
        local = pd.Timestamp(now).tz_convert(NY)
        hh, mm = map(int, self.cfg.strategy.flatten_at.split(":"))
        configured = local.normalize() + pd.Timedelta(hours=hh, minutes=mm)
        next_close = pd.Timestamp(clock["next_close"]).tz_convert(NY)
        return min(configured, next_close - pd.Timedelta(minutes=10)).to_pydatetime()

    def _completed_bars(self, now: datetime) -> dict[str, pd.DataFrame]:
        bars_per_day = max(390 // self.bar_minutes, 1)
        days = max(7, math.ceil(self.strategy.warmup / bars_per_day * 7 / 5) + 4)  # enough sessions for warmup
        raw = self.broker.bars(self.tradable, now - timedelta(days=days), self.cfg.strategy.timeframe)
        out = {}
        for sym, df in raw.items():
            if df is None or df.empty:
                self.event("missing_data", symbol=sym)
                continue
            df = df[df.index + self.bar_len <= pd.Timestamp(now)]
            if len(df) > self.strategy.warmup:
                out[sym] = df
            else:
                self.event("missing_data", symbol=sym, reason=f"only {len(df)} completed candles")
        return out

    def _open_positions(self, prices: dict[str, float]) -> list[OpenPosition]:
        out = []
        for sym, p in self.state["positions"].items():
            inst = self.cfg.instrument(sym)
            out.append(OpenPosition(sym, p["qty"], p["entry"], p.get("stop"), prices.get(sym, p["entry"]),
                                    inst.group, inst.leverage, p.get("reserve", 0.0)))
        return out

    def _exit_costs(self, positions: list[dict]) -> float:
        """Estimated cost to close everything now (half spread + slippage + fees)."""
        return sum(abs(float(p["qty"])) * self.costs.per_share_estimate(p["symbol"], float(p.get("current_price")
                   or p["avg_entry_price"])) / 2 for p in positions)

    # ---- reconciliation -----------------------------------------------------
    def reconcile(self, broker_positions: list[dict]) -> list[str]:
        """Bring state in line with the broker. Returns broker positions the bot didn't open; they're
        handled later in the poll, after the risk check, so a failure there can't block it."""
        live = {p["symbol"]: p for p in broker_positions}
        mine = self.state["positions"]
        for sym in list(mine):
            if sym in live:
                continue
            p = mine[sym]
            if p.get("status") == "pending":
                state = self._entry_state(p)
                if state == "pending":
                    continue
                if state == "never_filled":
                    mine.pop(sym)
                    self.state["trades_today"] = max(self.state["trades_today"] - 1, 0)
                    self.event("entry_not_filled", symbol=sym, client_order_id=p.get("client_order_id"))
                    continue
            closed = mine.pop(sym)
            self.state["closed_today"].append({"symbol": sym, **closed})
            self.event("exit_detected", symbol=sym, detail="position no longer at broker (stop/target filled or closed)",
                       entry=closed.get("entry"), qty=closed.get("qty"))
        unknown = []
        for sym, p in live.items():
            qty = int(float(p["qty"]))
            entry = float(p["avg_entry_price"])
            if sym not in mine:
                unknown.append(sym)
                continue
            if mine[sym]["qty"] != qty or mine[sym].get("status") in ("pending", "unknown"):
                if mine[sym]["qty"] != qty and mine[sym].get("status") != "unknown":
                    self.event("reconcile_qty_mismatch", symbol=sym, ours=mine[sym]["qty"], broker=qty)
                mine[sym].update(qty=qty, entry=entry, status="open" if mine[sym].get("status") != "pending_exit"
                                 else "pending_exit")
                mine[sym].pop("missing_polls", None)
        self.save()
        return unknown

    def _entry_state(self, pos: dict) -> str:
        """pending | never_filled | gone, for an entry order we sent but don't see as a position."""
        try:
            order = self.broker.order_by_client_id(pos["client_order_id"])
        except ApiError:
            return "pending"
        if not order:
            return "never_filled"   # the broker never accepted it
        status = order.get("status")
        filled_qty = float(order.get("filled_qty") or 0)
        if status in ("canceled", "expired", "rejected") and filled_qty == 0:
            return "never_filled"
        if status == "filled":
            legs = order.get("legs") or []
            if any(l.get("status") == "filled" for l in legs):
                return "gone"       # filled, then a stop or target closed it
            # Filled but not in this positions snapshot yet: give it a couple of polls.
            pos["missing_polls"] = pos.get("missing_polls", 0) + 1
            return "gone" if pos["missing_polls"] > 2 else "pending"
        return "pending"

    def _close_unknown(self, unknown: list[str]):
        for sym in unknown:
            self.event("reconcile_unknown_position", symbol=sym)
            if not self.cfg.paper.close_unknown_positions:
                continue
            try:
                self._cancel_symbol_orders(sym)
                self.broker.close_position(sym)
                self.event("order_submitted", symbol=sym, action="close unknown position")
            except ApiError as exc:
                self.event("error", where=f"close unknown {sym}", error=str(exc))

    # ---- the loop -----------------------------------------------------------
    def step(self) -> str:
        if (self.state_dir / KILL_FILE).exists():
            self._flatten("emergency stop file found")
            self.risk.state.locked, self.risk.state.status = True, "FAILED"
            self.risk.state.reason = "emergency stop"
            self.event("risk_decision", action=FLATTEN_LOCK, reason="emergency stop")
            self.stop_requested = True
            self.save()
            return "killed"

        clock = self.broker.clock()
        now = self._broker_now(clock)
        if not clock.get("is_open"):
            note = "market closed"
            if not self._closed_alerted:
                self._closed_alerted = True
                try:
                    left = self.broker.positions()
                except ApiError:
                    left = []
                if left:
                    note = "market closed WITH OPEN POSITIONS"
                    self.event("overnight_positions", severity="critical",
                               positions={p["symbol"]: p["qty"] for p in left},
                               message="positions are open while the market is closed; check them in Alpaca")
            self._write_status(None, note)
            return "closed"
        self._closed_alerted = False

        session = pd.Timestamp(now).tz_convert(NY).date().isoformat()
        if self.state["session"] != session:
            self.state.update(session=session, trades_today=0, flattened_today=False, closed_today=[])

        acct = self.broker.account()
        equity = float(acct["equity"])
        positions, unknown, healthy = [], [], True
        try:
            positions = self.broker.positions()
            unknown = self.reconcile(positions)
        except ApiError as exc:   # never let this block the risk check below
            healthy = False
            self.event("error", where="reconcile", error=str(exc))
        unrealized = sum(float(p.get("unrealized_pl", 0) or 0) for p in positions)
        balance = equity - unrealized

        if self.risk.state.status == PASSED and not positions and not self.risk.confirm_target(balance):
            self.event("risk_decision", action=OK, reason=f"closed balance {balance:,.2f} fell short of the target "
                                                          "after exit costs; trading resumes")
        decision = self.risk.update(now, balance, equity, target_equity=equity - self._exit_costs(positions))
        if decision.action != self.last_risk_action:
            self.event("risk_decision", action=decision.action, reason=decision.reason, equity=equity,
                       daily_floor=decision.daily_floor, total_floor=decision.total_floor)
            self.last_risk_action = decision.action
        if decision.action == FLATTEN_LOCK:
            if self.state["positions"] or positions:
                self._flatten(decision.reason)
            self._write_status(acct, f"locked: {decision.reason}")
            self.save()
            return "locked"
        if decision.action == TARGET_HIT:
            if self.state["positions"] or positions:
                self._flatten("profit target reached")
            self._write_status(acct, "target reached")
            self.save()
            return "passed"

        if now >= self._session_flatten_time(now, clock):
            ok = True
            if positions or self.state["positions"] or not self.state["flattened_today"]:
                ok = self._flatten("end of session cutoff")   # repeats every poll until the broker is flat
            self.state["flattened_today"] = True
            self.save()
            self._write_status(acct, "after cutoff: flat" if ok and not positions else "after cutoff: NOT FLAT, retrying")
            return "after_cutoff"

        self._close_unknown(unknown)
        for sym, p in list(self.state["positions"].items()):
            if p.get("status") == "pending_exit":
                self._exit(sym, p.get("exit_reason", "retry exit"))
        if not healthy:
            self._write_status(acct, "broker error: no new trades this poll")
            return "broker_error"

        try:
            bars = self._completed_bars(now)
        except ApiError as exc:
            self.event("error", where="market data", error=str(exc))
            self._write_status(acct, "data error")
            return "data_error"

        prices = {s: float(df["close"].iloc[-1]) for s, df in bars.items()}
        for sym, df in bars.items():
            frame = self.strategy.compute(df)
            last = frame.iloc[-1]
            bar_id = frame.index[-1].isoformat()
            if self.state["processed"].get(sym) == bar_id:
                continue
            self.state["processed"][sym] = bar_id
            self._on_candle(sym, frame.index[-1], last, equity, prices, decision.action, acct)
        if self.cfg.risk.trailing.enabled:
            self._update_trailing(bars)
        self._log_order_updates(now)
        self._write_status(acct, "trading" if decision.action == OK else decision.action)
        self.save()
        return "ok"

    def _on_candle(self, sym, ts, last, equity, prices, risk_action, acct):
        st = self.cfg.strategy
        held = self.state["positions"].get(sym)
        cross, signal = int(last["cross"]), last["signal"]
        if cross:
            self.event("signal", symbol=sym, candle=ts.isoformat(), cross=cross, signal=signal,
                       close=float(last["close"]))
        if held and st.exit_on_opposite and cross == (-1 if held["qty"] > 0 else 1):
            if not self._exit(sym, "opposite signal"):
                return
            held = None
        if signal not in (BUY, SELL) or held:
            return
        direction = 1 if signal == BUY else -1
        if direction < 0 and (not st.allow_short or sym not in self.shortable):
            self.event("entry_skipped", symbol=sym, reason="shorting disabled or not available")
            return
        if risk_action != OK or not self.risk.can_open():
            self.event("entry_skipped", symbol=sym, reason=f"risk manager: {risk_action}")
            return
        price = float(last["close"])
        if st.filters.liquidity["enabled"]:
            q = self.broker.latest_quote(sym)
            if not q or not q.get("bid") or not q.get("ask"):
                self.event("entry_skipped", symbol=sym, reason="no quote for the spread check")
                return
            mid = (q["bid"] + q["ask"]) / 2
            spread = (q["ask"] - q["bid"]) / mid * 10_000
            if spread > st.filters.liquidity["max_spread_bps"]:
                self.event("entry_skipped", symbol=sym, reason=f"spread {spread:.1f} bps too wide")
                return
            price = q["ask"] if direction > 0 else q["bid"]
        stop_dist = float(stop_distance(price, float(last["atr"]), self.cfg.risk.stop))
        inst = self.cfg.instrument(sym)
        sizing = size_position(sym, direction, price, stop_dist, equity, self._open_positions(prices), self.cfg.risk,
                               inst.group, inst.leverage, self.risk.risk_room(equity), self.state["trades_today"],
                               self.costs.per_share_estimate(sym, price), float(acct.get("buying_power", math.inf)))
        if sizing.qty < 1:
            self.event("entry_skipped", symbol=sym, reason=sizing.reason)
            return
        self._enter(sym, ts, direction, sizing.qty, price, stop_dist, sizing.details.get("reserve_per_share", 0.0))

    def _enter(self, sym, ts, direction, qty, price, stop_dist, reserve=0.0):
        rr = self.cfg.risk.reward_risk
        cid = f"pp-{sym}-{pd.Timestamp(ts).tz_convert(NY):%Y%m%d%H%M}-{'L' if direction > 0 else 'S'}"
        stop = round(price - direction * stop_dist, 2)
        target = round(price + direction * stop_dist * rr, 2) if rr else None
        # GTC legs: if the bot dies before flattening, the stop still protects the position at the
        # next open (it can't stop a gap through it, but it caps losses after that).
        order = {"symbol": sym, "qty": str(qty), "side": "buy" if direction > 0 else "sell", "type": "market",
                 "time_in_force": "gtc", "client_order_id": cid,
                 "order_class": "bracket" if target else "oto", "stop_loss": {"stop_price": stop}}
        if target:
            order["take_profit"] = {"limit_price": target}
        # Record intent before sending, so a crash right after submission can't lead to a second order.
        self.state["positions"][sym] = {"qty": direction * qty, "entry": price, "stop": stop, "target": target,
                                        "client_order_id": cid, "status": "pending", "risk": qty * stop_dist,
                                        "reserve": reserve, "opened": (pd.Timestamp(ts) + self.bar_len).isoformat()}
        self.state["trades_today"] += 1
        self.save()
        try:
            resp = self.broker.submit_order(**order)
            self.event("order_submitted", symbol=sym, client_order_id=cid, qty=qty, side=order["side"], stop=stop,
                       target=target, order_id=(resp or {}).get("id"))
        except ApiError as exc:
            if exc.status == 422 and "client_order_id" in exc.message:
                self.event("duplicate_order_prevented", symbol=sym, client_order_id=cid)
                return
            if exc.status is None or exc.status >= 500:
                # The order may or may not have reached the broker: keep the record, reconcile decides.
                self.event("order_status_unknown", symbol=sym, client_order_id=cid, error=str(exc))
                return
            self.state["positions"].pop(sym, None)
            self.state["trades_today"] -= 1
            self.event("order_rejected", symbol=sym, client_order_id=cid, error=str(exc))
            self.save()

    def _update_trailing(self, bars: dict[str, pd.DataFrame]):
        """Same rule as the backtest: once a trade is up `activate_r` x its initial risk, move the
        stop leg to `atr_mult` x ATR behind the best price since entry. Stops only ever tighten."""
        tr = self.cfg.risk.trailing
        open_orders = None
        for sym, p in self.state["positions"].items():
            if p.get("status") != "open" or sym not in bars or not p.get("stop"):
                continue
            df = self.strategy.compute(bars[sym])
            since = df[df.index >= pd.Timestamp(p["opened"])]   # candles that started after the entry
            long = p["qty"] > 0
            best = max([p["entry"], *since["high"]]) if long else min([p["entry"], *since["low"]])
            dist = abs(p["risk"] / p["qty"]) if p["qty"] else 0
            if not dist or (best - p["entry"]) * (1 if long else -1) < tr.activate_r * dist:
                continue
            new_stop = round(best - (1 if long else -1) * tr.atr_mult * float(df["atr"].iloc[-1]), 2)
            last = float(df["close"].iloc[-1])
            if (long and (new_stop <= p["stop"] or new_stop >= last)) or \
                    (not long and (new_stop >= p["stop"] or new_stop <= last)):
                continue
            if open_orders is None:
                open_orders = self._open_orders()
            leg = next((o for o in open_orders if o.get("symbol") == sym and o.get("type") in ("stop", "stop_limit")), None)
            if leg is None:
                self.event("warning", message=f"trailing stop for {sym}: no open stop leg found")
                continue
            try:
                self.broker.replace_order(leg["id"], stop_price=new_stop)
                self.event("trailing_stop_moved", symbol=sym, old=p["stop"], new=new_stop)
                p["stop"] = new_stop
            except ApiError as exc:
                self.event("error", where=f"trailing stop {sym}", error=str(exc))

    def _exit(self, sym, reason) -> bool:
        """Cancel the symbol's bracket legs, then close it. If the broker refuses, keep the
        position marked pending_exit and retry next poll."""
        try:
            self._cancel_symbol_orders(sym)
            self.broker.close_position(sym)
            self.event("exit_submitted", symbol=sym, reason=reason)
        except ApiError as exc:
            self.event("error", where=f"exit {sym}", error=str(exc))
            p = self.state["positions"].get(sym)
            if p is not None:
                p.update(status="pending_exit", exit_reason=reason)
                self.save()
            return False
        closed = self.state["positions"].pop(sym, None)
        if closed:
            self.state["closed_today"].append({"symbol": sym, **closed, "exit_reason": reason})
        self.save()
        return True

    def _log_order_updates(self, now):
        after = (pd.Timestamp(now) - pd.Timedelta(days=1)).isoformat()
        try:
            orders = self.broker.orders(status="closed", after=after)
        except ApiError as exc:
            self.event("error", where="order updates", error=str(exc))
            return
        seen = set(self.state["seen_orders"])
        for leg in flat_orders(orders):
            if leg["id"] in seen or leg.get("status") not in FINAL_ORDER_STATUSES:
                continue
            seen.add(leg["id"])
            kind = "fill" if leg["status"] == "filled" else f"order_{leg['status']}"
            self.event(kind, symbol=leg.get("symbol"), side=leg.get("side"), qty=leg.get("filled_qty"),
                       price=leg.get("filled_avg_price"), client_order_id=leg.get("client_order_id"),
                       order_type=leg.get("type"))
        self.state["seen_orders"] = sorted(seen)[-2000:]

    def _write_status(self, acct, note: str):
        r = self.risk
        status = {"time": self.local_now().isoformat(timespec="seconds"), "note": note,
                  "positions": self.state["positions"], "trades_today": self.state["trades_today"],
                  "closed_today": len(self.state["closed_today"])}
        if acct and r and r.state.day_key is not None:
            equity = float(acct["equity"])
            s = r.state
            u = r.usage(equity)
            status.update(
                equity=equity, initial=s.initial, pnl_total=equity - s.initial,
                pnl_today=equity - r.day_start_reference(),
                daily_loss_used_pct=u["daily_used_pct_of_limit"] * self.cfg.challenge.max_daily_loss_pct / 100,
                daily_limit_pct=self.cfg.challenge.max_daily_loss_pct,
                daily_limit_used_pct_of_limit=u["daily_used_pct_of_limit"],
                distance_to_daily_floor=u["distance_to_daily_floor"],
                distance_to_total_floor=u["distance_to_total_floor"],
                overall_drawdown_pct=u["total_used_pct_of_limit"] * self.cfg.challenge.max_total_loss_pct / 100,
                total_limit_used_pct_of_limit=u["total_used_pct_of_limit"],
                drawdown_type=self.cfg.challenge.drawdown_type,
                target=r.target(), challenge_status=s.status, locked=s.locked, reason=s.reason,
                open_exposure=sum(abs(float(p["qty"])) * float(p["entry"]) for p in self.state["positions"].values()))
        (self.state_dir / "status.json").write_text(json.dumps(status, indent=2, default=str))
        if acct:
            log.info("equity %s | today %+.2f | positions %d | trades today %d | %s", status.get("equity"),
                     status.get("pnl_today", 0), len(self.state["positions"]), self.state["trades_today"], note)

    def run(self):
        pid_path = self.state_dir / PID_FILE
        pid_path.write_text(str(os.getpid()))
        self.startup()

        def _stop(signum, frame):
            self.event("shutdown_requested", signal=signum)
            self.stop_requested = True
            self._wake.set()   # cut the sleep short so shutdown() runs before a process manager kills us
        for name in ("SIGINT", "SIGTERM", "SIGHUP"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), _stop)

        try:
            while not self.stop_requested:
                try:
                    result = self.step()
                except ApiError as exc:
                    self.event("error", where="loop", error=str(exc))
                    result = "error"
                except Exception as exc:  # never die silently with positions open
                    self.event("error", where="loop", error=repr(exc))
                    result = "error"
                if self.stop_requested:
                    break
                self._wake.wait(self.cfg.paper.poll_seconds if result != "closed" else 60)
        finally:
            self.shutdown()
            pid_path.unlink(missing_ok=True)

    def shutdown(self):
        try:
            open_at_broker = bool(self.broker.positions())
        except ApiError:
            open_at_broker = bool(self.state["positions"])
        if self.cfg.paper.flatten_on_exit and (self.state["positions"] or open_at_broker):
            try:
                self._flatten("shutdown")
            except ApiError:
                self.event("error", where="shutdown", error="could not flatten; check the broker manually")
        self.event("shutdown", positions=list(self.state["positions"]))
        self.save()


def bot_running(cfg: BotConfig) -> bool:
    """True if a paper bot process from this state directory is alive."""
    path = Path(cfg.paper.state_dir) / PID_FILE
    if not path.exists():
        return False
    try:
        os.kill(int(path.read_text().strip()), 0)
    except (ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True
    return True


def emergency_stop(cfg: BotConfig, broker=None) -> None:
    """Create the kill file (the running bot flattens and locks on its next poll) and, if
    credentials are available, cancel every order and close every position right now."""
    state = Path(cfg.paper.state_dir)
    state.mkdir(parents=True, exist_ok=True)
    (state / KILL_FILE).write_text(datetime.now(timezone.utc).isoformat())
    if broker is not None:
        broker.cancel_all()
        broker.close_all()
