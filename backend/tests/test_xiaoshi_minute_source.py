from __future__ import annotations

import hashlib
import io
from pathlib import Path

import httpx
import pandas as pd
import pytest

from app.services.xiaoshi_minute_source import (
    XiaoshiMinuteHistorySource,
    XiaoshiMinuteSourceError,
    resample_minute_rows,
)


def _parquet_bytes(rows: list[dict]) -> bytes:
    frame = pd.DataFrame(rows)
    output = io.BytesIO()
    frame.to_parquet(output, engine="pyarrow", index=False)
    return output.getvalue()


def _file_info(part: str, content: bytes, url: str, *, year: int | None = None) -> dict:
    return {
        "part": part,
        "year": year,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "url": url,
    }


def test_xiaoshi_minute_history_downloads_verifies_merges_and_caches(tmp_path: Path) -> None:
    base = _parquet_bytes(
        [
            {
                "ts_code": "000001.SZ",
                "open": 10,
                "high": 10.2,
                "low": 9.9,
                "close": 10,
                "vol": 100,
                "amount": 1000,
                "adj_factor": 1,
                "trade_date": "20260701",
                "trade_time": "2026-07-01 09:31:00",
            },
            {
                "ts_code": "000001.SZ",
                "open": 10,
                "high": 10.4,
                "low": 9.8,
                "close": 10.1,
                "vol": 200,
                "amount": 2000,
                "adj_factor": 1,
                "trade_date": "20260701",
                "trade_time": "2026-07-01 09:32:00",
            },
        ]
    )
    update = _parquet_bytes(
        [
            {
                "ts_code": "000001.SZ",
                "open": 20,
                "high": 21,
                "low": 19,
                "close": 20.5,
                "vol": 999,
                "amount": 9999,
                "adj_factor": 1,
                "trade_date": "20260701",
                "trade_time": "2026-07-01 09:32:00",
            },
            {
                "ts_code": "000002.SZ",
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
                "vol": 1,
                "amount": 1,
                "adj_factor": 1,
                "trade_date": "20260701",
                "trade_time": "2026-07-01 09:32:00",
            },
        ]
    )
    blobs = {"https://r2.test/base": base, "https://r2.test/update": update}
    files = [
        _file_info("base", base, "https://r2.test/base"),
        _file_info("update", update, "https://r2.test/update", year=2026),
    ]
    url_calls: list[str] = []

    class FakeXiaoshiClient:
        def get_history_download_session(self, **params):
            url_calls.append(str(params["year"]))
            return {"files": files}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=blobs[str(request.url)], request=request)

    r2_client = httpx.Client(transport=httpx.MockTransport(handler))
    source = XiaoshiMinuteHistorySource(
        client=FakeXiaoshiClient(),  # type: ignore[arg-type]
        cache_dir=tmp_path,
        r2_client=r2_client,
    )
    try:
        rows = source.fetch("000001", start_date="20260701", end_date="20260701")
        assert [row["trade_time"] for row in rows] == [
            "2026-07-01 09:31:00",
            "2026-07-01 09:32:00",
        ]
        assert rows[-1]["close"] == pytest.approx(20.5)
        assert rows[-1]["volume"] == pytest.approx(999)

        rows_again = source.fetch("000001", start_date="20260701", end_date="20260701")
        assert rows_again == rows
        assert url_calls == ["2026", "2026"]
        assert sorted(path.name for path in (tmp_path / "000001").rglob("*.parquet")) == [
            "base.parquet",
            "update.parquet",
        ]
    finally:
        r2_client.close()


def test_resample_minute_rows_keeps_lunch_break_separate() -> None:
    rows = []
    for index, minute in enumerate((31, 32, 33, 34, 35), start=1):
        rows.append(
            {
                "code": "000001",
                "trade_date": "2026-07-01",
                "trade_time": f"2026-07-01 09:{minute:02d}:00",
                "open": float(index),
                "high": float(index + 1),
                "low": float(index),
                "close": float(index + 0.5),
                "volume": 10,
                "amount": 100,
                "source": "test",
            }
        )
    for index, minute in enumerate((1, 2, 3, 4, 5), start=6):
        rows.append(
            {
                "code": "000001",
                "trade_date": "2026-07-01",
                "trade_time": f"2026-07-01 13:{minute:02d}:00",
                "open": float(index),
                "high": float(index + 1),
                "low": float(index),
                "close": float(index + 0.5),
                "volume": 10,
                "amount": 100,
                "source": "test",
            }
        )

    result = resample_minute_rows(rows, "5min")

    assert [row["trade_time"] for row in result] == [
        "2026-07-01 09:35:00",
        "2026-07-01 13:05:00",
    ]
    assert all(row["volume"] == 50 for row in result)


def test_xiaoshi_minute_source_rejects_invalid_ohlc(tmp_path: Path) -> None:
    content = _parquet_bytes(
        [
            {
                "ts_code": "000001.SZ",
                "open": 10,
                "high": 9,
                "low": 9,
                "close": 10,
                "vol": 1,
                "amount": 1,
                "adj_factor": 1,
                "trade_date": "20260701",
                "trade_time": "2026-07-01 09:31:00",
            }
        ]
    )
    file_info = _file_info("base", content, "https://r2.test/base")

    class FakeXiaoshiClient:
        def get_history_download_session(self, **params):
            return {"files": [file_info]}

    r2_client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=content, request=request)
        )
    )
    source = XiaoshiMinuteHistorySource(
        client=FakeXiaoshiClient(),  # type: ignore[arg-type]
        cache_dir=tmp_path,
        r2_client=r2_client,
    )
    try:
        with pytest.raises(XiaoshiMinuteSourceError, match="OHLC"):
            source.fetch("000001", start_date="20260701", end_date="20260701")
    finally:
        r2_client.close()


def test_unified_minute_entry_prefers_xiaoshi_before_tushare(monkeypatch) -> None:
    from app.services.tushare_client import fetch_minute_kbars

    rows = [
        {
            "code": "000001",
            "trade_date": "2026-07-01",
            "trade_time": "2026-07-01 09:30:00",
            "open": 10.0,
            "high": 10.1,
            "low": 9.9,
            "close": 10.05,
            "volume": 100.0,
            "amount": 1000.0,
            "source": "xiaoshi_min1",
        }
    ]
    monkeypatch.setattr("app.services.tushare_client._fetch_xiaoshi_minute_kbars", lambda *args, **kwargs: rows)
    monkeypatch.setattr(
        "app.services.tushare_client._fetch_tushare_minute_kbars",
        lambda *args, **kwargs: pytest.fail("Tushare should not be called when Xiaoshi returns rows"),
    )

    result = fetch_minute_kbars("000001", trade_date="20260701")

    assert result[0]["source"] == "xiaoshi_min1"
    assert result[0]["pre_close"] == pytest.approx(10.05)
