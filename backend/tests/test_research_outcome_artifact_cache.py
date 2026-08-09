from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from app.engine.backtest import ArtifactRef, ArtifactScope, ExecutionPolicy
from app.services.backtest import research_runner
from app.services.backtest.research_runner import ArtifactOutcomeDataReader


class _CountingArtifactRepository:
    def __init__(self, payload_by_id: dict[str, bytes]) -> None:
        self._payload_by_id = payload_by_id
        self.read_count = 0

    def get_artifact(
        self,
        artifact_id: str,
        *,
        expected_content_hash: str | None = None,
    ) -> SimpleNamespace:
        del expected_content_hash
        self.read_count += 1
        return SimpleNamespace(payload=self._payload_by_id[artifact_id])


def _outcome_artifact(artifact_id: str, payload: bytes) -> ArtifactRef:
    return ArtifactRef.from_content(
        artifact_id=artifact_id,
        signal_date="2026-07-01",
        artifact_scope=ArtifactScope.OUTCOME_INPUT,
        artifact_type="daily_outcome_bars",
        schema_version=1,
        row_count=2,
        payload=payload,
    )


def test_outcome_reader_reuses_one_verified_decode_for_multiple_codes() -> None:
    payload = json.dumps(
        {
            "bars_by_code": {
                "000001": [
                    {
                        "code": "000001",
                        "trade_date": "2026-07-02",
                        "open": 10.0,
                        "high": 10.8,
                        "low": 9.8,
                        "close": 10.5,
                    }
                ],
                "000002": [
                    {
                        "code": "000002",
                        "trade_date": "2026-07-02",
                        "open": 20.0,
                        "high": 20.6,
                        "low": 19.5,
                        "close": 20.2,
                    }
                ],
            }
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    artifact = _outcome_artifact("artifact-a", payload)
    repository = _CountingArtifactRepository({artifact.artifact_id: payload})
    cache = research_runner._ArtifactOutcomePayloadCache(repository)

    with patch(
        "app.services.backtest.research_runner._decode_artifact_payload",
        wraps=research_runner._decode_artifact_payload,
    ) as decode:
        first = ArtifactOutcomeDataReader(
            repository,
            artifact,
            code="000001",
            payload_cache=cache,
        ).read("2026-07-01", ExecutionPolicy())
        second = ArtifactOutcomeDataReader(
            repository,
            artifact,
            code="000002",
            payload_cache=cache,
        ).read("2026-07-01", ExecutionPolicy())

    assert repository.read_count == 1
    assert decode.call_count == 1
    assert [bar.code for bar in first.bars] == ["000001"]
    assert [bar.code for bar in second.bars] == ["000002"]


def test_outcome_reader_cache_key_does_not_alias_distinct_artifacts() -> None:
    payload = json.dumps({"bars_by_code": {}}, separators=(",", ":")).encode("utf-8")
    first_artifact = _outcome_artifact("artifact-a", payload)
    second_artifact = _outcome_artifact("artifact-b", payload)
    repository = _CountingArtifactRepository(
        {
            first_artifact.artifact_id: payload,
            second_artifact.artifact_id: payload,
        }
    )
    cache = research_runner._ArtifactOutcomePayloadCache(repository)

    ArtifactOutcomeDataReader(
        repository,
        first_artifact,
        code="000001",
        payload_cache=cache,
    ).read("2026-07-01", ExecutionPolicy())
    ArtifactOutcomeDataReader(
        repository,
        second_artifact,
        code="000001",
        payload_cache=cache,
    ).read("2026-07-01", ExecutionPolicy())

    assert repository.read_count == 2
