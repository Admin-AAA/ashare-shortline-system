"""Strategy evaluation stage: rule scan, skill gate, and candidate selection.

This stage runs the strategy rule scan across the full research context,
applies the skill rule gate to select review candidates, and produces
the prelim entries and pipeline audit for downstream review and aggregation.
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
# Ports
# ---------------------------------------------------------------------------

@runtime_checkable
class StrategyEvaluationPort(Protocol):
    """Run the full strategy evaluation: rule scan → skill gate → candidate selection."""

    def evaluate(
        self,
        *,
        research_contexts: Sequence[Any],
        selected_skills: Sequence[StageMapping],
        snapshot: StageMapping,
        params: StageMapping,
        prefilter_audit: StageMapping,
    ) -> tuple[
        tuple[StageMapping, ...],  # prelim_entries
        tuple[StageMapping, ...],  # skill_candidates
        StageMapping,              # skill_gate_audit
        StageMapping,              # pipeline_audit
    ]:
        """Return (prelim_entries, skill_candidates, skill_gate_audit, pipeline_audit)."""


# ---------------------------------------------------------------------------
# Stage implementation
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class StrategyEvaluationStage:
    """Evaluate strategies against the research context and gate review candidates."""

    phase: RunPhase = RunPhase.STRATEGY_EVALUATION
    strategy_evaluation: StrategyEvaluationPort | None = None

    def execute(self, context: RunContext, data: StageData) -> StageOutput:
        if self.strategy_evaluation is not None:
            prelim_entries, skill_candidates, skill_gate_audit, pipeline_audit = (
                self.strategy_evaluation.evaluate(
                    research_contexts=data.research_contexts,
                    selected_skills=data.selected_skills,
                    snapshot=data.snapshot or {},
                    params=data.params or {},
                    prefilter_audit=data.prefilter_audit or {},
                )
            )
        else:
            raise RuntimeError("StrategyEvaluationStage requires a StrategyEvaluationPort")

        pipeline_audit = dict(pipeline_audit)
        pipeline_audit["agent_review_disabled"] = bool(
            (data.params or {}).get("disable_agent_review")
        )

        result = RunStageResult(
            phase=RunPhase.STRATEGY_EVALUATION,
            side_effect_key=f"strategy_evaluation:{context.run_id}",
        )
        updated = data.with_updates(
            prelim_entries=prelim_entries,
            skill_candidates=skill_candidates,
            skill_gate_audit=skill_gate_audit,
            pipeline_audit=pipeline_audit,
        )
        return StageOutput(result=result, data=updated)


__all__ = [
    "StrategyEvaluationPort",
    "StrategyEvaluationStage",
]
