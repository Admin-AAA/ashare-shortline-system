"""Orthogonal governance state machines.

Code_State, Environment_Acceptance_State, Operation_Execution_State, and
Production_Activation_State are deliberately independent: a Layer 1 transition
can never manufacture an environment acceptance, and an environment acceptance
can never manufacture a production activation.  These helpers only validate
transitions; persistence stays with the store/events layer.
"""
from __future__ import annotations

from enum import StrEnum

from app.governance.envelope import GovernanceError


class CodeState(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    CODE_COMPLETE = "CODE_COMPLETE"


class EnvironmentAcceptanceState(StrEnum):
    NOT_RUN = "NOT_RUN"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    ACCEPTED = "ACCEPTED"


class OperationExecutionState(StrEnum):
    PLANNED = "PLANNED"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"
    AUTHORIZED_NOT_EXECUTED = "AUTHORIZED_NOT_EXECUTED"
    EXECUTED = "EXECUTED"
    ACCEPTED = "ACCEPTED"
    FAILED = "FAILED"
    NOT_AUTHORIZED_NOT_EXECUTED = "NOT_AUTHORIZED_NOT_EXECUTED"


class ProductionActivationState(StrEnum):
    NOT_AUTHORIZED = "NOT_AUTHORIZED"
    AUTHORIZED_NOT_ACTIVE = "AUTHORIZED_NOT_ACTIVE"
    ACTIVE = "ACTIVE"
    ROLLED_BACK = "ROLLED_BACK"


class SpecCompletionState(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    COMPLETE = "COMPLETE"


_CODE_TRANSITIONS: dict[CodeState, frozenset[CodeState]] = {
    CodeState.NOT_STARTED: frozenset({CodeState.IN_PROGRESS}),
    CodeState.IN_PROGRESS: frozenset({CodeState.CODE_COMPLETE}),
    # A stale revision binding pushes CODE_COMPLETE back to IN_PROGRESS while
    # the historical completion fact is preserved by the event log.
    CodeState.CODE_COMPLETE: frozenset({CodeState.IN_PROGRESS}),
}

_ENVIRONMENT_TRANSITIONS: dict[EnvironmentAcceptanceState, frozenset[EnvironmentAcceptanceState]] = {
    EnvironmentAcceptanceState.NOT_RUN: frozenset(
        {
            EnvironmentAcceptanceState.BLOCKED,
            EnvironmentAcceptanceState.FAILED,
            EnvironmentAcceptanceState.ACCEPTED,
        }
    ),
    EnvironmentAcceptanceState.BLOCKED: frozenset(
        {
            EnvironmentAcceptanceState.FAILED,
            EnvironmentAcceptanceState.ACCEPTED,
        }
    ),
    EnvironmentAcceptanceState.FAILED: frozenset(
        {
            EnvironmentAcceptanceState.BLOCKED,
            EnvironmentAcceptanceState.ACCEPTED,
        }
    ),
    EnvironmentAcceptanceState.ACCEPTED: frozenset(),
}

_INCLUDED_OPERATION_TRANSITIONS: dict[OperationExecutionState, frozenset[OperationExecutionState]] = {
    OperationExecutionState.PLANNED: frozenset(
        {
            OperationExecutionState.NOT_AUTHORIZED,
            OperationExecutionState.AUTHORIZED_NOT_EXECUTED,
        }
    ),
    OperationExecutionState.NOT_AUTHORIZED: frozenset(
        {OperationExecutionState.AUTHORIZED_NOT_EXECUTED}
    ),
    OperationExecutionState.AUTHORIZED_NOT_EXECUTED: frozenset(
        {
            OperationExecutionState.EXECUTED,
            OperationExecutionState.FAILED,
        }
    ),
    OperationExecutionState.EXECUTED: frozenset(
        {
            OperationExecutionState.ACCEPTED,
            OperationExecutionState.FAILED,
        }
    ),
    OperationExecutionState.FAILED: frozenset(
        {OperationExecutionState.AUTHORIZED_NOT_EXECUTED}
    ),
    OperationExecutionState.ACCEPTED: frozenset(),
    OperationExecutionState.NOT_AUTHORIZED_NOT_EXECUTED: frozenset(),
}

_ACTIVATION_TRANSITIONS: dict[ProductionActivationState, frozenset[ProductionActivationState]] = {
    ProductionActivationState.NOT_AUTHORIZED: frozenset(
        {ProductionActivationState.AUTHORIZED_NOT_ACTIVE}
    ),
    ProductionActivationState.AUTHORIZED_NOT_ACTIVE: frozenset(
        {ProductionActivationState.ACTIVE}
    ),
    ProductionActivationState.ACTIVE: frozenset({ProductionActivationState.ROLLED_BACK}),
    ProductionActivationState.ROLLED_BACK: frozenset(
        {ProductionActivationState.ACTIVE}
    ),
}


def _transition(
    current: StrEnum,
    target: StrEnum,
    table: dict,
    *,
    label: str,
) -> None:
    allowed = table.get(current, frozenset())
    if target not in allowed:
        raise GovernanceError(
            "illegal_state_transition",
            f"{label} cannot move {current.value} -> {target.value}",
        )


def transition_code_state(current: CodeState, target: CodeState) -> None:
    _transition(current, target, _CODE_TRANSITIONS, label="Code_State")


def transition_environment_state(
    current: EnvironmentAcceptanceState,
    target: EnvironmentAcceptanceState,
) -> None:
    _transition(current, target, _ENVIRONMENT_TRANSITIONS, label="Environment_Acceptance_State")


def transition_included_operation_state(
    current: OperationExecutionState,
    target: OperationExecutionState,
) -> None:
    """Validate a transition for an INCLUDED approval-gated operation."""

    if target is OperationExecutionState.NOT_AUTHORIZED_NOT_EXECUTED:
        raise GovernanceError(
            "illegal_state_transition",
            "NOT_AUTHORIZED_NOT_EXECUTED is reserved for EXCLUDED operations",
        )
    _transition(
        current,
        target,
        _INCLUDED_OPERATION_TRANSITIONS,
        label="Operation_Execution_State",
    )


def excluded_operation_terminal_state() -> OperationExecutionState:
    """The only terminal state an EXCLUDED operation may ever record."""

    return OperationExecutionState.NOT_AUTHORIZED_NOT_EXECUTED


def transition_activation_state(
    current: ProductionActivationState,
    target: ProductionActivationState,
) -> None:
    _transition(current, target, _ACTIVATION_TRANSITIONS, label="Production_Activation_State")


def apply_layer1_environment_result(
    reported: EnvironmentAcceptanceState,
) -> EnvironmentAcceptanceState:
    """Layer 1 evidence may record BLOCKED/FAILED but never ACCEPTED.

    Unit, mock, static, golden, and property results unlock
    Dependency_Safe_Work only; routing their environment claims through this
    guard keeps the rule executable rather than aspirational.
    """

    if reported is EnvironmentAcceptanceState.ACCEPTED:
        raise GovernanceError(
            "layer1_cannot_accept",
            "Layer 1 evidence cannot mark Environment_Acceptance_State ACCEPTED",
        )
    return reported


__all__ = [
    "CodeState",
    "EnvironmentAcceptanceState",
    "OperationExecutionState",
    "ProductionActivationState",
    "SpecCompletionState",
    "apply_layer1_environment_result",
    "excluded_operation_terminal_state",
    "transition_activation_state",
    "transition_code_state",
    "transition_environment_state",
    "transition_included_operation_state",
]
