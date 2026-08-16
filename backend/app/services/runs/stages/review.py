"""Review stage: LLM-based skill review with concurrency control.

This is the only async stage in the pipeline.  It calls the LLM review
executor for each candidate with bounded concurrency, records API costs,
and produces the reviewed candidate results for aggregation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol, Sequence, runtime_checkable

from ..contracts import RunMode, RunPhase, RunStageResult
from .contracts import StageData, StageMapping, StageOutput
from ..coordinator import AsyncRunStage, RunContext

logger = logging.getLogger(__name__)


class FormalReviewUnavailableError(RuntimeError):
    """Formal review had work to do but no model review succeeded."""

    def __init__(self, audit: StageMapping) -> None:
        self.audit = dict(audit)
        requested = int(audit.get("requested_count") or 0)
        succeeded = int(audit.get("success_count") or 0)
        reasons = dict(audit.get("reason_counts") or {})
        reason_text = ", ".join(
            f"{reason}={count}"
            for reason, count in sorted(reasons.items())
        ) or "unknown"
        super().__init__(
            f"正式推荐 LLM 深审不可用：成功 {succeeded}/{requested}；原因 {reason_text}"
        )


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

    def record_costs(self, run_id: str) -> tuple[StageMapping, ...]:
        """Drain/persist accumulated API costs and return the audit rows."""


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
        from app.core.config import get_settings
        from app.services.runs.skill_gate import resolve_skill_review_concurrency

        params = data.params or {}
        agent_review_disabled = bool(params.get("disable_agent_review"))
        concurrency = resolve_skill_review_concurrency(dict(params), get_settings())
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

        api_costs: tuple[StageMapping, ...] = ()
        if self.cost_recording is not None:
            api_costs = self.cost_recording.record_costs(context.run_id)

        from app.services.runs.filter_audit import skill_review_failure_audit

        review_failure_audit = skill_review_failure_audit(
            [dict(entry) for entry in candidate_results],
            [dict(cost) for cost in api_costs],
        )
        if (
            context.request.run_mode is RunMode.FORMAL
            and not agent_review_disabled
            and int(review_failure_audit["requested_count"] or 0) > 0
            and int(review_failure_audit["success_count"] or 0) == 0
        ):
            raise FormalReviewUnavailableError(review_failure_audit)
        pipeline_audit = dict(data.pipeline_audit or {})
        pipeline_audit.update(
            {
                "skill_agent_call_count": review_failure_audit["requested_count"],
                "skill_review_success_count": review_failure_audit["success_count"],
                "skill_review_failure_audit": review_failure_audit,
                "rule_only_fallback_count": 0,
            }
        )

        result = RunStageResult(
            phase=RunPhase.REVIEW,
            side_effect_key=f"review:{context.run_id}",
        )
        updated = data.with_updates(
            candidate_results=candidate_results,
            api_costs=api_costs,
            skill_review_failure_audit=review_failure_audit,
            pipeline_audit=pipeline_audit,
        )
        return StageOutput(result=result, data=updated)


__all__ = [
    "CostRecordingPort",
    "FormalReviewUnavailableError",
    "ReviewExecutorPort",
    "ReviewStage",
]
