"""Governance plane for the production-evidence-risk-hardening spec.

Content-addressed immutable objects, append-only events, orthogonal state
machines, and revision bindings.  No ORM, API, scheduler, or vendor imports:
entry-point adapters persist and query through the store abstraction only.
"""
from app.governance.envelope import (
    GOVERNANCE_SCHEMA_VERSION,
    GovernanceError,
    ImmutableEnvelope,
)
from app.governance.store import (
    DEFAULT_STORE_ROOT,
    EVIDENCE_URI_SCHEME,
    GovernanceObjectStore,
)
from app.governance.events import GovernanceEventLog
from app.governance.state import (
    CodeState,
    EnvironmentAcceptanceState,
    OperationExecutionState,
    ProductionActivationState,
    SpecCompletionState,
)
from app.governance.binding import (
    DependencyDigest,
    DependencyDigestKind,
    RevisionBinding,
    RevisionBindingStatus,
    digest_file,
)

__all__ = [
    "DEFAULT_STORE_ROOT",
    "EVIDENCE_URI_SCHEME",
    "GOVERNANCE_SCHEMA_VERSION",
    "CodeState",
    "DependencyDigest",
    "DependencyDigestKind",
    "EnvironmentAcceptanceState",
    "GovernanceError",
    "GovernanceEventLog",
    "GovernanceObjectStore",
    "ImmutableEnvelope",
    "OperationExecutionState",
    "ProductionActivationState",
    "RevisionBinding",
    "RevisionBindingStatus",
    "SpecCompletionState",
    "digest_file",
]
