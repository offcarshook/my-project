"""Portfolio and risk rules shared by the live bot and the backtester."""
from dataclasses import dataclass, field

from bot.strategy import BUY, SELL

ACTIVE, PASSED, FAILED = "ACTIVE", "PASSED", "FAILED"


@dataclass
class RiskConfig:
    leverage: float = 1.0          # max gross exposure as a multiple of equity
    risk_pct: float = 0.0          # % of equity lost if the stop is hit; 0 = split evenly
    stop_pct: float = 0.0          # 0 = no stop loss
    take_profit_pct: float = 0.0   # 0 = no take profit
    reward_risk: float = 0.0       # target as a multiple of the stop distance; overrides take_profit_pct
    max_trades_per_day: int = 0    # new entries per day; 0 = no limit
    allow_short: bool = False
    max_positions: int = 0         # 0 = no limit
    flatten_eod: bool = False      # close everything on the last bar of the day
    slippage_pct: float = 0.05


@dataclass
class ChallengeRules:
    enabled: bool = False
    profit_target_pct: float = 8.0
    max_daily_loss_pct: float = 5.0
    max_drawdown_pct: float = 10.0
    stop_on_pass: bool = True


@dataclass
class Position:
    qty: int                       # negative = short
    entry: float
    stop: float | None = None
    target: float | None = None


