"""Trusted provenance writer for formal recommendation runs (S1-b, task 1.1/1.2).

This adapter is the only path that maps a completed formal run to
``RunSource.FORMAL`` and persists the canonical evidence identity onto the run
row.  It derives every identity input from server-owned run facts and never
accepts a caller-declared ``evidence_class``.  Historical-replay runs
(``evaluation_scope == "historical_replay"``) are owned by the replay
entrypoint's own adapter and are skipped here, so this writer can never turn a
reconstruction into LIVE evidence.

The writer is deliberately ORM-free: it mutates the duck-typed run row and
leaves the transaction boundary to the caller, so provenance commits
atomically with the pipeline's completion commit.  Any provenance fact that
cannot be proven fails closed to ``UNVERIFIED`` instead of being fabricated.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from app.core.deployment_identity import resolve_application_revision
from app.services.evidence.identity import (
    PROVENANCE_SCHEMA_VERSION,
    EvidenceClass,
    EvidenceIdentityError,
    EvidenceSnapshotIdentity,
    RecommendationEvidenceIdentity,
    RunSource,
    build_recommendation_evidence_identity,
)

logger = logging.getLogger(__name__)

FORMAL_PROVENANCE_REASON = "trusted_formal_run_entrypoint"
INCOMPLETE_PROVENANCE_REASON = "trusted_formal_run_incomplete_provenance"

_FORMAL_RUN_MODE = "formal"
_LIVE_FORWARD_SCOPE = "live_forward"
_COMPLETED_STATUS = "completed"
_UNKNOWN_SNAPSHOT_ID = "unknown"


def resolve_code_revision(environ: Mapping[str, str] | None = None) -> str:
    """Resolve the deployed code revision, failing closed to ``"unknown"``.

    Delegates to the shared deployment-identity resolver so provenance and the
    execution-version hash can never diverge: explicit
    ``ASHARE_APPLICATION_REVISION`` first, then process-cached ``git rev-parse
    HEAD``; ``"unknown"`` is the canonical unverified identifier, so the
    builder classifies the run ``UNVERIFIED``.
    """
    return resolve_application_revision(environ)


def apply_formal_run_provenance(
    run: Any,
    *,
    code_revision: str | None = None,
) -> RecommendationEvidenceIdentity | None:
    """Derive and persist Complete_Provenance on one completed formal run row.

    Returns the identity that was written (or already present), or ``None``
    when the run is not owned by this adapter (non-formal mode, non-live
    scope, replay-owned evidence, or not yet completed).  The call is
    idempotent: a row already carrying the same ``evidence_identity_hash`` is
    left untouched, as is a row already marked with the fail-closed
    incomplete-provenance reason.

    The caller owns the transaction: this function never commits.
    """
    if str(getattr(run, "run_mode", "") or "").lower() != _FORMAL_RUN_MODE:
        return None
    if str(getattr(run, "evaluation_scope", "") or "") != _LIVE_FORWARD_SCOPE:
        return None
    if getattr(run, "evidence_class", None) == EvidenceClass.HISTORICAL_RECONSTRUCTION.value:
        return None
    if str(getattr(run, "status", "") or "") != _COMPLETED_STATUS:
        return None

    generated_at = _run_generated_at(run)
    signal_date = str(getattr(run, "trade_date", "") or "")
    data_as_of = str(getattr(run, "data_as_of", "") or "")
    source_snapshot_id = str(getattr(run, "source_snapshot_id", "") or "") or _UNKNOWN_SNAPSHOT_ID
    revision = code_revision or resolve_code_revision()

    identity = _build_identity(
        run,
        generated_at=generated_at,
        signal_date=signal_date,
        data_as_of=data_as_of,
        source_snapshot_id=source_snapshot_id,
        code_revision=revision,
    )
    if identity is None:
        return _apply_incomplete_provenance(
            run,
            generated_at=generated_at,
            data_as_of=data_as_of,
            source_snapshot_id=source_snapshot_id,
            code_revision=revision,
        )

    if getattr(run, "evidence_identity_hash", None) == identity.identity_hash:
        return identity
    run.evidence_class = identity.evidence_class.value
    run.actual_generated_at = identity.actual_generated_at.replace(tzinfo=None)
    run.data_as_of = identity.data_as_of
    run.source_snapshot_id = identity.source_snapshot_id
    run.evidence_identity_hash = identity.identity_hash
    run.strategy_policy_fingerprint = identity.strategy_policy_fingerprint
    run.execution_policy_fingerprint = identity.execution_policy_fingerprint
    run.code_revision = identity.code_revision
    run.provenance_schema_version = identity.provenance_schema_version
    run.classification_reason = FORMAL_PROVENANCE_REASON
    return identity


def _run_generated_at(run: Any) -> datetime:
    """Return the run's real creation time as aware UTC (naive rows are UTC)."""

    started_at = getattr(run, "started_at", None)
    if isinstance(started_at, datetime):
        if started_at.tzinfo is None or started_at.utcoffset() is None:
            return started_at.replace(tzinfo=timezone.utc)
        return started_at.astimezone(timezone.utc)
    # A completed formal run always has started_at; reaching this fallback
    # means the row is legacy/corrupt, so use the truthful finalize time.
    return datetime.now(timezone.utc)


