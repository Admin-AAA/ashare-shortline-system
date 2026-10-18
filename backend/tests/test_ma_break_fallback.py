from types import SimpleNamespace as N

from app.services.ma_break_exit import first_break_date


def _rows(closes, ma5=None):
    return [N(trade_date=f"2026-01-{i + 1:02d}", close=c, open=c, high=c, low=c, pct_chg=0.0, volume=1.0,
              ma5=ma5, ma10=None) for i, c in enumerate(closes)]


def test_zero_or_null_ma_computed_from_closes():
    closes = [10, 10, 10, 10, 10, 9]  # day6 close 9 < ma5 (9.8)
    for stored in (None, 0.0):
        assert first_break_date("000001", _rows(closes, stored), 5, "2026-01-01", "2026-01-31") == "2026-01-06"


def test_partial_window_skipped():
    closes = [10, 10, 9]  # only 3 closes available: no ma5 -> no break
    assert first_break_date("000001", _rows(closes, 0.0), 5, "2026-01-01", "2026-01-31") is None


def test_stored_ma_still_preferred():
    closes = [10, 10, 10, 10, 10, 9]
    assert first_break_date("000001", _rows(closes, 8.0), 5, "2026-01-01", "2026-01-31") is None
