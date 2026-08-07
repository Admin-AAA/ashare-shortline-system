"""Canonical run-level evidence selectors (single source of truth).

This module is the one place that defines which persisted runs count as
``LIVE_FORWARD`` or ``HISTORICAL_RECONSTRUCTION`` evidence.  It is a pure
domain + SQLAlchemy expression module: it never touches a session, never
executes I/O and never mutates anything.

Rules encoded here (production-evidence-risk-hardening Requirements 2.5–2.9,
4.3, 4.8):

* LIVE eligibility is derived only from complete, verified provenance.
  ``run_name``, ``evaluation_scope``, ``status`` and rewritten timestamps are
  deliberately absent from the LIVE predicate and can never promote a row to
  LIVE; a classification backfill cannot create LIVE either, because the
  identity hash and policy fingerprints must already be present.
* The same predicate is exposed twice with identical semantics:
  :func:`live_forward_run_conditions` as a SQL WHERE tuple and
  :func:`is_live_forward_run` as a Python row-level predicate.
* The historical-reconstruction eligibility branch that used to live in
  ``app.services.official_sample_scope`` is migrated here; that module imports
  it back so no second copy of the expression can drift.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.models.tables import Recommendation, RecommendationRun
from app.services.evidence.identity import (
    PROVENANCE_SCHEMA_VERSION,
    EvidenceClass,
)

OFFICIAL_RECOMMENDATION_PREFIX = "rec_"


def live_forward_provenance_conditions() -> tuple:
    """Return the complete persisted provenance core of a LIVE claim.

    The causal comparison is intentionally kept out of this core so readers
    can identify and count malformed LIVE claims instead of making them
    disappear from both the accepted and violation populations.
    """
    return (
        RecommendationRun.evidence_class == EvidenceClass.LIVE_FORWARD.value,
        RecommendationRun.actual_generated_at.is_not(None),
        RecommendationRun.data_as_of.is_not(None),
        RecommendationRun.source_snapshot_id.is_not(None),
        RecommendationRun.evidence_identity_hash.is_not(None),
        RecommendationRun.strategy_policy_fingerprint.is_not(None),
        RecommendationRun.execution_policy_fingerprint.is_not(None),
        RecommendationRun.code_revision.is_not(None),
        RecommendationRun.provenance_schema_version == PROVENANCE_SCHEMA_VERSION,
    )


def live_forward_run_conditions() -> tuple:
    """Return the canonical SQL WHERE tuple for provenance-complete LIVE runs.

    This is the only LIVE_FORWARD predicate.  LIVE_KPI, forward gates, the
    True_Forward_Ledger and champion evidence must all select through this
    expression (Requirement 2.6).  Display metadata and mutable lifecycle
    fields are intentionally not part of it (Requirement 2.5).
    """
    return (
        *live_forward_provenance_conditions(),
        RecommendationRun.data_as_of <= RecommendationRun.trade_date,
    )


def is_live_forward_run(row: Any) -> bool:
    """Row-level LIVE_FORWARD predicate with the same semantics as the SQL tuple.

    ``row`` may be an ORM instance, a namespace or a mapping with the
    ``RecommendationRun`` provenance attributes.  Missing attributes never
    qualify.
    """
    data_as_of = _value(row, "data_as_of")
    signal_date = _value(row, "trade_date")
    return (
        _value(row, "evidence_class") == EvidenceClass.LIVE_FORWARD.value
        and _value(row, "actual_generated_at") is not None
        and data_as_of is not None
        and signal_date is not None
        and data_as_of <= signal_date
        and _value(row, "source_snapshot_id") is not None
        and _value(row, "evidence_identity_hash") is not None
        and _value(row, "strategy_policy_fingerprint") is not None
        and _value(row, "execution_policy_fingerprint") is not None
        and _value(row, "code_revision") is not None
        and _value(row, "provenance_schema_version") == PROVENANCE_SCHEMA_VERSION
    )


def historical_reconstruction_provenance_conditions() -> tuple:
    """Return the provenance-completeness core of a reconstruction claim.

    This subset is shared by :func:`historical_reconstruction_run_conditions`
    (which adds the sample-eligibility extras) and by the Historical_Query
    as-of enforcement (which must detect causality breaches without the as-of
    clause itself, otherwise the check would be vacuous).
    """
    return (
        RecommendationRun.evidence_class
        == EvidenceClass.HISTORICAL_RECONSTRUCTION.value,
        RecommendationRun.actual_generated_at.is_not(None),
        RecommendationRun.reconstruction_batch_id.is_not(None),
        RecommendationRun.evidence_identity_hash.is_not(None),
        RecommendationRun.strategy_policy_fingerprint.is_not(None),
        RecommendationRun.execution_policy_fingerprint.is_not(None),
        RecommendationRun.code_revision.is_not(None),
        RecommendationRun.provenance_schema_version == PROVENANCE_SCHEMA_VERSION,
        RecommendationRun.data_as_of.is_not(None),
        RecommendationRun.source_snapshot_id.is_not(None),
    )


def historical_reconstruction_run_conditions() -> tuple:
    """Return eligibility for the historical record page, never for LIVE KPI.

    Migrated from ``official_sample_scope.historical_reconstruction_sample_conditions``
    (semantics preserved exactly).  Historical records are selected by their
    immutable evidence identity and signal/trade date.  Their real generation
    timestamp is deliberately not compared with the historical date and is
    never rewritten to make them LIVE.
    """
    return (
        RecommendationRun.run_mode == "formal",
        RecommendationRun.status == "completed",
        *historical_reconstruction_provenance_conditions(),
        RecommendationRun.data_as_of <= RecommendationRun.trade_date,
        Recommendation.recommendation_id.like(f"{OFFICIAL_RECOMMENDATION_PREFIX}%"),
    )


def _value(row: Any, name: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(name)
    return getattr(row, name, None)


__all__ = [
    "OFFICIAL_RECOMMENDATION_PREFIX",
    "historical_reconstruction_provenance_conditions",
    "historical_reconstruction_run_conditions",
    "is_live_forward_run",
    "live_forward_provenance_conditions",
    "live_forward_run_conditions",
]
