"""Review stage: LLM-based skill review with concurrency control.

This is the only async stage in the pipeline.  It calls the LLM review
executor for each candidate with bounded concurrency, records API costs,
and produces the reviewed candidate results for aggregation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol, Sequence, runtime_checkable

from ..contracts import RunPhase, RunStageResult
from .contracts import StageData, StageMapping, StageOutput
from ..coordinator import AsyncRunStage, RunContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Ports
# ---------------------------------------------------------------------------

@runtime_checkable
class ReviewExecutorPort(Protocol):
    """Execute LLM-based skill reviews for a batch of candidates."""

    async def review_candidates(
        self,
        candidates: Sequence[StageMapping],
        *,
        selected_skills: Sequence[StageMapping],
        snapshot: StageMapping,
        session_id: str,
        concurrency: int,
        agent_review_disabled: bool,
    ) -> tuple[StageMapping, ...]:
        """Return reviewed candidate entries with skill_reviews attached."""


@runtime_checkable
class CostRecordingPort(Protocol):
    """Record API call costs after the review batch completes."""

    def record_costs(self, run_id: str) -> None:
        """Drain and persist accumulated API costs for the run."""


# ---------------------------------------------------------------------------
# Stage implementation
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ReviewStage:
    """Run LLM skill reviews with bounded concurrency.

    Implements :class:`AsyncRunStage` because the underlying LLM calls are
    async and require an ``asyncio.Semaphore`` for concurrency control.
    """

    phase: RunPhase = RunPhase.REVIEW
    review_executor: ReviewExecutorPort | None = None
    cost_recording: CostRecordingPort | None = None

    async def execute(self, context: RunContext, data: StageData) -> StageOutput:
        params = data.params or {}
        agent_review_disabled = bool(params.get("disable_agent_review"))
        concurrency = max(1, int(params.get("agent_review_concurrency") or 1))
        session_id = f"sess_{context.run_id}"

        if self.review_executor is not None:
            candidate_results = await self.review_executor.review_candidates(
                data.skill_candidates,
                selected_skills=data.selected_skills,
                snapshot=data.snapshot or {},
                session_id=session_id,
                concurrency=concurrency,
                agent_review_disabled=agent_review_disabled,
            )
        else:
            raise RuntimeError("ReviewStage requires a ReviewExecutorPort")

        if self.cost_recording is not None:
            self.cost_recording.record_costs(context.run_id)

        result = RunStageResult(
            phase=RunPhase.REVIEW,
            side_effect_key=f"review:{context.run_id}",
        )
        updated = data.with_updates(
            candidate_results=candidate_results,
        )
        return StageOutput(result=result, data=updated)


__all__ = [
    "CostRecordingPort",
    "ReviewExecutorPort",
    "ReviewStage",
]
