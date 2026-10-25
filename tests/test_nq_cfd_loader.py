"""MetaTrader CFD loader (nq/data.load_mt_cfd): server clock -> New York, regular session."""
import pytest

pytest.importorskip("pandas_market_calendars")

from nq.data import load_mt_cfd  # noqa: E402


def test_server_clock_converts_to_new_york_and_keeps_the_session(tmp_path):
    rows = ["DateTime\tOpen\tHigh\tLow\tClose\tVolume\tTickVolume",
            "2024.01.02 16:15:00\t1\t1\t1\t1\t0\t10",          # 09:15 NY: pre-market, dropped
            "2024.01.02 16:30:00\t16704.1\t16715.3\t16610.2\t16619.8\t0\t6919",   # 09:30 NY winter (GMT+2)
            "2024.07.01 16:30:00\t20000\t20010\t19990\t20005\t0\t500",           # 09:30 NY summer (GMT+3)
            "2024.03.12 15:30:00\t18000\t18010\t17990\t18005\t0\t500"]           # 09:30 NY in the DST gap week
    p = tmp_path / "m15.csv"
    p.write_text("\n".join(rows))
    f = load_mt_cfd(str(p))
    assert [t.strftime("%Y-%m-%d %H:%M") for t in f.index] == ["2024-01-02 09:45", "2024-03-12 09:45", "2024-07-01 09:45"]
    assert f.iloc[0].open == 16704.0 and f.iloc[0].close == 16619.75            # rounded to 0.25
