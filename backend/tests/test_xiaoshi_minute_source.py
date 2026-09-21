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
    clear_session_cooldown,
    minute_bucket,
    pick_bucket_probes,
    resample_minute_rows,
    session_cooldown_seconds,
)
from app.services.xiaoshi_client import XiaoshiError


def _parquet_bytes(rows: list[dict]) -> bytes:
    frame = pd.DataFrame(rows)
    output = io.BytesIO()
    frame.to_parquet(output, engine="pyarrow", index=False)
    return output.getvalue()


def _file_info(
    key: str,
    content: bytes,
    url: str,
    *,
    first_date: str | None = None,
    last_date: str | None = None,
) -> dict:
    return {
        "key": key,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "url": url,
        "first_date": first_date,
        "last_date": last_date,
    }


def _minute_row(
    symbol: str,
    trade_time: str,
    *,
    open_price: float,
    close_price: float,
    volume: float,
    amount: float,
) -> dict:
    return {
        "market": "CN",
        "symbol": symbol,
        "trade_time": trade_time,
        "open": open_price,
        "high": max(open_price, close_price),
        "low": min(open_price, close_price),
        "close": close_price,
        "volume": volume,
        "amount": amount,
    }


def test_xiaoshi_minute_history_downloads_verifies_merges_and_caches(tmp_path: Path) -> None:
    base = _parquet_bytes(
        [
            _minute_row("000001", "2026-07-01 09:31:00", open_price=10, close_price=10, volume=100, amount=1000),
            _minute_row("000001", "2026-07-01 09:32:00", open_price=10, close_price=10.1, volume=200, amount=2000),
        ]
    )
    update = _parquet_bytes(
        [
            _minute_row("000001", "2026-07-01 09:32:00", open_price=20, close_price=20.5, volume=999, amount=9999),
            _minute_row("000002", "2026-07-01 09:32:00", open_price=1, close_price=1, volume=1, amount=1),
        ]
    )
    blobs = {"https://r2.test/base": base, "https://r2.test/update": update}
    files = [
        _file_info(
            "history/v2/releases/release-1/data/cn-minute/market=CN/symbol=000001/data.parquet",
            base,
            "https://r2.test/base",
            first_date="2026-06-01",
            last_date="2026-07-01",
        ),
        _file_info(
            "history/v2/releases/release-1/data/cn-minute/market=CN/year=2026/month=2026-07/bucket=a/delta.parquet",
            update,
            "https://r2.test/update",
            first_date="2026-07-01",
            last_date="2026-07-01",
        ),
    ]
    url_calls: list[dict] = []

    class FakeXiaoshiClient:
        def get_history_download_session(self, **params):
            url_calls.append(dict(params))
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
        assert url_calls == [
            {"dataset": "min1", "code": "000001", "since": "2026-07-01", "to": "2026-07-01"},
            {"dataset": "min1", "code": "000001", "since": "2026-07-01", "to": "2026-07-01"},
        ]
        cached = sorted(
            str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*.parquet")
        )
        assert cached == [
            "history/v2/releases/release-1/data/cn-minute/market=CN/symbol=000001/data.parquet",
            "history/v2/releases/release-1/data/cn-minute/market=CN/year=2026/month=2026-07/bucket=a/delta.parquet",
        ]
    finally:
        r2_client.close()


