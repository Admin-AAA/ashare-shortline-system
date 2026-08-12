from __future__ import annotations

import json
from types import SimpleNamespace

import app.services.backtest.research_runner as research_runner
from app.engine.backtest import (
    ArtifactRef,
    ArtifactScope,
    BarInput,
    ExecutionPolicy,
    OutcomeInput,
    RecommendationInput,
)
from app.engine.simulation import simulate


class _ArtifactRepository:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.reads: list[tuple[str, str]] = []

    def get_artifact(self, artifact_id: str, *, expected_content_hash: str):
        self.reads.append((artifact_id, expected_content_hash))
        return SimpleNamespace(payload=self.payload)


def _outcome_payload() -> bytes:
    bars_by_code = {}
    for code in ("000001", "000002"):
        bars_by_code[code] = [
            {
                "code": code,
                "trade_date": f"2026-01-{day:02d}",
                "open": 10.0,
                "high": 11.0,
                "low": 9.8,
                "close": 10.8,
                "volume": 1000.0,
                "tradable": True,
            }
            for day in range(3, 9)
        ]
    return json.dumps({"bars_by_code": bars_by_code}, sort_keys=True).encode("utf-8")


def test_same_signal_date_outcome_payload_is_decoded_once_and_trades_match(monkeypatch) -> None:
    payload = _outcome_payload()
    artifact = ArtifactRef.from_content(
        artifact_id="bta_outcome_fixture",
        signal_date="2026-01-02",
        artifact_scope=ArtifactScope.OUTCOME_INPUT,
        artifact_type="daily_kbars_execution_input",
        schema_version=1,
        row_count=12,
        payload=payload,
    )
    repository = _ArtifactRepository(payload)
    decode_count = 0
    original_decode = research_runner._decode_artifact_payload

    def counted_decode(raw_payload: bytes, artifact_id: str):
        nonlocal decode_count
        decode_count += 1
        return original_decode(raw_payload, artifact_id)

    monkeypatch.setattr(research_runner, "_decode_artifact_payload", counted_decode)
    cache = research_runner._OutcomeArtifactPayloadCache(repository)
    policy = ExecutionPolicy(horizon_days=2)

    cached_inputs: list[OutcomeInput] = []
    cached_trades = []
    for code in ("000001", "000002"):
        outcome = research_runner.ArtifactOutcomeDataReader(
            repository,
            artifact,
            code=code,
            payload_cache=cache,
        ).read("2026-01-02", policy)
        cached_inputs.append(outcome)
        cached_trades.append(
            simulate(
                RecommendationInput(code=code, signal_date="2026-01-02", signal_price=10.0),
                outcome.bars,
                policy,
            ).trade.canonical_payload()
        )

    assert repository.reads == [(artifact.artifact_id, artifact.content_hash)]
    assert decode_count == 1

    uncached_inputs = [
        research_runner.ArtifactOutcomeDataReader(repository, artifact, code=code).read(
            "2026-01-02", policy
        )
        for code in ("000001", "000002")
    ]
    uncached_trades = [
        simulate(
            RecommendationInput(code=code, signal_date="2026-01-02", signal_price=10.0),
            outcome.bars,
            policy,
        ).trade.canonical_payload()
        for code, outcome in zip(("000001", "000002"), uncached_inputs)
    ]

    assert [item.canonical_payload() for item in cached_inputs] == [
        item.canonical_payload() for item in uncached_inputs
    ]
    assert cached_trades == uncached_trades
    assert len(repository.reads) == 3
    assert decode_count == 3

    cache.clear()
    research_runner.ArtifactOutcomeDataReader(
        repository,
        artifact,
        code="000001",
        payload_cache=cache,
    ).read("2026-01-02", policy)
    assert len(repository.reads) == 4
    assert decode_count == 4
