from __future__ import annotations

from types import SimpleNamespace

from app.services.runs.contracts import RunPhase, parse_run_request
from app.services.runs.coordinator import RunContext, WorkerLease
from app.services.runs.stages.contracts import StageData
from app.services.runs.stages.persistence import (
    PersistenceStage,
    SealedRecommendationCollection,
)


class _PersistencePort:
    def __init__(self) -> None:
        self.calls = 0

    def persist(self, *, run_id: str, trade_date: str, cards):
        self.calls += 1
        assert run_id == "run-persistence-stage"
        assert trade_date == "2026-08-07"
        assert len(cards) == 1
        return SealedRecommendationCollection(
            recommendations=(SimpleNamespace(recommendation_id="rec-1"),),
            recommendation_row_count=1,
            collection_digest="a" * 64,
            side_effect_key="recommendation-seal:v1:run-persistence-stage:" + "a" * 64,
        )


def test_persistence_stage_uses_the_database_seal_side_effect_key() -> None:
    persistence = _PersistencePort()
    stage = PersistenceStage(recommendation_persistence=persistence)
    context = RunContext(
        run_id="run-persistence-stage",
        request=parse_run_request({}),
        worker_lease=WorkerLease("run-persistence-stage", "lease-token", 1),
    )

    output = stage.execute(
        context,
        StageData(
            snapshot={"trade_date": "2026-08-07"},
            cards=({"code": "000001"},),
        ),
    )

    assert persistence.calls == 1
    assert output.result.phase is RunPhase.PERSISTENCE
    assert output.result.side_effect_key.startswith(
        "recommendation-seal:v1:run-persistence-stage:"
    )
    assert len(output.data.saved_recommendations) == 1
