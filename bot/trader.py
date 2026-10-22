"""Paper-trading loop for the Prop Pass strategy (Alpaca paper account only).

Each poll:
  1. emergency stop file?  -> cancel everything, close everything, lock
  2. market closed?        -> wait
  3. reconcile our positions with the broker's (the broker is the source of truth)
  4. risk manager check    -> flatten + lock on a breach, stop at the target
  5. past the cutoff?      -> flatten once, no new entries
  6. new completed candle  -> exits on opposite signals, then entries (bracket orders with a
                              deterministic client_order_id, so a restart can't double-submit)
  7. log new fills / rejections, write status.json
"""
import json
import logging
import math
import signal
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from bot.config import BotConfig
from bot.costs import CostModel
from bot.data import TF_MINUTES
from bot.http import ApiError
from bot.risk import FLATTEN_LOCK, OK, TARGET_HIT, OpenPosition, RiskManager, size_position
from bot.strategy import BUY, SELL, EmaCross, stop_distance

log = logging.getLogger("propbot")
NY = "America/New_York"
KILL_FILE = "KILL"


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


class Trader:
    def __init__(self, cfg: BotConfig, broker, now=None):
        self.cfg, self.broker = cfg, broker
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.state_dir = Path(cfg.paper.state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.state_dir / "trader_state.json"
        self.event = EventLog(cfg.paper.log_dir)
        self.costs = CostModel(cfg.costs, cfg.spread_bps)
        self.bar_minutes = TF_MINUTES[cfg.strategy.timeframe]
        self.strategy = EmaCross(cfg.strategy, cfg.risk.stop, self.bar_minutes, cfg.paper.data_feed)
        self.stop_requested = False
        self.shortable: set[str] = set()
        self.tradable: list[str] = []
        self.last_risk_action = None
        self._load_state()

    # ---- state --------------------------------------------------------------
    def _load_state(self):
        if self.state_path.exists():
            s = json.loads(self.state_path.read_text())
            self.state = s
            self.risk = RiskManager.from_dict(self.cfg.challenge, s["risk"])
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

    # ---- helpers ------------------------------------------------------------
    def _flatten(self, reason: str):
        self.event("flatten", reason=reason, positions=list(self.state["positions"]))
        try:
            self.broker.cancel_all()
            self.broker.close_all()
        except ApiError as exc:
            self.event("error", where="flatten", error=str(exc))
            raise
        self.state["positions"] = {}
        self.save()

    def _session_flatten_time(self, now: datetime, clock: dict) -> datetime:
        local = pd.Timestamp(now).tz_convert(NY)
        hh, mm = map(int, self.cfg.strategy.flatten_at.split(":"))
        configured = local.normalize() + pd.Timedelta(hours=hh, minutes=mm)
        next_close = pd.Timestamp(clock["next_close"]).tz_convert(NY)
        return min(configured, next_close - pd.Timedelta(minutes=10)).to_pydatetime()

    def _completed_bars(self, now: datetime) -> dict[str, pd.DataFrame]:
        start = now - timedelta(days=7)
        raw = self.broker.bars(self.tradable, start, self.cfg.strategy.timeframe)
        out = {}
        bar_len = pd.Timedelta(minutes=self.bar_minutes)
        for sym, df in raw.items():
            if df is None or df.empty:
                self.event("missing_data", symbol=sym)
                continue
            df = df[df.index + bar_len <= pd.Timestamp(now)]
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
                                    inst.group, inst.leverage))
        return out

    # ---- reconciliation -----------------------------------------------------
    def reconcile(self, broker_positions: list[dict]):
        live = {p["symbol"]: p for p in broker_positions}
        mine = self.state["positions"]
        for sym in list(mine):
            if sym not in live:
                if mine[sym].get("status") == "pending" and not self._entry_finished(mine[sym]):
                    continue  # entry not filled yet
                closed = mine.pop(sym)
                self.state["closed_today"].append({"symbol": sym, **closed})
                self.event("exit_detected", symbol=sym, detail="position no longer at broker (stop/target filled or closed)",
                           entry=closed.get("entry"), qty=closed.get("qty"))
        for sym, p in live.items():
            qty = int(float(p["qty"]))
            entry = float(p["avg_entry_price"])
            if sym not in mine:
                self.event("reconcile_unknown_position", symbol=sym, qty=qty)
                if self.cfg.paper.close_unknown_positions:
                    self.broker.close_position(sym)
                    self.event("order_submitted", symbol=sym, action="close unknown position")
                continue
            if mine[sym]["qty"] != qty or mine[sym].get("status") == "pending":
                if mine[sym]["qty"] != qty:
                    self.event("reconcile_qty_mismatch", symbol=sym, ours=mine[sym]["qty"], broker=qty)
                mine[sym].update(qty=qty, entry=entry, status="open")
        self.save()

    def _entry_finished(self, pos: dict) -> bool:
        try:
            order = self.broker.order_by_client_id(pos["client_order_id"])
        except ApiError:
            return False
        return bool(order) and order.get("status") in ("filled", "canceled", "expired", "rejected")

    # ---- the loop -----------------------------------------------------------
    def step(self) -> str:
        now = self.now()
        if (self.state_dir / KILL_FILE).exists():
            self._flatten("emergency stop file found")
            self.risk.state.locked, self.risk.state.status = True, "FAILED"
            self.risk.state.reason = "emergency stop"
            self.event("risk_decision", action=FLATTEN_LOCK, reason="emergency stop")
            self.stop_requested = True
            self.save()
            return "killed"

        clock = self.broker.clock()
        if not clock.get("is_open"):
            self._write_status(None, "market closed")
            return "closed"

        session = pd.Timestamp(now).tz_convert(NY).date().isoformat()
        if self.state["session"] != session:
            self.state.update(session=session, trades_today=0, flattened_today=False, closed_today=[])

        acct = self.broker.account()
        positions = self.broker.positions()
        self.reconcile(positions)
        equity = float(acct["equity"])
        unrealized = sum(float(p.get("unrealized_pl", 0) or 0) for p in positions)
        balance = equity - unrealized

        decision = self.risk.update(now, balance, equity)
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
        if decision.action == TARGET_HIT and self.cfg.challenge.stop_at_target:
            if self.state["positions"] or positions:
                self._flatten("profit target reached")
            self._write_status(acct, "target reached")
            self.save()
            return "passed"

        if now >= self._session_flatten_time(now, clock):
            if not self.state["flattened_today"]:
                self._flatten("end of session cutoff")
                self.state["flattened_today"] = True
                self.save()
            self._write_status(acct, "after cutoff: flat")
            return "after_cutoff"

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
            self._exit(sym, "opposite signal")
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
        self._enter(sym, ts, direction, sizing.qty, price, stop_dist)

    def _enter(self, sym, ts, direction, qty, price, stop_dist):
        rr = self.cfg.risk.reward_risk
        cid = f"pp-{sym}-{pd.Timestamp(ts).tz_convert(NY):%Y%m%d%H%M}-{'L' if direction > 0 else 'S'}"
        stop = round(price - direction * stop_dist, 2)
        target = round(price + direction * stop_dist * rr, 2) if rr else None
        order = {"symbol": sym, "qty": str(qty), "side": "buy" if direction > 0 else "sell", "type": "market",
                 "time_in_force": "day", "client_order_id": cid,
                 "order_class": "bracket" if target else "oto", "stop_loss": {"stop_price": stop}}
        if target:
            order["take_profit"] = {"limit_price": target}
        # Record intent before sending, so a crash right after submission can't lead to a second order.
        self.state["positions"][sym] = {"qty": direction * qty, "entry": price, "stop": stop, "target": target,
                                        "client_order_id": cid, "status": "pending",
                                        "risk": qty * stop_dist, "opened": pd.Timestamp(ts).isoformat()}
        self.state["trades_today"] += 1
        self.save()
        try:
            resp = self.broker.submit_order(**order)
            self.event("order_submitted", symbol=sym, client_order_id=cid, qty=qty, side=order["side"], stop=stop,
                       target=target, order_id=resp.get("id"))
        except ApiError as exc:
            if exc.status == 422 and "client_order_id" in exc.message:
                self.event("duplicate_order_prevented", symbol=sym, client_order_id=cid)
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
            since = df[df.index >= pd.Timestamp(p["opened"])]
            if since.empty:
                continue
            long = p["qty"] > 0
            best = since["high"].max() if long else since["low"].min()
            dist = abs(p["risk"] / p["qty"]) if p["qty"] else 0
            if not dist or (best - p["entry"]) * (1 if long else -1) < tr.activate_r * dist:
                continue
            new_stop = round(best - (1 if long else -1) * tr.atr_mult * float(df["atr"].iloc[-1]), 2)
            if (long and new_stop <= p["stop"]) or (not long and new_stop >= p["stop"]):
                continue
            if open_orders is None:
                open_orders = self.broker.orders(status="open")
            leg = next((o for o in open_orders if o.get("symbol") == sym and o.get("type") in ("stop", "stop_limit")), None)
            if leg is None:
                continue
            try:
                self.broker.replace_order(leg["id"], stop_price=new_stop)
                self.event("trailing_stop_moved", symbol=sym, old=p["stop"], new=new_stop)
                p["stop"] = new_stop
            except ApiError as exc:
                self.event("error", where=f"trailing stop {sym}", error=str(exc))

    def _exit(self, sym, reason):
        try:
            for o in self.broker.orders(status="open"):
                if o.get("symbol") == sym:
                    self.broker.cancel_order(o["id"])
            self.broker.close_position(sym)
            self.event("exit_submitted", symbol=sym, reason=reason)
        except ApiError as exc:
            self.event("error", where=f"exit {sym}", error=str(exc))
            return
        closed = self.state["positions"].pop(sym, None)
        if closed:
            self.state["closed_today"].append({"symbol": sym, **closed, "exit_reason": reason})
        self.save()

    def _log_order_updates(self, now):
        after = (pd.Timestamp(now) - pd.Timedelta(days=1)).isoformat()
        try:
            orders = self.broker.orders(status="closed", after=after)
        except ApiError as exc:
            self.event("error", where="order updates", error=str(exc))
            return
        seen = set(self.state["seen_orders"])
        for o in orders:
            for leg in [o] + list(o.get("legs") or []):
                if leg["id"] in seen or leg.get("status") not in ("filled", "canceled", "rejected", "expired"):
                    continue
                seen.add(leg["id"])
                kind = "fill" if leg["status"] == "filled" else f"order_{leg['status']}"
                self.event(kind, symbol=leg.get("symbol"), side=leg.get("side"), qty=leg.get("filled_qty"),
                           price=leg.get("filled_avg_price"), client_order_id=leg.get("client_order_id"),
                           order_type=leg.get("type"))
        self.state["seen_orders"] = sorted(seen)[-2000:]

    def _write_status(self, acct, note: str):
        r = self.risk
        status = {"time": self.now().isoformat(timespec="seconds"), "note": note,
                  "positions": self.state["positions"], "trades_today": self.state["trades_today"],
                  "closed_today": len(self.state["closed_today"])}
        if acct and r:
            equity = float(acct["equity"])
            s = r.state
            day_start = max(s.day_start_balance or equity, s.day_start_equity or equity) \
                if self.cfg.challenge.day_start_reference == "higher" else (s.day_start_equity or equity)
            status.update(
                equity=equity, initial=s.initial, pnl_total=equity - s.initial,
                pnl_today=equity - (s.day_start_equity or equity),
                daily_loss_used_pct=max(day_start - equity, 0) / s.initial * 100,
                daily_limit_pct=self.cfg.challenge.max_daily_loss_pct,
                distance_to_daily_floor=equity - r.daily_floor(),
                distance_to_total_floor=equity - r.total_floor(),
                overall_drawdown_pct=max(s.initial - equity, 0) / s.initial * 100,
                target=r.target(), challenge_status=s.status, locked=s.locked, reason=s.reason,
                open_exposure=sum(abs(float(p["qty"])) * float(p["entry"]) for p in self.state["positions"].values()))
        (self.state_dir / "status.json").write_text(json.dumps(status, indent=2, default=str))
        if acct:
            log.info("equity %s | today %+.2f | positions %d | trades today %d | %s", status.get("equity"),
                     status.get("pnl_today", 0), len(self.state["positions"]), self.state["trades_today"], note)

    def run(self):
        self.startup()

        def _stop(signum, frame):
            self.event("shutdown_requested", signal=signum)
            self.stop_requested = True
        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)

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
            time.sleep(self.cfg.paper.poll_seconds if result != "closed" else 60)
        self.shutdown()

    def shutdown(self):
        if self.cfg.paper.flatten_on_exit and self.state["positions"]:
            try:
                self._flatten("shutdown")
            except ApiError:
                self.event("error", where="shutdown", error="could not flatten; check the broker manually")
        self.event("shutdown", positions=list(self.state["positions"]))
        self.save()


def emergency_stop(cfg: BotConfig, broker=None) -> None:
    """Create the kill file (the running bot flattens and locks on its next poll) and, if
    credentials are available, cancel every order and close every position right now."""
    state = Path(cfg.paper.state_dir)
    state.mkdir(parents=True, exist_ok=True)
    (state / KILL_FILE).write_text(datetime.now(timezone.utc).isoformat())
    if broker is not None:
        broker.cancel_all()
        broker.close_all()