@dataclass
class Book:
    cash: float
    starting_cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    day: str | None = None
    day_start_equity: float | None = None
    last_equity: float | None = None   # equity after the previous step, i.e. the prior close
    status: str = ACTIVE
    status_reason: str = ""
    last_signal_bar: dict[str, str] = field(default_factory=dict)
    trades_today: int = 0

    @classmethod
    def new(cls, cash: float) -> "Book":
        return cls(cash=cash, starting_cash=cash)

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + sum(p.qty * prices.get(s, p.entry) for s, p in self.positions.items())

    def gross_exposure(self, prices: dict[str, float]) -> float:
        return sum(abs(p.qty) * prices.get(s, p.entry) for s, p in self.positions.items())

    def step(self, ts, day: str, prices: dict[str, float], signals: dict[str, tuple],
             risk: RiskConfig, rules: ChallengeRules, n_symbols: int, eod: bool = False,
             highs: dict[str, float] | None = None, lows: dict[str, float] | None = None) -> list[dict]:
        """Process one bar. `signals` maps symbol -> (signal, bar id[, stop distance]); each
        bar's signal is acted on at most once, so re-running the same bar never double-trades.
        With `highs`/`lows`, stops and targets trigger on the bar's range and fill at their
        level (stop first if both were touched); otherwise they're checked on the close."""
        fills: list[dict] = []
        if day != self.day:
            start = self.last_equity if self.last_equity is not None else self.equity(prices)
            self.day, self.day_start_equity, self.trades_today = day, start, 0
        if self.status == FAILED or (self.status == PASSED and rules.enabled and rules.stop_on_pass):
            return fills

        for sym, pos in list(self.positions.items()):
            price = prices.get(sym)
            if price is None:
                continue
            long = pos.qty > 0
            hi = highs.get(sym, price) if highs else price
            lo = lows.get(sym, price) if lows else price
            if pos.stop is not None and (lo <= pos.stop if long else hi >= pos.stop):
                fills.append(self._close(sym, pos.stop if highs else price, ts, risk, "stop loss"))
            elif pos.target is not None and (hi >= pos.target if long else lo <= pos.target):
                fills.append(self._close(sym, pos.target if highs else price, ts, risk, "take profit"))

        if eod:
            for sym in [s for s in self.positions if s in prices]:
                fills.append(self._close(sym, prices[sym], ts, risk, "end of day"))
        else:
            for sym, sig in signals.items():
                signal, bar = sig[0], sig[1]
                stop_dist = sig[2] if len(sig) > 2 and sig[2] == sig[2] else None  # NaN -> None
                price = prices.get(sym)
                if price is None or signal not in (BUY, SELL) or self.last_signal_bar.get(sym) == bar:
                    continue
                self.last_signal_bar[sym] = bar
                direction = 1 if signal == BUY else -1
                pos = self.positions.get(sym)
                if pos and (pos.qty > 0) == (direction > 0):
                    continue
                if pos:
                    fills.append(self._close(sym, price, ts, risk, "signal"))
                if direction > 0 or risk.allow_short:
                    fill = self._open(sym, direction, price, ts, risk, rules, prices, n_symbols, stop_dist)
                    if fill:
                        fills.append(fill)

        if rules.enabled and self.status == ACTIVE:
            fills += self._check_rules(ts, prices, risk, rules)
        self.last_equity = self.equity(prices)
        return fills

    def flatten(self, prices, ts, risk, reason) -> list[dict]:
        return [self._close(s, prices.get(s, p.entry), ts, risk, reason)
                for s, p in list(self.positions.items())]

    def _check_rules(self, ts, prices, risk, rules) -> list[dict]:
        eq = self.equity(prices)
        if eq <= self.starting_cash * (1 - rules.max_drawdown_pct / 100):
            self.status, self.status_reason = FAILED, "max drawdown hit"
        elif eq <= self.day_start_equity * (1 - rules.max_daily_loss_pct / 100):
            self.status, self.status_reason = FAILED, "daily loss limit hit"
        elif eq >= self.starting_cash * (1 + rules.profit_target_pct / 100):
            self.status, self.status_reason = PASSED, "profit target hit"
            return self.flatten(prices, ts, risk, "target reached") if rules.stop_on_pass else []
        else:
            return []
        return self.flatten(prices, ts, risk, self.status_reason) if self.status == FAILED else []

    def _open(self, sym, direction, price, ts, risk, rules, prices, n_symbols, stop_dist=None) -> dict | None:
        if risk.max_positions and len(self.positions) >= risk.max_positions:
            return None
        if risk.max_trades_per_day and self.trades_today >= risk.max_trades_per_day:
            return None
        equity = self.equity(prices)
        if equity <= 0:
            return None
        fill = price * (1 + direction * risk.slippage_pct / 100)
        if stop_dist is None and risk.stop_pct:
            stop_dist = price * risk.stop_pct / 100
        stop_pct = stop_dist / price * 100 if stop_dist else 0.0
        if risk.risk_pct > 0 and stop_pct > 0:
            notional = equity * risk.risk_pct / stop_pct
        else:
            notional = equity * risk.leverage / max(n_symbols, 1)
        notional = min(notional, equity * risk.leverage - self.gross_exposure(prices))
        if rules.enabled and stop_pct > 0 and self.status == ACTIVE:
            # Size down so a stop-out can't breach the daily or total loss limit by itself.
            room = min(equity - self.day_start_equity * (1 - rules.max_daily_loss_pct / 100),
                       equity - self.starting_cash * (1 - rules.max_drawdown_pct / 100))
            notional = min(notional, 0.9 * room / ((stop_pct + 2 * risk.slippage_pct) / 100))
        qty = int(notional // fill) if notional > 0 else 0
        if qty <= 0:
            return None
        self.cash -= direction * qty * fill
        self.trades_today += 1
        stop = fill - direction * stop_dist if stop_dist else None
        if risk.reward_risk and stop_dist:
            target = fill + direction * stop_dist * risk.reward_risk
        elif risk.take_profit_pct:
            target = fill * (1 + direction * risk.take_profit_pct / 100)
        else:
            target = None
        self.positions[sym] = Position(direction * qty, fill, stop, target)
        return _fill(ts, sym, "BUY" if direction > 0 else "SHORT", qty, fill, None, "signal")

    def _close(self, sym, price, ts, risk, reason) -> dict:
        pos = self.positions.pop(sym)
        direction = 1 if pos.qty > 0 else -1
        fill = price * (1 - direction * risk.slippage_pct / 100)
        self.cash += pos.qty * fill
        pnl = (fill - pos.entry) * pos.qty
        return _fill(ts, sym, "SELL" if direction > 0 else "COVER", abs(pos.qty), fill, pnl, reason)


def _fill(ts, symbol, side, qty, price, pnl, reason) -> dict:
    return {"ts": ts, "symbol": symbol, "side": side, "qty": qty, "price": price,
            "pnl": pnl, "reason": reason}
