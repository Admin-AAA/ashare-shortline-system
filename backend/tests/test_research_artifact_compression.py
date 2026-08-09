from __future__ import annotations

import json

import pytest

from app.services.backtest.research_runner import (
    ARTIFACT_CONTENT_ENCODING,
    SnapshotCaptureError,
    _decode_artifact_payload,
    _encode_artifact_payload,
)


def test_compressed_artifact_round_trip_is_smaller_and_deterministic() -> None:
    payload = {
        "schema_version": 1,
        "bars_by_code": {
            f"{index:06d}": [
                {
                    "trade_date": f"2026-01-{day:02d}",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.8,
                    "close": 10.2,
                    "volume": 100000.0,
                }
                for day in range(1, 11)
            ]
            for index in range(100)
        },
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()

    first = _encode_artifact_payload(payload)
    second = _encode_artifact_payload(payload)

    assert ARTIFACT_CONTENT_ENCODING == "zlib+json"
    assert first == second
    assert len(first) < len(raw) * 0.25
    assert _decode_artifact_payload(first, "compressed") == payload


def test_decoder_remains_compatible_with_legacy_plain_json() -> None:
    raw = b'{"schema_version":1,"stocks":[]}'

    assert _decode_artifact_payload(raw, "legacy") == {
        "schema_version": 1,
        "stocks": [],
    }


def test_corrupt_compressed_artifact_fails_closed() -> None:
    payload = _encode_artifact_payload({"schema_version": 1})[:-3]

    with pytest.raises(SnapshotCaptureError, match="canonical UTF-8 JSON"):
        _decode_artifact_payload(payload, "corrupt")
