from sqlalchemy import select

from app.models.tables import DailyKbar
from app.services.market_pool.core import _load_cached_bars_batch


def test_cached_bar_batch_returns_only_latest_rows_in_ascending_order(db):
    codes = ["000001", "600000"]
    as_of = "2026-07-24"
    actual = _load_cached_bars_batch(db, codes, limit=3, as_of_date=as_of)

    for code in codes:
        expected = db.scalars(
            select(DailyKbar)
            .where(DailyKbar.code == code, DailyKbar.trade_date <= as_of)
            .order_by(DailyKbar.trade_date.desc())
            .limit(3)
        ).all()
        expected_dates = sorted(str(row.trade_date) for row in expected)
        assert [row["trade_date"] for row in actual[code]] == expected_dates
        assert all(row["code"] == code for row in actual[code])
