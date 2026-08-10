from __future__ import annotations

from datetime import date

import pytest

from app.services.backtest.stock_metadata import (
    POINT_IN_TIME,
    UNKNOWN,
    StockMetadataRecord,
    current_static_resolution,
    resolve_stock_metadata_as_of,
)


def _record(*, industry: str, start: str, end: str | None = None) -> StockMetadataRecord:
    return StockMetadataRecord(
        code="000001",
        name="平安银行",
        industry=industry,
        market="主板",
        list_date="1991-04-03",
        is_st=False,
        effective_from=start,
        effective_to=end,
        source_version="stock-basic-history/v1",
    )


def test_point_in_time_resolution_uses_interval_for_signal_date() -> None:
    records = (
        _record(industry="银行", start="2025-01-01", end="2025-06-30"),
        _record(industry="金融", start="2025-07-01"),
    )

    first = resolve_stock_metadata_as_of(records, ("000001",), "2025-06-30")
    second = resolve_stock_metadata_as_of(records, ("000001",), "2025-07-01")

    assert first.temporality == POINT_IN_TIME
    assert second.temporality == POINT_IN_TIME
    assert first.metadata_by_code["000001"]["industry"] == "银行"
    assert second.metadata_by_code["000001"]["industry"] == "金融"
    assert first.fingerprint != second.fingerprint


def test_missing_interval_never_claims_point_in_time() -> None:
    result = resolve_stock_metadata_as_of(
        (_record(industry="银行", start="2025-01-01", end="2025-01-31"),),
        ("000001", "000002"),
        "2025-02-01",
    )

    assert result.temporality == UNKNOWN
    assert result.missing_codes == ("000001", "000002")


def test_overlapping_intervals_fail_closed() -> None:
    with pytest.raises(ValueError, match="overlapping"):
        resolve_stock_metadata_as_of(
            (
                _record(industry="银行", start="2025-01-01", end="2025-06-30"),
                _record(industry="金融", start="2025-06-30"),
            ),
            ("000001",),
            "2025-06-30",
        )


def test_current_static_resolution_is_explicitly_not_point_in_time() -> None:
    result = current_static_resolution(
        {"000001": {"name": "平安银行", "industry": "银行", "is_st": False}},
        ("000001",),
        "2025-06-30",
    )

    assert result.temporality == "current_static"
    assert result.canonical_payload()["signal_date"] == date(2025, 6, 30).isoformat()
