import pytest

from bot.book import FAILED, PASSED, Book, ChallengeRules, RiskConfig

SAFE = RiskConfig(slippage_pct=0)
OFF = ChallengeRules()


def step(book, prices, signals, risk=SAFE, rules=OFF, day="2026-01-05", n=1, eod=False):
    return book.step("t", day, prices, signals, risk, rules, n, eod)


def test_safe_mode_buys_and_sells_whole_shares():
    book = Book.new(1000)
    [buy] = step(book, {"A": 100}, {"A": ("BUY", 1)})
    assert buy["qty"] == 10 and book.cash == 0
    [sell] = step(book, {"A": 110}, {"A": ("SELL", 2)})
    assert sell["pnl"] == 100 and book.cash == 1100 and not book.positions


def test_same_bar_signal_only_acts_once():
    book = Book.new(1000)
    step(book, {"A": 100}, {"A": ("BUY", 1)})
    step(book, {"A": 100}, {"A": ("SELL", 2)})
    assert step(book, {"A": 100}, {"A": ("SELL", 2)}) == []
    assert step(book, {"A": 100}, {"A": ("BUY", 2)}) == []  # still bar 2


def test_short_and_reverse():
    risk = RiskConfig(allow_short=True, slippage_pct=0)
    book = Book.new(1000)
    [short] = step(book, {"A": 100}, {"A": ("SELL", 1)}, risk)
    assert short["side"] == "SHORT" and book.positions["A"].qty == -10
    cover, buy = step(book, {"A": 90}, {"A": ("BUY", 2)}, risk)
    assert cover["side"] == "COVER" and cover["pnl"] == pytest.approx(100)
    assert buy["side"] == "BUY" and book.positions["A"].qty > 0


def test_leverage_and_risk_sizing():
    risk = RiskConfig(leverage=4, risk_pct=2, stop_pct=1, slippage_pct=0)
    book = Book.new(10_000)
    [buy] = step(book, {"A": 100}, {"A": ("BUY", 1)}, risk)
    assert buy["qty"] == 200  # 2% risk / 1% stop = 2x equity
    assert book.cash == -10_000  # borrowed on margin
    assert book.equity({"A": 100}) == 10_000
    # 4x cap: second position can only use the remaining 2x
    [buy2] = step(book, {"A": 100, "B": 50}, {"B": ("BUY", 1)}, risk)
    assert buy2["qty"] * 50 == 20_000


def test_stop_loss_and_take_profit():
    risk = RiskConfig(stop_pct=2, take_profit_pct=5, slippage_pct=0)
    book = Book.new(1000)
    step(book, {"A": 100, "B": 100}, {"A": ("BUY", 1), "B": ("BUY", 1)}, risk, n=2)
    fills = step(book, {"A": 97, "B": 106}, {}, risk)
    assert {f["symbol"]: f["reason"] for f in fills} == {"A": "stop loss", "B": "take profit"}


def test_end_of_day_flatten():
    book = Book.new(1000)
    step(book, {"A": 100}, {"A": ("BUY", 1)})
    [f] = step(book, {"A": 101}, {}, eod=True)
    assert f["reason"] == "end of day" and not book.positions


def test_challenge_pass_and_fail():
    rules = ChallengeRules(enabled=True, profit_target_pct=8, max_daily_loss_pct=5, max_drawdown_pct=10)
    book = Book.new(1000)
    step(book, {"A": 100}, {"A": ("BUY", 1)}, rules=rules)
    step(book, {"A": 109}, {}, rules=rules)
    assert book.status == PASSED and not book.positions
    assert step(book, {"A": 100}, {"A": ("BUY", 3)}, rules=rules) == []

    book = Book.new(1000)
    step(book, {"A": 100}, {"A": ("BUY", 1)}, rules=rules)
    step(book, {"A": 94}, {}, rules=rules)
    assert book.status == FAILED and book.status_reason == "daily loss limit hit"


def test_overnight_gap_counts_toward_daily_loss():
    rules = ChallengeRules(enabled=True, max_daily_loss_pct=5, max_drawdown_pct=50)
    book = Book.new(1000)
    step(book, {"A": 100}, {"A": ("BUY", 1)}, rules=rules, day="2026-01-05")
    step(book, {"A": 93}, {}, rules=rules, day="2026-01-06")  # gaps down 7% at the open
    assert book.status == FAILED and book.status_reason == "daily loss limit hit"


def test_challenge_sizing_respects_daily_room():
    risk = RiskConfig(leverage=10, risk_pct=10, stop_pct=1, slippage_pct=0)
    rules = ChallengeRules(enabled=True, max_daily_loss_pct=5)
    book = Book.new(10_000)
    [buy] = step(book, {"A": 100}, {"A": ("BUY", 1)}, risk, rules)
    worst_loss = buy["qty"] * 100 * 0.01
    assert worst_loss < 500


def test_strategy_stop_distance_sets_size_stop_and_target():
    risk = RiskConfig(leverage=4, risk_pct=0.5, reward_risk=2, slippage_pct=0)
    book = Book.new(10_000)
    [buy] = step(book, {"A": 100}, {"A": ("BUY", 1, 0.5)}, risk)
    assert buy["qty"] == 100  # $50 risk / $0.50 stop distance
    pos = book.positions["A"]
    assert pos.stop == 99.5 and pos.target == 101


def test_intrabar_stop_fills_at_stop_level():
    risk = RiskConfig(risk_pct=0.5, reward_risk=2, leverage=4, slippage_pct=0)
    book = Book.new(10_000)
    step(book, {"A": 100}, {"A": ("BUY", 1, 0.5)}, risk)
    # close is back above the stop, but the low went through it
    [f] = book.step("t", "2026-01-05", {"A": 100.2}, {}, risk, OFF, 1, highs={"A": 100.3}, lows={"A": 99.4})
    assert f["reason"] == "stop loss" and f["price"] == 99.5 and f["pnl"] == -50


def test_both_stop_and_target_touched_counts_as_stop():
    risk = RiskConfig(risk_pct=0.5, reward_risk=2, leverage=4, slippage_pct=0)
    book = Book.new(10_000)
    step(book, {"A": 100}, {"A": ("BUY", 1, 0.5)}, risk)
    [f] = book.step("t", "2026-01-05", {"A": 100}, {}, risk, OFF, 1, highs={"A": 101.5}, lows={"A": 99})
    assert f["reason"] == "stop loss"


def test_max_trades_per_day():
    risk = RiskConfig(max_trades_per_day=2, slippage_pct=0)
    book = Book.new(3000)
    signals = {s: ("BUY", 1) for s in "ABC"}
    fills = step(book, {"A": 100, "B": 100, "C": 100}, signals, risk, n=3)
    assert len(fills) == 2
    fills = step(book, {"A": 100, "B": 100, "C": 100}, {"C": ("BUY", 2)}, risk, day="2026-01-06", n=3)
    assert len(fills) == 1  # new day resets the count
