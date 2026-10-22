"""Risk management, independent of the strategy.

RiskManager enforces the prop challenge rules (daily loss, overall drawdown, profit target,
safety buffer, lock-out). size_position turns a signal into a share count, or a reason why not.
Both are used by the backtester and the paper trader, so the same rules apply everywhere."""
import math
from dataclasses import asdict, dataclass, field
from datetime import timedelta

import pandas as pd

from bot.config import ChallengeSettings, RiskSettings

NY = "America/New_York"
ACTIVE, PASSED, FAILED = "ACTIVE", "PASSED", "FAILED"
OK, NO_NEW_ENTRIES, FLATTEN_LOCK, TARGET_HIT = "ok", "no_new_entries", "flatten_and_lock", "target_hit"


@dataclass
class RiskDecision:
    action: str
    reason: str = ""
    daily_floor: float = 0.0
    total_floor: float = 0.0


@dataclass
class RiskState:
    initial: float
    day_key: str | None = None
    day_start_balance: float | None = None
    day_start_equity: float | None = None
    high_water: float | None = None
    status: str = ACTIVE
    reason: str = ""
    locked: bool = False
    buffered: bool = False


class RiskManager:
    def __init__(self, rules: ChallengeSettings, initial_balance: float, state: RiskState | None = None):
        self.rules = rules
        self.state = state or RiskState(initial=initial_balance, high_water=initial_balance)
        hh, mm = rules.daily_reset.split(":")
        self._reset_offset = timedelta(hours=int(hh), minutes=int(mm))

    # ---- day handling -------------------------------------------------------
    def day_key(self, ts) -> str:
        ts = pd.Timestamp(ts)
        local = ts.tz_convert(NY) if ts.tzinfo else ts
        return (local - self._reset_offset).date().isoformat()

    def _roll_day(self, ts, balance: float, equity: float) -> None:
        key = self.day_key(ts)
        if key != self.state.day_key:
            self.state.day_key = key
            self.state.day_start_balance, self.state.day_start_equity = balance, equity

    # ---- limits -------------------------------------------------------------
    def daily_floor(self) -> float:
        s, r = self.state, self.rules
        ref = {"balance": s.day_start_balance, "equity": s.day_start_equity,
               "higher": max(s.day_start_balance, s.day_start_equity)}[r.day_start_reference]
        base = s.initial if r.daily_loss_base == "initial" else ref
        return ref - base * r.max_daily_loss_pct / 100

    def total_floor(self) -> float:
        s, r = self.state, self.rules
        distance = s.initial * r.max_total_loss_pct / 100
        if r.drawdown_type == "static":
            return s.initial - distance
        floor = s.high_water - distance
        return min(floor, s.initial) if r.trailing_lock_at_initial else floor

    def target(self) -> float:
        return self.state.initial * (1 + self.rules.profit_target_pct / 100)

    def risk_room(self, equity: float) -> float:
        """Money that can still be lost before hitting the safety buffer of the nearest limit."""
        buffer = self.state.initial * self.rules.safety_buffer_pct / 100
        return min(equity - self.daily_floor(), equity - self.total_floor()) - buffer

    def can_open(self) -> bool:
        s = self.state
        return s.status == ACTIVE and not s.locked and not s.buffered

    # ---- the check ----------------------------------------------------------
    def update(self, ts, balance: float, equity: float, worst_equity: float | None = None,
               best_equity: float | None = None, target_equity: float | None = None) -> RiskDecision:
        """Call on every candle / poll.

        balance        closed P&L only
        equity         including open positions (marked), after costs paid so far
        worst_equity   lowest equity since the last call (e.g. at the candle's low); breaches are
                       judged on it, like a firm checking every tick
        best_equity    highest equity since the last call; raises the trailing high-water mark
        target_equity  what the account would be worth if closed now (after exit spread, slippage
                       and fees); the profit target is judged on it, so a pass survives the exit
        """
        s = self.state
        self._roll_day(ts, balance, equity)
        worst = equity if worst_equity is None else min(worst_equity, equity)
        daily, total = self.daily_floor(), self.total_floor()
        if s.locked or s.status == FAILED:
            return RiskDecision(FLATTEN_LOCK, s.reason, daily, total)
        if s.status == PASSED:
            return RiskDecision(TARGET_HIT, s.reason, daily, total)
        if worst <= total:
            return self._fail(f"max total loss hit (equity {worst:,.2f} <= floor {total:,.2f})", daily, total)
        if worst <= daily:
            return self._fail(f"daily loss limit hit (equity {worst:,.2f} <= floor {daily:,.2f})", daily, total)

        if self.rules.trailing_basis == "equity":
            peak = max(equity, best_equity if best_equity is not None else equity)
        else:
            peak = balance
        s.high_water = max(s.high_water or s.initial, peak)

        exit_value = equity if target_equity is None else min(target_equity, equity)
        if exit_value >= self.target() and self.rules.stop_at_target:
            s.status, s.reason = PASSED, f"profit target reached (equity after exit costs {exit_value:,.2f})"
            return RiskDecision(TARGET_HIT, s.reason, daily, total)
        # With stop_at_target false the target is only informational: keep trading normally.

        s.buffered = self.risk_room(equity) <= 0
        if s.buffered:
            return RiskDecision(NO_NEW_ENTRIES, "inside the safety buffer of a loss limit", daily, total)
        return RiskDecision(OK, "", daily, total)

    def confirm_target(self, balance: float) -> bool:
        """After flattening on a pass, check the closed balance really made the target. If exit
        fills slipped it below, go back to ACTIVE instead of staying stuck in PASSED."""
        s = self.state
        if s.status == PASSED and balance < self.target():
            s.status, s.reason = ACTIVE, ""
            return False
        return s.status == PASSED

    # ---- display helpers (status file / dashboard) ---------------------------
    def day_start_reference(self) -> float:
        s = self.state
        return {"balance": s.day_start_balance, "equity": s.day_start_equity,
                "higher": max(s.day_start_balance, s.day_start_equity)}[self.rules.day_start_reference]

    def usage(self, equity: float) -> dict:
        """How much of each limit is used, as % of that limit (100 = breached)."""
        daily_room = self.day_start_reference() - self.daily_floor()
        total_room = self.state.initial * self.rules.max_total_loss_pct / 100
        return {"daily_used_pct_of_limit": max(self.day_start_reference() - equity, 0) / daily_room * 100,
                "total_used_pct_of_limit": max(total_room - (equity - self.total_floor()), 0) / total_room * 100,
                "distance_to_daily_floor": equity - self.daily_floor(),
                "distance_to_total_floor": equity - self.total_floor()}

    def _fail(self, reason: str, daily: float, total: float) -> RiskDecision:
        self.state.status, self.state.reason, self.state.locked = FAILED, reason, True
        return RiskDecision(FLATTEN_LOCK, reason, daily, total)

    def reset(self, new_initial: float | None = None) -> None:
        """Manual reset after a breach or a pass: starts a fresh challenge."""
        initial = new_initial if new_initial is not None else self.state.initial
        self.state = RiskState(initial=initial, high_water=initial)

    def to_dict(self) -> dict:
        return asdict(self.state)

    @classmethod
    def from_dict(cls, rules: ChallengeSettings, data: dict) -> "RiskManager":
        return cls(rules, data["initial"], RiskState(**data))


