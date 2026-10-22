import copy

import pandas as pd
import pytest

from bot.config import load_config
from bot.sim import Simulator

BASE = load_config("config.yaml")


def cfg(**risk):
    c = copy.deepcopy(BASE)
    c.costs.slippage_bps = 0
    c.costs.sec_fee_rate = c.costs.taf_per_share = 0
    c.instruments = {}
    c.costs.default_spread_bps = 0
    c.backtest.starting_cash = 100_000
    c.risk.stop.type, c.risk.stop.fixed_pct = "fixed", 1.0      # 1% stop
    c.risk.reward_risk = 2.0
    c.risk.sizing_buffer_pct = 0
    for k, v in risk.items():
        setattr(c.risk, k, v)
    return c


class Given:
    """Strategy stub: the test supplies cross/signal columns directly."""
    warmup = 0

    def __init__(self, signals):
        self.signals = signals  # {bar_number: +1/-1}

    def compute(self, df):
        df = df.copy()
        df["atr"] = 1.0
        df["cross"] = 0
        df["signal"] = "HOLD"
        for i, s in self.signals.items():
            df.iloc[i, df.columns.get_loc("cross")] = s
            df.iloc[i, df.columns.get_loc("signal")] = "BUY" if s > 0 else "SELL"
        return df


def day(rows, date="2026-01-05", start="10:00"):
    """rows: list of (open, high, low, close)."""
    idx = pd.date_range(f"{date} {start}", periods=len(rows), freq="5min", tz="America/New_York").tz_convert("UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx).assign(volume=1e6)


FLAT = (100, 100.2, 99.9, 100)


def test_entry_fills_at_next_candle_open_not_signal_close():
    bars = day([FLAT, (101, 101.2, 100.9, 101), FLAT, FLAT])
    res = Simulator(cfg(), {"A": bars}, Given({0: 1})).run()
    t = res.trades.iloc[0]
    assert t.entry == 101 and t.entry_ts == bars.index[1]
    # 0.25% of 100k = $250 risk, 1% stop on $101 = $1.01/share -> 247 shares
    assert t.qty == 247 and t.stop == pytest.approx(99.99)


def test_take_profit_hits():
    bars = day([FLAT, FLAT, (100, 102.5, 99.95, 102)])
    t = Simulator(cfg(), {"A": bars}, Given({0: 1})).run().trades.iloc[0]
    assert t.reason == "take profit" and t.exit == pytest.approx(102) and t.r == pytest.approx(2)


def test_stop_wins_when_both_touched_in_one_candle():
    bars = day([FLAT, FLAT, (100, 103, 98, 100)])
    t = Simulator(cfg(), {"A": bars}, Given({0: 1})).run().trades.iloc[0]
    assert t.reason == "stop loss" and t.exit == 99 and t.r == pytest.approx(-1)


def test_gap_through_stop_fills_at_open():
    bars = day([FLAT, FLAT, (97, 97.5, 96.5, 97)])
    t = Simulator(cfg(), {"A": bars}, Given({0: 1})).run().trades.iloc[0]
    assert t.reason == "stop loss" and t.exit == 97 and t.r < -1


def test_short_trade():
    bars = day([FLAT, FLAT, (100, 100.05, 97.9, 98)])
    t = Simulator(cfg(), {"A": bars}, Given({0: -1})).run().trades.iloc[0]
    assert t.side == "SHORT" and t.reason == "take profit" and t.pnl > 0


def test_opposite_signal_exits_and_reverses_at_next_open():
    bars = day([FLAT, FLAT, FLAT, (99.5, 99.6, 99.4, 99.5), FLAT])
    res = Simulator(cfg(), {"A": bars}, Given({0: 1, 2: -1})).run()
    first, second = res.trades.iloc[0], res.trades.iloc[1]
    assert first.reason == "opposite signal" and first.exit == 99.5 and first.exit_ts == bars.index[3]
    assert second.side == "SHORT" and second.entry == 99.5


def test_flatten_time_and_no_overnight_positions():
    rows = [FLAT] * 6
    bars = pd.concat([day(rows, start="15:30"), day(rows, date="2026-01-06", start="10:00")])
    res = Simulator(cfg(), {"A": bars}, Given({0: 1})).run()
    t = res.trades.iloc[0]
    assert t.reason == "end of day" and pd.Timestamp(t.exit_ts).tz_convert("America/New_York").strftime("%H:%M") == "15:50"
    assert res.equity.loc[res.equity.index > t.exit_ts, "positions"].max() == 0


def test_early_close_session_flattens_at_last_candle():
    bars = pd.concat([day([FLAT] * 4, start="12:40"), day([FLAT] * 3, date="2026-01-06")])  # session ends 12:55
    t = Simulator(cfg(), {"A": bars}, Given({0: 1})).run().trades.iloc[0]
    assert t.reason == "end of day" and t.exit_ts == bars.index[3]


def test_no_entry_from_signal_on_last_candle_before_flatten():
    bars = day([FLAT] * 6, start="15:25")  # 15:45 signal would execute at 15:50 = flatten
    assert Simulator(cfg(), {"A": bars}, Given({4: 1})).run().trades.empty


def test_costs_reduce_pnl():
    bars = day([FLAT, FLAT, (100, 102.5, 99.95, 102)])
    free = Simulator(cfg(), {"A": bars}, Given({0: 1})).run().trades.iloc[0]
    c = cfg()
    c.costs.default_spread_bps, c.costs.slippage_bps, c.costs.sec_fee_rate = 2, 1, 0.0000278
    paid = Simulator(c, {"A": bars}, Given({0: 1})).run().trades.iloc[0]
    assert paid.entry > free.entry and paid.fees > 0 and paid.pnl < free.pnl


def test_max_trades_per_day():
    bars = {s: day([FLAT] * 4) for s in "ABC"}
    res = Simulator(cfg(max_trades_per_day=2, max_per_group=5), bars, Given({0: 1})).run()
    assert len(res.trades) == 2


def crash_setup():
    c = cfg(risk_per_trade_pct=1.0, max_gross_exposure=50)
    c.risk.stop.fixed_pct = 20
    c.risk.max_positions = 5
    return c


def test_sizing_keeps_total_stop_risk_inside_the_loss_limit():
    # 1% risk x 5 symbols would be 5%, but only 4% of room is left after the 1% safety buffer:
    # the 5th entry is refused and the stops cap the loss before the 5% daily limit
    crash = day([FLAT, FLAT, (100, 100, 60, 61), FLAT, FLAT])
    res = Simulator(crash_setup(), {s: crash for s in "ABCDE"}, Given({0: 1})).run(challenge=True)
    assert len(res.trades) == 4 and set(res.trades.reason) == {"stop loss"}
    assert res.status == "ACTIVE" and res.equity.equity.iloc[-1] == pytest.approx(96_000)


def test_gap_past_stops_breaches_limit_and_halts():
    gap = day([FLAT, FLAT, (60, 61, 59, 60), FLAT, FLAT])
    res = Simulator(crash_setup(), {s: gap for s in "ABCDE"}, Given({0: 1})).run(challenge=True)
    assert res.status == "FAILED" and "daily loss" in res.reason
    assert (res.trades.exit == 60).all()       # stops filled at the gapped open
    assert len(res.equity) == 3                # nothing after the breach candle
    assert res.decisions and res.decisions[0][1] == "flatten_and_lock"


def test_challenge_target_stops_trading():
    c = cfg(risk_per_trade_pct=1.0)
    c.risk.reward_risk = 0  # no take profit, ride it
    bars = day([FLAT, FLAT, (100, 160, 100, 159), FLAT])
    res = Simulator(c, {"A": bars}, Given({0: 1})).run(challenge=True)
    assert res.status == "PASSED" and res.trades.iloc[0].reason == "profit target reached"


def test_trailing_stop_locks_in_profit():
    c = cfg()
    c.risk.trailing.enabled, c.risk.trailing.activate_r, c.risk.trailing.atr_mult = True, 1.0, 0.5
    c.risk.reward_risk = 0
    bars = day([FLAT, FLAT, (100, 101.6, 100, 101.5), (101.5, 101.6, 100.5, 100.8)])
    t = Simulator(c, {"A": bars}, Given({0: 1})).run().trades.iloc[0]
    assert t.reason == "stop loss" and t.exit == pytest.approx(101.1) and t.pnl > 0