def test_xiaoshi_minute_history_skips_partitions_outside_requested_range(tmp_path: Path) -> None:
    base = _parquet_bytes(
        [_minute_row("000001", "2026-06-30 09:31:00", open_price=1, close_price=1, volume=1, amount=1)]
    )
    delta = _parquet_bytes(
        [_minute_row("000001", "2026-07-01 09:31:00", open_price=2, close_price=2, volume=2, amount=2)]
    )
    blobs = {"https://r2.test/base": base, "https://r2.test/delta": delta}
    files = [
        _file_info(
            "history/v2/releases/release-1/data/cn-minute/market=CN/symbol=000001/data.parquet",
            base,
            "https://r2.test/base",
            first_date="2020-01-02",
            last_date="2026-06-30",
        ),
        _file_info(
            "history/v2/releases/release-1/data/cn-minute/market=CN/year=2026/month=2026-07/bucket=a/delta.parquet",
            delta,
            "https://r2.test/delta",
            first_date="2026-07-01",
            last_date="2026-07-15",
        ),
    ]
    downloaded: list[str] = []

    class FakeXiaoshiClient:
        def get_history_download_session(self, **params):
            return {"files": files}

    def handler(request: httpx.Request) -> httpx.Response:
        downloaded.append(str(request.url))
        return httpx.Response(200, content=blobs[str(request.url)], request=request)

    r2_client = httpx.Client(transport=httpx.MockTransport(handler))
    source = XiaoshiMinuteHistorySource(
        client=FakeXiaoshiClient(),  # type: ignore[arg-type]
        cache_dir=tmp_path,
        r2_client=r2_client,
    )
    try:
        rows = source.fetch("000001", start_date="20260701", end_date="20260701")
    finally:
        r2_client.close()

    assert downloaded == ["https://r2.test/delta"]
    assert [row["trade_time"] for row in rows] == ["2026-07-01 09:31:00"]


def test_xiaoshi_minute_batch_requests_one_signature_per_bucket(tmp_path: Path) -> None:
    # 000001/000002/000003 分别落在 0/8/3 号分桶；同一分桶只申请一次签名。
    month_file = _parquet_bytes(
        [
            _minute_row("000001", "2026-07-01 09:31:00", open_price=1, close_price=1.1, volume=10, amount=11),
            _minute_row("000002", "2026-07-01 09:31:00", open_price=2, close_price=2.2, volume=20, amount=22),
            _minute_row("000003", "2026-07-01 09:31:00", open_price=3, close_price=3.3, volume=30, amount=33),
        ]
    )
    key = "history/v2/releases/release-1/data/cn-minute/market=CN/year=2026/month=2026-07/bucket=a/delta.parquet"
    file_info = _file_info(
        key,
        month_file,
        "https://r2.test/month",
        first_date="2026-07-01",
        last_date="2026-07-15",
    )
    downloads: list[str] = []
    session_calls: list[str] = []

    class FakeXiaoshiClient:
        def get_history_download_session(self, **params):
            session_calls.append(str(params["code"]))
            return {"files": [file_info]}

    def handler(request: httpx.Request) -> httpx.Response:
        downloads.append(str(request.url))
        return httpx.Response(200, content=month_file, request=request)

    r2_client = httpx.Client(transport=httpx.MockTransport(handler))
    source = XiaoshiMinuteHistorySource(
        client=FakeXiaoshiClient(),  # type: ignore[arg-type]
        cache_dir=tmp_path,
        r2_client=r2_client,
    )
    try:
        rows_by_code = source.fetch_many(
            ["000001", "000002", "000003"],
            start_date="20260701",
            end_date="20260701",
        )
    finally:
        r2_client.close()

    assert sorted(session_calls) == ["000001", "000002", "000003"]
    assert downloads == ["https://r2.test/month"]
    assert sorted(rows_by_code) == ["000001", "000002", "000003"]
    assert rows_by_code["000003"][0]["close"] == pytest.approx(3.3)


def test_xiaoshi_minute_batch_shares_one_bucket_for_same_bucket_codes(tmp_path: Path) -> None:
    # 600519 与 000014 同属 f 号分桶：两只股票只应触发一次签名申请。
    month_file = _parquet_bytes(
        [
            _minute_row("600519", "2026-07-01 09:31:00", open_price=1, close_price=1.1, volume=10, amount=11),
            _minute_row("000014", "2026-07-01 09:31:00", open_price=2, close_price=2.2, volume=20, amount=22),
        ]
    )
    file_info = _file_info(
        "history/v2/releases/release-1/data/cn-minute/market=CN/year=2026/month=2026-07/bucket=f/delta.parquet",
        month_file,
        "https://r2.test/bucket-f",
        first_date="2026-07-01",
        last_date="2026-07-15",
    )
    session_calls: list[str] = []

    class FakeXiaoshiClient:
        def get_history_download_session(self, **params):
            session_calls.append(str(params["code"]))
            return {"files": [file_info]}

    r2_client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=month_file, request=request)
        )
    )
    source = XiaoshiMinuteHistorySource(
        client=FakeXiaoshiClient(),  # type: ignore[arg-type]
        cache_dir=tmp_path,
        r2_client=r2_client,
    )
    try:
        rows_by_code = source.fetch_many(
            ["600519", "000014"],
            start_date="20260701",
            end_date="20260701",
        )
    finally:
        r2_client.close()

    assert session_calls == ["600519"]
    assert rows_by_code["000014"][0]["close"] == pytest.approx(2.2)


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


