"""Prepare stage: validate RunRequest, sync skills, persist run start.

This is the first stage in the recommendation pipeline.  It resolves the
canonical ``RunRequest``, synchronizes skill definitions, selects the active
skill set for the run, computes the execution version, and persists the initial
run record.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol, Sequence, runtime_checkable

from ..contracts import RunPhase, RunRequest, RunStageResult
from .contracts import StageData, StageMapping, StageOutput
from ..coordinator import RunContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Ports — constructor-injected capabilities that isolate the stage from
# ORM, vendor SDKs and module-level global state.
# ---------------------------------------------------------------------------

@runtime_checkable
class SkillResolutionPort(Protocol):
    """Resolve and validate the skill set that participates in a run."""

    def sync_and_select(
        self,
        *,
        skill_ids: Sequence[dict[str, Any]],
        run_mode: str | None = None,
        preset_id: str | None = None,
    ) -> tuple[tuple[StageMapping, ...], dict[str, Any]]:
        """Return (selected_skills, skill_map) for the given request skills.

        ``selected_skills`` is the tuple consumed by downstream stages and
        ``skill_map`` maps skill_id → skill object for legacy consumers.
        """


@runtime_checkable
class RunStartPort(Protocol):
    """Persist the initial run record and its canonical request."""

    def persist_run_start(
        self,
        run_id: str,
        params: StageMapping,
        selected_skills: Sequence[StageMapping],
        *,
        request: RunRequest,
        execution_version: str | None,
        execution_audit: StageMapping | None = None,
    ) -> None:
        """Write the queued/running row with all canonical request columns."""


@runtime_checkable
class ExecutionVersionPort(Protocol):
    """Compute the deterministic execution-version fingerprint."""

    def compute(
        self,
        request: RunRequest,
        selected_skills: Sequence[StageMapping],
    ) -> tuple[str, StageMapping]:
        """Return (execution_version, execution_audit)."""


# ---------------------------------------------------------------------------
# Stage implementation
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PrepareStage:
    """Resolve the run request, select skills, and persist the run start."""

    phase: RunPhase = RunPhase.PREPARE
    skill_resolution: SkillResolutionPort | None = None
    run_start: RunStartPort | None = None
    execution_version: ExecutionVersionPort | None = None

    def execute(self, context: RunContext, data: StageData) -> StageOutput:
        request = context.request
        params = request.model_dump(mode="json")

        # Resolve skills through the injected port.
        if self.skill_resolution is not None:
            selected_skills, _skill_map = self.skill_resolution.sync_and_select(
                skill_ids=params.get("skills") or [],
                run_mode=str(params.get("run_mode") or ""),
                preset_id=str(params.get("preset_id") or "") or None,
            )
        else:
            # Fallback: no skill resolution wired — produce empty set.
            logger.warning("PrepareStage has no SkillResolutionPort; skill selection skipped")
            selected_skills = ()

        if not selected_skills:
            raise ValueError("没有可运行的 Skill，请检查预设配置或 Skill 状态")

        # Compute execution version through the injected port.
        exec_version: str | None = None
        exec_audit: StageMapping | None = None
        if self.execution_version is not None:
            exec_version, exec_audit = self.execution_version.compute(request, selected_skills)

        # Persist the run start through the injected port.
        if self.run_start is not None:
            self.run_start.persist_run_start(
                context.run_id,
                params,
                selected_skills,
                request=request,
                execution_version=exec_version,
                execution_audit=exec_audit,
            )

        result = RunStageResult(
            phase=RunPhase.PREPARE,
            side_effect_key=f"prepare:{context.run_id}",
        )
        updated = data.with_updates(
            params=params,
            selected_skills=tuple(selected_skills),
            execution_version=exec_version,
            execution_audit=exec_audit,
        )
        return StageOutput(result=result, data=updated)


__all__ = [
    "ExecutionVersionPort",
    "PrepareStage",
    "RunStartPort",
    "SkillResolutionPort",
]
