"""Persistence stage: idempotent recommendation writes and run completion.

This stage persists the final recommendation cards to the database using
an idempotent repository.  Persistence failure must not leave a completed
run or duplicate recommendations.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol, Sequence, runtime_checkable

from ..contracts import RunPhase, RunStageResult
from .contracts import StageData, StageMapping, StageOutput
from ..coordinator import RunContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Port
# ---------------------------------------------------------------------------

@runtime_checkable
class RecommendationPersistencePort(Protocol):
    """Idempotent recommendation persistence."""

    def persist(
        self,
        *,
        run_id: str,
        trade_date: str,
        cards: Sequence[StageMapping],
    ) -> tuple[Any, ...]:
        """Persist recommendation cards and return saved ORM objects.

        The repository must ensure idempotency by run_id / natural-key dedup.
        """


# ---------------------------------------------------------------------------
# Stage implementation
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PersistenceStage:
    """Persist recommendation cards with idempotent writes."""

    phase: RunPhase = RunPhase.PERSISTENCE
    recommendation_persistence: RecommendationPersistencePort | None = None

    def execute(self, context: RunContext, data: StageData) -> StageOutput:
        snapshot = data.snapshot or {}

        if self.recommendation_persistence is not None:
            saved = self.recommendation_persistence.persist(
                run_id=context.run_id,
                trade_date=str(snapshot.get("trade_date") or ""),
                cards=data.cards,
            )
        else:
            raise RuntimeError("PersistenceStage requires a RecommendationPersistencePort")

        result = RunStageResult(
            phase=RunPhase.PERSISTENCE,
            side_effect_key=f"persistence:{context.run_id}",
        )
        updated = data.with_updates(
            saved_recommendations=saved,
        )
        return StageOutput(result=result, data=updated)


__all__ = [
    "PersistenceStage",
    "RecommendationPersistencePort",
]
