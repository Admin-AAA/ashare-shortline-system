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
        # LLM 是可选的风险 overlay：熔断、余额不足或超时只记录审计，候选继续
        # 交给聚合阶段按 strategy.yaml 规则信号降级处理，不能让正式推荐整批失败。
        rule_only_fallback_count = 0
        if not agent_review_disabled:
            for entry in candidate_results:
                reviews = list(entry.get("skill_reviews") or [])
                requested = len(entry.get("review_skill_ids") or reviews)
                succeeded = sum(
                    1
                    for review in reviews
                    if review.get("review") is not None and not review.get("error")
                )
                if requested > 0 and succeeded == 0:
                    rule_only_fallback_count += 1
        pipeline_audit = dict(data.pipeline_audit or {})
        pipeline_audit.update(
            {
                "skill_agent_call_count": review_failure_audit["requested_count"],
                "skill_review_success_count": review_failure_audit["success_count"],
                "skill_review_failure_audit": review_failure_audit,
                "rule_only_fallback_count": rule_only_fallback_count,
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
    "ReviewExecutorPort",
    "ReviewStage",
]
