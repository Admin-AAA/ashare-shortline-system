"""Aggregation stage: build recommendation cards and apply pool rules.

This stage takes the reviewed candidate results, builds recommendation cards
with scored signals, and applies pool rules (score line, output count, sector
diversity, deduplication).
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
class RecommendationAssemblyPort(Protocol):
    """Build recommendation cards and apply pool rules."""

    def aggregate(
        self,
        *,
        candidate_results: Sequence[StageMapping],
        selected_skills: Sequence[StageMapping],
        snapshot: StageMapping,
        params: StageMapping,
    ) -> tuple[tuple[StageMapping, ...], tuple[StageMapping, ...], StageMapping]:
        """Return (cards, scored_cards, pipeline_audit_updates)."""


# ---------------------------------------------------------------------------
# Stage implementation
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class AggregationStage:
    """Build scored recommendation cards and apply pool rules."""

    phase: RunPhase = RunPhase.AGGREGATION
    recommendation_assembly: RecommendationAssemblyPort | None = None

    def execute(self, context: RunContext, data: StageData) -> StageOutput:
        if self.recommendation_assembly is not None:
            cards, scored_cards, pipeline_updates = self.recommendation_assembly.aggregate(
                candidate_results=data.candidate_results,
                selected_skills=data.selected_skills,
                snapshot=data.snapshot or {},
                params=data.params or {},
            )
        else:
            raise RuntimeError("AggregationStage requires a RecommendationAssemblyPort")

        result = RunStageResult(
            phase=RunPhase.AGGREGATION,
            side_effect_key=f"aggregation:{context.run_id}",
        )
        updated = data.with_updates(
            cards=cards,
            scored_cards=scored_cards,
        )
        return StageOutput(result=result, data=updated)


__all__ = [
    "AggregationStage",
    "RecommendationAssemblyPort",
]
