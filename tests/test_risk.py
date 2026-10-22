import pandas as pd
import pytest

from bot.config import ChallengeSettings, CostSettings, RiskSettings
from bot.costs import CostModel
from bot.risk import (FAILED, FLATTEN_LOCK, NO_NEW_ENTRIES, OK, PASSED, TARGET_HIT, OpenPosition, RiskManager,
                      open_risk, size_position)


def rules(**kw):
    base = dict(profit_target_pct=8, max_daily_loss_pct=5, max_total_loss_pct=10, daily_loss_base="initial",
                day_start_reference="balance", drawdown_type="static", daily_reset="00:00", safety_buffer_pct=0)
    return ChallengeSettings(**{**base, **kw})


def ts(s):
    return pd.Timestamp(s, tz="America/New_York")


# ---- drawdown limits ------------------------------------------------------
def test_daily_limit_from_initial_balance():
    rm = RiskManager(rules(), 100_000)
    assert rm.update(ts("2026-01-05 10:00"), 100_000, 100_000).action == OK
    assert rm.daily_floor() == 95_000
    d = rm.update(ts("2026-01-05 11:00"), 100_000, 96_000, worst_equity=94_999)
    assert d.action == FLATTEN_LOCK and "daily" in d.reason and rm.state.locked


def test_daily_limit_resets_next_day_from_new_balance():
    rm = RiskManager(rules(), 100_000)
    rm.update(ts("2026-01-05 10:00"), 100_000, 100_000)
    rm.update(ts("2026-01-05 15:59"), 97_000, 97_000)
    rm.update(ts("2026-01-06 09:31"), 97_000, 97_000)
    assert rm.daily_floor() == 92_000  # 97k - 5% of the 100k starting balance


def test_daily_limit_percent_of_day_start_and_higher_reference():
    rm = RiskManager(rules(daily_loss_base="day_start", day_start_reference="higher"), 100_000)
    rm.update(ts("2026-01-05 10:00"), 110_000, 112_000)
    assert rm.daily_floor() == pytest.approx(112_000 * 0.95)


def test_daily_reset_time_respected():
    rm = RiskManager(rules(daily_reset="17:00"), 100_000)
    rm.update(ts("2026-01-05 16:00"), 100_000, 100_000)
    rm.update(ts("2026-01-05 16:59"), 98_000, 98_000)
    assert rm.daily_floor() == 95_000          # still the same "day"
    rm.update(ts("2026-01-05 17:01"), 98_000, 98_000)
    assert rm.daily_floor() == 93_000          # new day after 17:00


def test_static_total_drawdown():
    rm = RiskManager(rules(max_daily_loss_pct=50), 100_000)
    rm.update(ts("2026-01-05 10:00"), 105_000, 105_000)
    assert rm.total_floor() == 90_000
    assert rm.update(ts("2026-01-06 10:00"), 90_500, 89_999).action == FLATTEN_LOCK
    assert "total" in rm.state.reason and rm.state.status == FAILED


def test_trailing_drawdown_follows_high_water_and_locks_at_initial():
    rm = RiskManager(rules(drawdown_type="trailing", max_daily_loss_pct=50, profit_target_pct=50), 100_000)
    rm.update(ts("2026-01-05 10:00"), 100_000, 104_000)
    assert rm.total_floor() == 94_000
    rm.update(ts("2026-01-05 11:00"), 100_000, 115_000)
    assert rm.total_floor() == 100_000  # locked at the starting balance
    rm2 = RiskManager(rules(drawdown_type="trailing", trailing_lock_at_initial=False, max_daily_loss_pct=50,
                            profit_target_pct=50), 100_000)
    rm2.update(ts("2026-01-05 11:00"), 100_000, 115_000)
    assert rm2.total_floor() == 105_000


def test_trailing_on_balance_ignores_open_profit():
    rm = RiskManager(rules(drawdown_type="trailing", trailing_basis="balance", max_daily_loss_pct=50), 100_000)
    rm.update(ts("2026-01-05 10:00"), 100_000, 107_000)
    assert rm.total_floor() == 90_000


def test_open_losses_count_toward_limits():
    rm = RiskManager(rules(), 100_000)
    rm.update(ts("2026-01-05 10:00"), 100_000, 100_000)
    # balance untouched but open positions are down 5.1%
    assert rm.update(ts("2026-01-05 10:05"), 100_000, 94_900).action == FLATTEN_LOCK


def test_safety_buffer_blocks_new_entries_but_does_not_lock():
    rm = RiskManager(rules(safety_buffer_pct=1), 100_000)
    rm.update(ts("2026-01-05 10:00"), 100_000, 100_000)
    d = rm.update(ts("2026-01-05 10:05"), 100_000, 95_900)
    assert d.action == NO_NEW_ENTRIES and not rm.can_open() and not rm.state.locked
    assert rm.update(ts("2026-01-05 10:10"), 100_000, 97_500).action == OK and rm.can_open()