def _build_identity(
    run: Any,
    *,
    generated_at: datetime,
    signal_date: str,
    data_as_of: str,
    source_snapshot_id: str,
    code_revision: str,
) -> RecommendationEvidenceIdentity | None:
    """Build the canonical identity, or ``None`` when facts are unprovable."""

    policies = _execution_policies_from_summary(getattr(run, "result_summary_json", None))
    if policies is None or not signal_date or not data_as_of:
        return None
    strategy_policy, execution_policy = policies
    try:
        return build_recommendation_evidence_identity(
            run_source=RunSource.FORMAL,
            actual_generated_at=generated_at,
            signal_date=signal_date,
            snapshot=EvidenceSnapshotIdentity(
                source_snapshot_id=source_snapshot_id,
                data_as_of=data_as_of,
            ),
            strategy_policy=strategy_policy,
            execution_policy=execution_policy,
            code_revision=code_revision,
        )
    except EvidenceIdentityError as exc:
        logger.warning(
            "formal run %s provenance facts rejected (%s); failing closed",
            getattr(run, "run_id", "?"),
            exc.code,
        )
        return None


def _execution_policies_from_summary(raw_summary: Any) -> tuple[Any, Any] | None:
    """Extract strategy/execution policy fingerprints from the execution audit.

    The shape matches the canonical execution audit persisted by both enqueue
    and the pipeline completion summary: ``execution.strategy_fingerprints``
    (non-empty list of fingerprint mappings) and ``execution.execution_policy``
    (mapping with a string ``fingerprint``).  Anything else is unprovable.
    """

    if not isinstance(raw_summary, str) or not raw_summary:
        return None
    try:
        summary = json.loads(raw_summary)
    except (TypeError, json.JSONDecodeError):
        return None
    execution = summary.get("execution") if isinstance(summary, dict) else None
    if not isinstance(execution, dict):
        return None
    strategy_policy = execution.get("strategy_fingerprints")
    execution_policy = execution.get("execution_policy")
    if not isinstance(strategy_policy, list) or not strategy_policy:
        return None
    if not isinstance(execution_policy, dict) or not isinstance(
        execution_policy.get("fingerprint"), str
    ):
        return None
    return strategy_policy, execution_policy


def _apply_incomplete_provenance(
    run: Any,
    *,
    generated_at: datetime,
    data_as_of: str,
    source_snapshot_id: str,
    code_revision: str,
) -> None:
    """Fail closed: mark the run UNVERIFIED without fabricating identity facts.

    Fingerprints and the identity hash stay ``None`` because they cannot be
    proven; requirement 2.9 classifies such artifacts ``UNVERIFIED``.
    """

    if (
        getattr(run, "evidence_class", None) == EvidenceClass.UNVERIFIED.value
        and getattr(run, "classification_reason", None) == INCOMPLETE_PROVENANCE_REASON
        and getattr(run, "evidence_identity_hash", None) is None
    ):
        return None
    logger.warning(
        "formal run %s lacks provable provenance facts; marking UNVERIFIED",
        getattr(run, "run_id", "?"),
    )
    run.evidence_class = EvidenceClass.UNVERIFIED.value
    run.actual_generated_at = generated_at.replace(tzinfo=None)
    run.data_as_of = data_as_of or None
    run.source_snapshot_id = source_snapshot_id
    run.evidence_identity_hash = None
    run.strategy_policy_fingerprint = None
    run.execution_policy_fingerprint = None
    run.code_revision = code_revision
    run.provenance_schema_version = PROVENANCE_SCHEMA_VERSION
    run.classification_reason = INCOMPLETE_PROVENANCE_REASON
    return None


__all__ = [
    "FORMAL_PROVENANCE_REASON",
    "INCOMPLETE_PROVENANCE_REASON",
    "apply_formal_run_provenance",
    "resolve_code_revision",
]
