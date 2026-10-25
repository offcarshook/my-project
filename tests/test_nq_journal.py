"""ORB trade journal (nq/journal.py): fields, reconciliation, mark-to-market drawdown."""
import pandas as pd
import pytest

pytest.importorskip("nautilus_trader")

from nq.backtest import run_orb  # noqa: E402
from nq.journal import enrich, metrics, mtm_curve  # noqa: E402
from test_nq_orb import NY, day, frame  # noqa: E402


def test_journal_fields_reconcile_and_intraday_drawdown_includes_the_open_trade():
    rows = [(20000, 20050, 19950, 20000)]                                  # 09:30 candle = 5m range 19950-20050
    rows += [(20000, 20005, 19995, 20002), (20002, 20060, 20001, 20055)]   # breakout close at 09:45 candle
    rows += [(20055, 20056, 19960, 19990)]                                  # deep dip, stop (19950) not hit
    rows += [(19990, 20120, 19985, 20110)] * 74                             # recovers into the 15:50 flatten
    f = frame(day("2026-01-05", rows))
    trades, equity = run_orb(f, range_minutes=5, risk_pct=2.0)
    j = enrich(trades, f)
    tr = j.iloc[0]
    assert tr.or_high == 20050 and tr.or_low == 19950 and tr.or_width_ticks == 400
    assert tr.breakout_distance_pts == 5 and tr.entry_slippage_ticks == 1
    assert tr.intended_price == 20055 and tr.fill_price == 20055.25 and tr.exit_reason == "end of day"
    assert 50_000 + j.net_pnl_usd.sum() == pytest.approx(float(equity.iloc[-1]))
    assert tr.mae_r == pytest.approx((20055.25 - 19960) / (20055.25 - 19950))
    m = metrics(j, mtm_curve(j, f))
    # the closed trade is a winner, but the account dipped while it was open
    assert m["max_dd_closed_usd"] == 0 and m["max_dd_intraday_mtm_usd"] < -100
