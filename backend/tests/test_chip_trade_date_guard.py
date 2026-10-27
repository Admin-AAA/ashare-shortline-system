import pandas as pd

from app.services.data_freshness import chip_frozen_issue
from app.services.tushare_client_pure import keep_rows_for_trade_date


def test_rows_from_other_trade_date_are_dropped_not_restamped():
    frame = pd.DataFrame({
        "ts_code": ["000001.SZ", "000002.SZ", "600000.SH"],
        "trade_date": ["20260730", "20260730", "20260731"],
        "cost_50pct": [10.0, 20.0, 30.0],
    })
    kept = keep_rows_for_trade_date(frame, "2026-07-31", api_name="cyq_perf")
    assert list(kept["ts_code"]) == ["600000.SH"]
    # upstream answered entirely with the previous day -> nothing to write (stays NULL)
    assert keep_rows_for_trade_date(frame.iloc[:2], "20260731").empty


def test_frame_without_mismatch_or_trade_date_column_is_untouched():
    frame = pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20260731"]})
    assert keep_rows_for_trade_date(frame, "2026-07-31") is frame
    bare = pd.DataFrame({"ts_code": ["000001.SZ"]})
    assert keep_rows_for_trade_date(bare, "2026-07-31") is bare
    assert keep_rows_for_trade_date(None, "2026-07-31") is None


def test_chip_frozen_threshold():
    assert chip_frozen_issue("2026-08-03", "2026-07-31", 5522, 5522)
    assert chip_frozen_issue("2026-10-14", "2026-10-13", 5560, 1000) is None
    assert chip_frozen_issue("2026-10-14", "2026-10-13", 0, 0) is None