# ---------------------------------------------------------------------------
# Position sizing
# ---------------------------------------------------------------------------
@dataclass
class OpenPosition:
    symbol: str
    qty: int              # negative = short
    entry: float
    stop: float | None
    price: float          # latest price
    group: str = "other"
    leverage: float = 1.0
    reserve_per_share: float = 0.0   # gap buffer + exit cost set aside at entry, beyond the stop


@dataclass
class SizingResult:
    qty: int
    reason: str = ""
    risk_dollars: float = 0.0
    details: dict = field(default_factory=dict)


def open_risk(positions: list[OpenPosition]) -> float:
    """Money lost if every open position hit its stop from here, including the gap buffer and
    exit costs each one was sized with (unprotected = whole value)."""
    total = 0.0
    for p in positions:
        if p.stop is None:
            total += abs(p.qty) * p.price
        else:
            to_stop = max((p.price - p.stop) if p.qty > 0 else (p.stop - p.price), 0.0)
            total += abs(p.qty) * (to_stop + p.reserve_per_share)
    return total


def size_position(symbol: str, direction: int, entry_price: float, stop_dist: float, equity: float,
                  positions: list[OpenPosition], risk: RiskSettings, group: str, leverage: float,
                  risk_room: float, trades_today: int, cost_per_share: float = 0.0,
                  buying_power: float | None = None) -> SizingResult:
    """Shares to trade so that hitting the stop (plus a slippage/gap buffer and costs) loses
    at most `risk_per_trade_pct` of equity, while respecting every portfolio limit."""
    if trades_today >= risk.max_trades_per_day:
        return SizingResult(0, "daily trade limit reached")
    if any(p.symbol == symbol for p in positions):
        return SizingResult(0, "already have a position in this symbol")
    if len(positions) >= risk.max_positions:
        return SizingResult(0, "max open positions reached")
    eff_dir = direction * (1 if leverage >= 0 else -1)
    same_bet = [p for p in positions if p.group == group and (1 if p.qty > 0 else -1) * (1 if p.leverage >= 0 else -1) == eff_dir]
    if len(same_bet) >= risk.max_per_group:
        return SizingResult(0, f"max {risk.max_per_group} correlated position(s) in group '{group}'")
    if not (stop_dist > 0 and entry_price > 0 and equity > 0):
        return SizingResult(0, "invalid price, stop or equity")

    per_share = stop_dist + entry_price * risk.sizing_buffer_pct / 100 + cost_per_share
    budget = equity * risk.risk_per_trade_pct / 100
    room = risk_room - open_risk(positions)
    if room <= 0:
        return SizingResult(0, "too close to a drawdown limit")
    budget = min(budget, room)
    qty = math.floor(budget / per_share)

    exposure = sum(abs(p.qty) * p.price * abs(p.leverage) for p in positions)
    max_by_exposure = math.floor(max(risk.max_gross_exposure * equity - exposure, 0) / (entry_price * max(abs(leverage), 1e-9)))
    qty = min(qty, max_by_exposure)
    if buying_power is not None:
        qty = min(qty, math.floor(max(buying_power, 0) / entry_price))
    if qty < 1:
        return SizingResult(0, "position would be under 1 share after risk and exposure limits")
    return SizingResult(qty, "", qty * per_share, {"per_share_risk": per_share, "budget": budget,
                                                   "max_by_exposure": max_by_exposure,
                                                   "reserve_per_share": per_share - stop_dist})