def test_minute_bucket_follows_symbol_sha1_prefix() -> None:
    assert minute_bucket("000001") == "0"
    assert minute_bucket("600519") == "f"
    assert minute_bucket("000014.SZ") == "f"


def test_pick_bucket_probes_returns_one_symbol_per_bucket() -> None:
    probes = pick_bucket_probes(
        {"0", "8", "f"},
        ["600519", "000001", "000002", "000014"],
    )

    assert probes == {"0": "000001", "8": "000002", "f": "600519"}


def test_xiaoshi_minute_source_cools_down_after_rate_limit(tmp_path: Path) -> None:
    class RateLimitedClient:
        def get_history_download_session(self, **params):
            from app.services.xiaoshi_client import XiaoshiError

            raise XiaoshiError(
                "Xiaoshi HTTP 429",
                status_code=429,
                path="/api/v3/history/download-session",
                retry_after_seconds=240.0,
            )

    r2_client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(500, request=request))
    )
    source = XiaoshiMinuteHistorySource(
        client=RateLimitedClient(),  # type: ignore[arg-type]
        cache_dir=tmp_path,
        r2_client=r2_client,
    )
    clear_session_cooldown()
    try:
        with pytest.raises(XiaoshiError):
            source._request_session("000001", "2026-07-01", "2026-07-01")
        assert session_cooldown_seconds() > 0
        with pytest.raises(XiaoshiMinuteSourceError, match="限流"):
            source._request_session("000002", "2026-07-01", "2026-07-01")
    finally:
        clear_session_cooldown()
        source.close()


def test_xiaoshi_minute_source_rejects_invalid_ohlc(tmp_path: Path) -> None:
    content = _parquet_bytes(
        [
            {
                "symbol": "000001",
                "open": 10,
                "high": 9,
                "low": 9,
                "close": 10,
                "volume": 1,
                "amount": 1,
                "trade_time": "2026-07-01 09:31:00",
            }
        ]
    )
    file_info = _file_info(
        "history/v2/releases/release-1/data/cn-minute/market=CN/symbol=000001/data.parquet",
        content,
        "https://r2.test/base",
        first_date="2020-01-02",
        last_date="2026-07-01",
    )

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


def test_minute_batch_limits_per_code_fallback_when_all_shards_missing(monkeypatch) -> None:
    from app.services import tushare_client

    codes = [f"{index:06d}" for index in range(80)]
    monkeypatch.setattr(
        tushare_client,
        "_fetch_xiaoshi_minute_kbars_batch",
        lambda *args, **kwargs: ({}, set(codes), "小石分钟历史取数失败"),
    )
    fallback_codes: list[str] = []

    def _fake_single(code, **kwargs):
        fallback_codes.append(code)
        return []

    monkeypatch.setattr(
        tushare_client,
        "fetch_minute_kbars",
        _fake_single,
    )
    audit: dict = {}

    rows_by_code = tushare_client.fetch_minute_kbars_batch(
        codes,
        trade_date="2026-09-14",
        audit=audit,
    )

    assert rows_by_code == {code: [] for code in codes}
    assert len(fallback_codes) == tushare_client.MINUTE_FALLBACK_MAX_CODES
    assert audit["fallback_limited"] is True
    assert audit["unresolved_code_count"] == len(codes)
    assert audit["provider_note"] == "小石分钟历史取数失败"