def test_target_stops_trading():
    rm = RiskManager(rules(), 100_000)
    assert rm.update(ts("2026-01-05 10:00"), 100_000, 108_000).action == TARGET_HIT
    assert rm.state.status == PASSED and not rm.can_open()


def test_lock_persists_until_manual_reset_and_survives_reload():
    rm = RiskManager(rules(), 100_000)
    rm.update(ts("2026-01-05 10:00"), 100_000, 94_000)
    again = RiskManager.from_dict(rm.rules, rm.to_dict())
    assert again.state.locked and again.update(ts("2026-01-06 10:00"), 94_000, 94_000).action == FLATTEN_LOCK
    again.reset()
    assert again.can_open() and again.update(ts("2026-01-06 10:05"), 100_000, 100_000).action == OK


# ---- position sizing ------------------------------------------------------
RISK = RiskSettings(risk_per_trade_pct=0.25, max_positions=3, max_per_group=2, max_gross_exposure=3.0,
                    max_trades_per_day=20, sizing_buffer_pct=0.05)


def size(**kw):
    args = dict(symbol="QQQ", direction=1, entry_price=500.0, stop_dist=1.0, equity=100_000.0, positions=[],
                risk=RISK, group="nasdaq", leverage=1.0, risk_room=10_000.0, trades_today=0)
    return size_position(**{**args, **kw})


def test_size_from_stop_distance_with_buffer():
    r = size()
    # $250 risk / ($1 stop + $0.25 buffer) = 200 shares
    assert r.qty == 200 and r.risk_dollars <= 250


def test_wider_stop_means_fewer_shares():
    assert size(stop_dist=2.0).qty < size(stop_dist=1.0).qty


def test_exposure_cap_with_leverage():
    # tight stop would allow a huge position, the 3x exposure cap trims it (TQQQ counts 3x)
    r = size(symbol="TQQQ", entry_price=50.0, stop_dist=0.01, leverage=3.0)
    assert r.qty == 2000  # 3 x 100k / (50 x 3)


def test_correlated_positions_limited_and_inverse_counts_as_short():
    longs = [OpenPosition("QQQ", 10, 500, 495, 500, "nasdaq", 1), OpenPosition("XLK", 10, 200, 198, 200, "nasdaq", 1)]
    assert size(symbol="SMH", positions=longs).reason.startswith("max 2 correlated")
    # buying SQQQ is a short Nasdaq bet, so it's not the same direction
    assert size(symbol="SQQQ", leverage=-3, entry_price=10, stop_dist=0.1, positions=longs).qty > 0
    # shorting SQQQ is a long Nasdaq bet: blocked
    assert size(symbol="SQQQ", direction=-1, leverage=-3, entry_price=10, stop_dist=0.1,
                positions=longs).qty == 0


def test_limits_block_entries():
    assert size(trades_today=20).reason == "daily trade limit reached"
    three = [OpenPosition(s, 1, 100, 99, 100, g, 1) for s, g in [("SPY", "broad"), ("IWM", "small"), ("XLK", "nasdaq")]]
    assert size(positions=three).reason == "max open positions reached"
    assert size(positions=[OpenPosition("QQQ", 1, 500, 495, 500, "nasdaq", 1)]).reason.startswith("already")


def test_open_risk_reduces_room_near_drawdown_limit():
    pos = [OpenPosition("SPY", 100, 500, 498, 500, "broad", 1)]  # $200 at risk
    assert open_risk(pos) == 200
    r = size(positions=pos, risk_room=300)
    assert r.qty == 80  # only $100 of room left
    assert size(positions=pos, risk_room=150).reason == "too close to a drawdown limit"


def test_buying_power_cap():
    assert size(buying_power=5_000).qty == 10


# ---- costs ------------------------------------------------------------------
def test_cost_model_fills_and_fees():
    cm = CostModel(CostSettings(slippage_bps=1.0, sec_fee_rate=0.0000278, taf_per_share=0.000166, taf_max=8.30),
                   lambda s: 2.0)
    assert cm.market_fill("QQQ", 1, 100.0) == pytest.approx(100.02)   # 1bp half spread + 1bp slip
    assert cm.market_fill("QQQ", -1, 100.0) == pytest.approx(99.98)
    assert cm.fees(100, 100.0, is_sell=False) == 0
    assert cm.fees(100, 100.0, is_sell=True) == pytest.approx(10_000 * 0.0000278 + 0.0166)
    assert cm.fees(1_000_000, 1.0, is_sell=True) == pytest.approx(27.8 + 8.30)  # TAF capped
    assert not cm.limit_touched("QQQ", -1, 101.0, high=101.005, low=100)     # touched, not traded through
    assert cm.limit_touched("QQQ", -1, 101.0, high=101.02, low=100)
