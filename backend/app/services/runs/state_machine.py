"""Pure, explicit lifecycle rules for recommendation pipeline runs.

The state machine is the only owner of lifecycle legality.  Repository adapters apply
its decisions with compare-and-set writes; workers, queues and API handlers do not
invent status transitions themselves.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from app.services.runs.contracts import (
    RUN_PHASE_ORDER,
    RunActor,
    RunErrorCode,
    RunPhase,
    RunStatus,
    RunTransition,
    RunTrigger,
)


class RunStateTransitionError(ValueError):
    """A rejected lifecycle action with a stable error code."""

    def __init__(self, code: RunErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class TransitionRule:
    """One fully declared transition in the run lifecycle table.

    ``recovery_condition`` is intentionally descriptive metadata.  The executable
    check is performed by :meth:`RunStateMachine.transition`, while the repository
    enforces the same lease requirement in its SQL CAS predicate.
    """

    expected_status: RunStatus
    trigger: RunTrigger
    target_status: RunStatus
    actors: frozenset[RunActor]
    default_error_code: RunErrorCode | None = None
    target_phase: RunPhase | None = None
    terminal: bool = False
    requires_checkpoint: bool = False
    requires_active_lease: bool = False
    recovery_condition: str | None = None

    @property
    def stable_error_code(self) -> RunErrorCode | None:
        """Canonical name for the legacy ``default_error_code`` field."""
        return self.default_error_code


@dataclass(frozen=True, slots=True)
class ResumeCheck:
    """Repository-derived facts required before an interrupted run can resume."""

    last_committed_phase: RunPhase | None
    side_effect_key: str | None
    has_active_lease: bool

    @property
    def has_valid_checkpoint(self) -> bool:
        return (
            self.last_committed_phase in RUN_PHASE_ORDER
            and bool(self.side_effect_key and self.side_effect_key.strip())
        )


_TERMINAL_STATUSES: Final[frozenset[RunStatus]] = frozenset(
    {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
)

# This table is intentionally one row per supported trigger/status pair.  Keep actor,
# target, terminality, recovery precondition and stable error code together so a new
# caller cannot accidentally create a legal-looking but incomplete transition.
TRANSITION_RULES: Final[tuple[TransitionRule, ...]] = (
    TransitionRule(
        RunStatus.QUEUED,
        RunTrigger.START,
        RunStatus.RUNNING,
        frozenset({RunActor.DISPATCHER}),
    ),
    TransitionRule(
        RunStatus.QUEUED,
        RunTrigger.SPAWN_FAILED,
        RunStatus.FAILED,
        frozenset({RunActor.DISPATCHER}),
        RunErrorCode.WORKER_SPAWN_FAILED,
        terminal=True,
    ),
    TransitionRule(
        RunStatus.QUEUED,
        RunTrigger.CANCEL,
        RunStatus.CANCELLED,
        frozenset({RunActor.API, RunActor.OPERATOR}),
        RunErrorCode.RUN_CANCELLED,
        terminal=True,
    ),
    TransitionRule(
        RunStatus.RUNNING,
        RunTrigger.SPAWN_FAILED,
        RunStatus.FAILED,
        frozenset({RunActor.DISPATCHER}),
        RunErrorCode.WORKER_SPAWN_FAILED,
        terminal=True,
    ),
    TransitionRule(
        RunStatus.RUNNING,
        RunTrigger.STAGE_FAILED,
        RunStatus.FAILED,
        frozenset({RunActor.WORKER}),
        RunErrorCode.STAGE_EXECUTION_FAILED,
        terminal=True,
        requires_active_lease=True,
    ),
    TransitionRule(
        RunStatus.RUNNING,
        RunTrigger.COMPLETE,
        RunStatus.COMPLETED,
        frozenset({RunActor.WORKER}),
        target_phase=RunPhase.COMPLETED,
        terminal=True,
        requires_checkpoint=True,
        requires_active_lease=True,
    ),
    TransitionRule(
        RunStatus.RUNNING,
        RunTrigger.HEARTBEAT_TIMEOUT,
        RunStatus.INTERRUPTED,
        frozenset({RunActor.WATCHDOG}),
        RunErrorCode.HEARTBEAT_TIMEOUT,
        recovery_condition="checkpoint_and_no_active_lease",
    ),
    TransitionRule(
        RunStatus.RUNNING,
        RunTrigger.CANCEL,
        RunStatus.CANCELLED,
        frozenset({RunActor.API, RunActor.OPERATOR, RunActor.WORKER}),
        RunErrorCode.RUN_CANCELLED,
        terminal=True,
    ),
    TransitionRule(
        RunStatus.INTERRUPTED,
        RunTrigger.RESUME,
        RunStatus.RUNNING,
        frozenset({RunActor.API, RunActor.OPERATOR}),
        recovery_condition="checkpoint_and_no_active_lease",
    ),
    TransitionRule(
        RunStatus.INTERRUPTED,
        RunTrigger.REPLAY,
        RunStatus.QUEUED,
        frozenset({RunActor.WORKER, RunActor.OPERATOR}),
        recovery_condition="immutable_request_full_replay",
    ),
    TransitionRule(
        RunStatus.INTERRUPTED,
        RunTrigger.CANCEL,
        RunStatus.CANCELLED,
        frozenset({RunActor.API, RunActor.OPERATOR}),
        RunErrorCode.RUN_CANCELLED,
        terminal=True,
    ),
)

_RULE_BY_STATUS_AND_TRIGGER: Final[dict[tuple[RunStatus, RunTrigger], TransitionRule]] = {
    (rule.expected_status, rule.trigger): rule for rule in TRANSITION_RULES
}


class RunStateMachine:
    """The only source of truth for lifecycle and checkpoint ordering rules."""

    terminal_statuses: Final[frozenset[RunStatus]] = _TERMINAL_STATUSES

    @staticmethod
    def initial_phase() -> RunPhase:
        return RUN_PHASE_ORDER[0]

    @staticmethod
    def is_terminal(status: RunStatus) -> bool:
        return status in _TERMINAL_STATUSES

    @staticmethod
    def legal_transitions(
        status: RunStatus,
        *,
        actor: RunActor | None = None,
    ) -> tuple[TransitionRule, ...]:
        """List permitted rules, optionally narrowed to one actor."""
        return tuple(
            rule
            for rule in TRANSITION_RULES
            if rule.expected_status is status and (actor is None or actor in rule.actors)
        )

    @staticmethod
    def next_phase(last_committed_phase: RunPhase | None) -> RunPhase | None:
        """Return the one phase that may execute after a committed checkpoint."""
        if last_committed_phase is None:
            return RunStateMachine.initial_phase()
        if last_committed_phase not in RUN_PHASE_ORDER:
            raise RunStateTransitionError(
                RunErrorCode.RESUME_REQUIRES_VALID_CHECKPOINT,
                f"{last_committed_phase.value} 不能作为运行 checkpoint",
            )
        current_index = RUN_PHASE_ORDER.index(last_committed_phase)
        if current_index == len(RUN_PHASE_ORDER) - 1:
            return None
        return RUN_PHASE_ORDER[current_index + 1]

    @staticmethod
    def require_next_checkpoint(
        *,
        last_committed_phase: RunPhase | None,
        phase: RunPhase,
    ) -> None:
        """Reject skipped, duplicated, terminal, or out-of-order checkpoints."""
        expected_phase = RunStateMachine.next_phase(last_committed_phase)
        if expected_phase is not phase:
            expected_name = expected_phase.value if expected_phase else "完成转换"
            raise RunStateTransitionError(
                RunErrorCode.CHECKPOINT_OUT_OF_ORDER,
                f"checkpoint 阶段必须是 {expected_name}，不能提交 {phase.value}",
            )

    @staticmethod
    def validate_resume(resume: ResumeCheck) -> RunPhase | None:
        """Validate resume preconditions and return the next executable phase."""
        if resume.has_active_lease:
            raise RunStateTransitionError(
                RunErrorCode.RESUME_REQUIRES_NO_ACTIVE_LEASE,
                "仍存在活跃 worker lease，不能并发恢复运行",
            )
        if not resume.has_valid_checkpoint:
            raise RunStateTransitionError(
                RunErrorCode.RESUME_REQUIRES_VALID_CHECKPOINT,
                "恢复运行需要已原子提交的阶段和 side_effect_key",
            )
        return RunStateMachine.next_phase(resume.last_committed_phase)

    @staticmethod
    def transition(
        *,
        current_status: RunStatus,
        trigger: RunTrigger,
        actor: RunActor,
        resume: ResumeCheck | None = None,
        last_committed_phase: RunPhase | None = None,
        side_effect_key: str | None = None,
        error_message: str | None = None,
        stable_error_code: str | None = None,
    ) -> RunTransition:
        """Validate one lifecycle action and return a CAS-ready transition."""
        rule = _RULE_BY_STATUS_AND_TRIGGER.get((current_status, trigger))
        if rule is None:
            raise RunStateTransitionError(
                RunErrorCode.INVALID_STATUS_TRANSITION,
                f"不允许从 {current_status.value} 触发 {trigger.value}",
            )
        if actor not in rule.actors:
            raise RunStateTransitionError(
                RunErrorCode.TRANSITION_ACTOR_NOT_ALLOWED,
                f"{actor.value} 无权从 {current_status.value} 触发 {trigger.value}",
            )

        if trigger is RunTrigger.RESUME:
            if resume is None:
                raise RunStateTransitionError(
                    RunErrorCode.RESUME_REQUIRES_VALID_CHECKPOINT,
                    "resume 必须提供 repository 校验过的 checkpoint",
                )
            RunStateMachine.validate_resume(resume)

        if rule.requires_checkpoint:
            if last_committed_phase is not RunPhase.PERSISTENCE or not (side_effect_key or "").strip():
                raise RunStateTransitionError(
                    RunErrorCode.COMPLETION_REQUIRES_PERSISTENCE_CHECKPOINT,
                    "完成运行前必须提交 persistence checkpoint 与 side_effect_key",
                )

        error_code = stable_error_code or (
            rule.stable_error_code.value if rule.stable_error_code is not None else None
        )
        return RunTransition(
            expected_status=current_status,
            target_status=rule.target_status,
            trigger=trigger,
            actor=actor,
            target_phase=rule.target_phase,
            stable_error_code=error_code,
            error_message=error_message,
            terminal=rule.terminal,
            requires_active_lease=rule.requires_active_lease,
            recovery_condition=rule.recovery_condition,
        )


__all__ = [
    "ResumeCheck",
    "RunStateMachine",
    "RunStateTransitionError",
    "TRANSITION_RULES",
    "TransitionRule",
]
