"""Dukascopy candle decoding (nq/dukascopy.py)."""
import datetime as dt
import lzma
import struct

from nq.dukascopy import decode


def test_decode_records_and_scale(tmp_path):
    recs = [(0, 16841909, 16840139, 16839769, 16842389, 0.02),            # 00:00 UTC
            (52200, 16674229, 16681969, 16654869, 16682419, 0.17)]        # 14:30 UTC = 09:30 New York
    raw = b"".join(struct.pack(">5if", *r) for r in recs)
    path = tmp_path / "day.bi5"
    path.write_bytes(lzma.compress(raw, format=lzma.FORMAT_ALONE))
    d = decode(str(path), dt.date(2024, 1, 2), 1000.0)
    assert len(d) == 2 and str(d.index[1]) == "2024-01-02 14:30:00+00:00"
    row = d.iloc[1]                                                        # file order: open, close, low, high
    assert (row.open, row.close, row.low, row.high) == (16674.229, 16681.969, 16654.869, 16682.419)


def test_empty_file_is_an_empty_day(tmp_path):
    path = tmp_path / "holiday.bi5"
    path.write_bytes(b"")
    assert decode(str(path), dt.date(2024, 12, 25), 1000.0).empty
