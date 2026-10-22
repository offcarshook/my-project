import pandas as pd

from bot.data import load_bars


class FakeData:
    def __init__(self):
        self.calls = []

    def get_history(self, symbols, start, timeframe, end=None, feed=None):
        self.calls.append((symbols[0], pd.Timestamp(start).strftime("%Y-%m"), feed))
        first = (pd.Timestamp(start).normalize() + pd.Timedelta(hours=10)).tz_convert("UTC")
        idx = pd.date_range(first, periods=3, freq="D")
        return {symbols[0]: pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=idx)}


def test_monthly_cache_reused_and_filtered(tmp_path):
    data = FakeData()
    bars = load_bars(data, ["QQQ"], "2025-01-15", "2025-03-02", "5Min", "sip", str(tmp_path), log=lambda *_: None)
    assert [c[1] for c in data.calls] == ["2025-01", "2025-02", "2025-03"]
    assert (tmp_path / "sip" / "5Min" / "QQQ" / "2025-02.csv.gz").exists()
    df = bars["QQQ"]
    assert df.index.min() >= pd.Timestamp("2025-01-15", tz="America/New_York")
    assert df.index.max() < pd.Timestamp("2025-03-03", tz="America/New_York")

    again = FakeData()
    load_bars(again, ["QQQ"], "2025-01-15", "2025-03-02", "5Min", "sip", str(tmp_path), log=lambda *_: None)
    assert again.calls == []   # everything came from the cache


def test_current_month_not_cached(tmp_path):
    month = pd.Timestamp.now(tz="America/New_York").tz_localize(None).to_period("M")
    load_bars(FakeData(), ["SPY"], month.start_time, month.start_time + pd.Timedelta(days=1), "5Min", "iex",
              str(tmp_path), log=lambda *_: None)
    assert not (tmp_path / "iex" / "5Min" / "SPY" / f"{month}.csv.gz").exists()
