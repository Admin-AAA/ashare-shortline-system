"""Layer 1 contracts for strategy data quality and outcome provenance.

This module deliberately contains no database or network adapter.  It operates on
records owned by the existing ``RecommendationOutcomeVersion`` append history and
``RecommendationOutcome`` current projection.  The 0005 migration supplies the
additive columns; a shared ORM mapping is required before production cutover.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Iterable, Mapping, Sequence

from app.engine.canonical import canonical_fingerprint, canonical_json


OUTCOME_CALCULATION_SCHEMA_VERSION = 2


class QualityContractError(ValueError):
    """A strategy quality declaration or observation is invalid."""


class OutcomeContractError(ValueError):
    """Outcome identity or history violates the append-only contract."""


class OutcomeFingerprintConflict(OutcomeContractError):
    """One calculation fingerprint is associated with conflicting content."""


class StaleProjectionWrite(OutcomeContractError):
    """A compare-and-set writer observed an obsolete projection revision."""


class DataQualityStatus(StrEnum):
    READY = "READY"
    DEGRADED = "DEGRADED"
    BLOCKED = "BLOCKED"


class AuxiliaryImpact(StrEnum):
    SUPPORTED = "SUPPORTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class DatasetRequirement:
    dataset: str
    required: bool
    minimum_coverage: float = 1.0
    max_age_seconds: int | None = None
    auxiliary: bool = False

    def __post_init__(self) -> None:
        _required_text(self.dataset, "dataset")
        if not 0.0 <= float(self.minimum_coverage) <= 1.0:
            raise QualityContractError("minimum_coverage must be between zero and one")
        if self.max_age_seconds is not None and self.max_age_seconds < 0:
            raise QualityContractError("max_age_seconds must be non-negative")


@dataclass(frozen=True, slots=True)
class StrategyDataContract:
    strategy_id: str
    requirements: tuple[DatasetRequirement, ...]
    minimum_auxiliary_counterfactual_samples: int = 1

    def __post_init__(self) -> None:
        _required_text(self.strategy_id, "strategy_id")
        requirements = tuple(self.requirements)
        if not requirements:
            raise QualityContractError("a strategy must declare at least one dataset")
        names = [item.dataset for item in requirements]
        if len(names) != len(set(names)):
            raise QualityContractError("dataset declarations must be unique")
        if self.minimum_auxiliary_counterfactual_samples < 0:
            raise QualityContractError(
                "minimum_auxiliary_counterfactual_samples must be non-negative"
            )
        object.__setattr__(self, "requirements", requirements)


@dataclass(frozen=True, slots=True)
class DatasetObservation:
    dataset: str
    present: bool
    coverage: float | None = None
    age_seconds: int | None = None
    synthetic_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _required_text(self.dataset, "dataset")
        if self.coverage is not None and not 0.0 <= float(self.coverage) <= 1.0:
            raise QualityContractError("coverage must be between zero and one")
        if self.age_seconds is not None and self.age_seconds < 0:
            raise QualityContractError("age_seconds must be non-negative")
        fields = tuple(sorted({_required_text(item, "synthetic field") for item in self.synthetic_fields}))
        object.__setattr__(self, "synthetic_fields", fields)


@dataclass(frozen=True, slots=True)
class DataQualityReason:
    code: str
    dataset: str
    detail: str


@dataclass(frozen=True, slots=True)
class DataQualityDecision:
    strategy_id: str
    status: DataQualityStatus
    reasons: tuple[DataQualityReason, ...]
    auxiliary_impact: AuxiliaryImpact
    evaluated_before_formal_execution: bool = True

    @property
    def can_execute_formally(self) -> bool:
        return self.status is not DataQualityStatus.BLOCKED


def decide_quality(
    contract: StrategyDataContract,
    observations: Iterable[DatasetObservation],
    *,
    auxiliary_counterfactual_samples: int = 0,
) -> DataQualityDecision:
    """Evaluate only the active strategy's declared data contract.

    Required absence, unprovable/insufficient coverage, or unprovable/stale
    freshness blocks formal execution.  Equivalent optional failures and every
    declared synthetic field degrade it.  Auxiliary causal impact remains
    ``UNKNOWN`` until the strategy's declared counterfactual sample threshold is
    met; this impact label is orthogonal to readiness.
    """

    if auxiliary_counterfactual_samples < 0:
        raise QualityContractError("auxiliary_counterfactual_samples must be non-negative")
    observed: dict[str, DatasetObservation] = {}
    for item in observations:
        if item.dataset in observed:
            raise QualityContractError(f"duplicate observation for dataset {item.dataset}")
        observed[item.dataset] = item

    blockers: list[DataQualityReason] = []
    degradations: list[DataQualityReason] = []
    informational: list[DataQualityReason] = []
    auxiliary_declared = False

    for requirement in contract.requirements:
        auxiliary_declared = auxiliary_declared or requirement.auxiliary
        observation = observed.get(requirement.dataset)
        target = blockers if requirement.required else degradations
        if observation is None or not observation.present:
            target.append(
                DataQualityReason(
                    "REQUIRED_DATASET_MISSING" if requirement.required else "OPTIONAL_DATASET_MISSING",
                    requirement.dataset,
                    "declared dataset is absent",
                )
            )
            continue

        if observation.coverage is None or observation.coverage < requirement.minimum_coverage:
            target.append(
                DataQualityReason(
                    "REQUIRED_COVERAGE_INSUFFICIENT"
                    if requirement.required
                    else "OPTIONAL_COVERAGE_INSUFFICIENT",
                    requirement.dataset,
                    f"coverage={observation.coverage!r}, required={requirement.minimum_coverage}",
                )
            )
        if requirement.max_age_seconds is not None and (
            observation.age_seconds is None
            or observation.age_seconds > requirement.max_age_seconds
        ):
            target.append(
                DataQualityReason(
                    "REQUIRED_DATASET_STALE" if requirement.required else "OPTIONAL_DATASET_STALE",
                    requirement.dataset,
                    f"age_seconds={observation.age_seconds!r}, maximum={requirement.max_age_seconds}",
                )
            )
        if observation.synthetic_fields:
            degradations.append(
                DataQualityReason(
                    "SYNTHETIC_FIELDS_USED",
                    requirement.dataset,
                    ",".join(observation.synthetic_fields),
                )
            )

    if blockers:
        status = DataQualityStatus.BLOCKED
    elif degradations:
        status = DataQualityStatus.DEGRADED
    else:
        status = DataQualityStatus.READY

    auxiliary_impact = AuxiliaryImpact.SUPPORTED
    if (
        auxiliary_declared
        and auxiliary_counterfactual_samples
        < contract.minimum_auxiliary_counterfactual_samples
    ):
        auxiliary_impact = AuxiliaryImpact.UNKNOWN
        informational.append(
            DataQualityReason(
                "AUXILIARY_COUNTERFACTUAL_INSUFFICIENT",
                "*",
                "auxiliary impact cannot yet be inferred",
            )
        )

    reasons = tuple(
        sorted(
            (*blockers, *degradations, *informational),
            key=lambda item: (item.code, item.dataset, item.detail),
        )
    )
    return DataQualityDecision(
        strategy_id=contract.strategy_id,
        status=status,
        reasons=reasons,
        auxiliary_impact=auxiliary_impact,
    )


@dataclass(frozen=True, slots=True)
class OutcomeCalculationIdentity:
    recommendation_id: str
    recommendation_identity_hash: str
    market_snapshot_id: str
    market_artifact_hash: str
    execution_policy_fingerprint: str
    calculation_code_revision: str
    label_policy_fingerprint: str
    calculation_schema_version: int = OUTCOME_CALCULATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _required_text(self.recommendation_id, "recommendation_id")
        _sha256(self.recommendation_identity_hash, "recommendation_identity_hash")
        _required_text(self.market_snapshot_id, "market_snapshot_id")
        _sha256(self.market_artifact_hash, "market_artifact_hash")
        _sha256(self.execution_policy_fingerprint, "execution_policy_fingerprint")
        revision = _required_text(self.calculation_code_revision, "calculation_code_revision")
        if len(revision) > 64:
            raise OutcomeContractError("calculation_code_revision exceeds 64 characters")
        _sha256(self.label_policy_fingerprint, "label_policy_fingerprint")
        if self.calculation_schema_version != OUTCOME_CALCULATION_SCHEMA_VERSION:
            raise OutcomeContractError("unsupported outcome calculation schema version")

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "calculation_code_revision": self.calculation_code_revision,
            "calculation_schema_version": self.calculation_schema_version,
            "execution_policy_fingerprint": self.execution_policy_fingerprint,
            "label_policy_fingerprint": self.label_policy_fingerprint,
            "market_artifact_hash": self.market_artifact_hash,
            "market_snapshot_id": self.market_snapshot_id,
            "recommendation_id": self.recommendation_id,
            "recommendation_identity_hash": self.recommendation_identity_hash,
        }

    @property
    def calculation_fingerprint(self) -> str:
        return canonical_fingerprint(self.canonical_payload())


@dataclass(frozen=True, slots=True)
class OutcomeVersionRecord:
    version_id: str
    recommendation_id: str
    calculation_fingerprint: str | None
    result_hash: str | None
    result_json: str
    recommendation_identity_hash: str | None = None
    market_snapshot_id: str | None = None
    market_artifact_hash: str | None = None
    execution_policy_fingerprint: str | None = None
    calculation_code_revision: str | None = None
    label_policy_fingerprint: str | None = None
    calculation_schema_version: int | None = None
    validity_status: str = "VALID"
    computed_at: datetime | None = None

    @property
    def is_complete_valid(self) -> bool:
        try:
            if self.validity_status != "VALID":
                return False
            identity = self.identity()
            return (
                self.calculation_fingerprint == identity.calculation_fingerprint
                and self.result_hash == canonical_fingerprint(_parse_canonical_json(self.result_json))
            )
        except (OutcomeContractError, TypeError, ValueError):
            return False

    def identity(self) -> OutcomeCalculationIdentity:
        return OutcomeCalculationIdentity(
            recommendation_id=self.recommendation_id,
            recommendation_identity_hash=self.recommendation_identity_hash or "",
            market_snapshot_id=self.market_snapshot_id or "",
            market_artifact_hash=self.market_artifact_hash or "",
            execution_policy_fingerprint=self.execution_policy_fingerprint or "",
            calculation_code_revision=self.calculation_code_revision or "",
            label_policy_fingerprint=self.label_policy_fingerprint or "",
            calculation_schema_version=self.calculation_schema_version or 0,
        )


@dataclass(frozen=True, slots=True)
class OutcomeAppendDecision:
    versions: tuple[OutcomeVersionRecord, ...]
    version: OutcomeVersionRecord
    appended: bool


def upsert_outcome_version(
    existing_versions: Sequence[OutcomeVersionRecord],
    identity: OutcomeCalculationIdentity,
    result: Any,
    *,
    computed_at: datetime | None = None,
) -> OutcomeAppendDecision:
    """Return an append-or-reuse decision over the existing version history.

    No existing record is modified.  A fingerprint collision with a different
    result (or duplicate existing fingerprint rows) fails closed.
    """

    versions = tuple(existing_versions)
    calculation_fingerprint = identity.calculation_fingerprint
    result_json = canonical_json(result)
    result_hash = canonical_fingerprint(result)
    matches = [
        item for item in versions if item.calculation_fingerprint == calculation_fingerprint
    ]
    if len(matches) > 1:
        raise OutcomeFingerprintConflict(
            "duplicate rows already exist for one outcome calculation fingerprint"
        )
    if matches:
        existing = matches[0]
        if (
            existing.recommendation_id != identity.recommendation_id
            or existing.result_hash != result_hash
            or existing.result_json != result_json
            or not existing.is_complete_valid
        ):
            raise OutcomeFingerprintConflict(
                "outcome calculation fingerprint conflicts with stored result or identity"
            )
        return OutcomeAppendDecision(versions=versions, version=existing, appended=False)

    version_id = f"outv_{calculation_fingerprint}"
    if any(item.version_id == version_id for item in versions):
        raise OutcomeFingerprintConflict("deterministic outcome version id collision")
    timestamp = computed_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise OutcomeContractError("computed_at must be timezone-aware")
    version = OutcomeVersionRecord(
        version_id=version_id,
        recommendation_id=identity.recommendation_id,
        calculation_fingerprint=calculation_fingerprint,
        result_hash=result_hash,
        result_json=result_json,
        recommendation_identity_hash=identity.recommendation_identity_hash,
        market_snapshot_id=identity.market_snapshot_id,
        market_artifact_hash=identity.market_artifact_hash,
        execution_policy_fingerprint=identity.execution_policy_fingerprint,
        calculation_code_revision=identity.calculation_code_revision,
        label_policy_fingerprint=identity.label_policy_fingerprint,
        calculation_schema_version=identity.calculation_schema_version,
        validity_status="VALID",
        computed_at=timestamp.astimezone(timezone.utc),
    )
    return OutcomeAppendDecision(versions=(*versions, version), version=version, appended=True)


@dataclass(frozen=True, slots=True)
class CurrentProjection:
    recommendation_id: str
    calculation_version_id: str
    current_calculation_fingerprint: str | None
    projection_revision: int
    projected_at: datetime


@dataclass(frozen=True, slots=True)
class ProjectionCasDecision:
    projection: CurrentProjection
    updated: bool


def cas_current(
    current: CurrentProjection | None,
    target: OutcomeVersionRecord,
    *,
    expected_revision: int | None,
    projected_at: datetime | None = None,
) -> ProjectionCasDecision:
    """Atomically model the existing current projection's revision/CAS update."""

    if not target.is_complete_valid or target.calculation_fingerprint is None:
        raise OutcomeContractError("current projection may reference only a complete valid version")
    if current is None:
        if expected_revision is not None:
            raise StaleProjectionWrite("projection was created by another writer")
        next_revision = 1
    else:
        if current.recommendation_id != target.recommendation_id:
            raise OutcomeContractError("projection and target recommendation identities differ")
        if expected_revision != current.projection_revision:
            raise StaleProjectionWrite("projection revision compare-and-set failed")
        if (
            current.calculation_version_id == target.version_id
            and current.current_calculation_fingerprint == target.calculation_fingerprint
        ):
            return ProjectionCasDecision(projection=current, updated=False)
        next_revision = current.projection_revision + 1

    timestamp = projected_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise OutcomeContractError("projected_at must be timezone-aware")
    return ProjectionCasDecision(
        projection=CurrentProjection(
            recommendation_id=target.recommendation_id,
            calculation_version_id=target.version_id,
            current_calculation_fingerprint=target.calculation_fingerprint,
            projection_revision=next_revision,
            projected_at=timestamp.astimezone(timezone.utc),
        ),
        updated=True,
    )


@dataclass(frozen=True, slots=True)
class OutcomeRecomputeCandidate:
    recommendation_id: str
    expected_calculation_fingerprint: str | None
    projection: CurrentProjection | None


@dataclass(frozen=True, slots=True)
class StaleOutcomeSelection:
    recommendation_ids: tuple[str, ...]
    reasons: tuple[tuple[str, str], ...]


def select_stale_outcomes(
    candidates: Iterable[OutcomeRecomputeCandidate],
    versions: Iterable[OutcomeVersionRecord],
) -> StaleOutcomeSelection:
    """Select exactly missing or mismatched fingerprint/projection records."""

    by_id = {item.version_id: item for item in versions}
    selected: list[str] = []
    reasons: list[tuple[str, str]] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.recommendation_id in seen:
            raise OutcomeContractError("recompute candidates must be unique")
        seen.add(candidate.recommendation_id)
        projection = candidate.projection
        expected = candidate.expected_calculation_fingerprint
        reason: str | None = None
        if expected is None:
            reason = "EXPECTED_FINGERPRINT_MISSING"
        elif projection is None:
            reason = "CURRENT_PROJECTION_MISSING"
        elif projection.recommendation_id != candidate.recommendation_id:
            reason = "CURRENT_PROJECTION_ORPHAN"
        elif projection.current_calculation_fingerprint is None:
            reason = "CURRENT_FINGERPRINT_MISSING"
        elif projection.current_calculation_fingerprint != expected:
            reason = "CURRENT_FINGERPRINT_MISMATCH"
        else:
            version = by_id.get(projection.calculation_version_id)
            if version is None:
                reason = "CURRENT_VERSION_MISSING"
            elif version.recommendation_id != candidate.recommendation_id:
                reason = "CURRENT_VERSION_ORPHAN"
            elif version.calculation_fingerprint is None:
                reason = "VERSION_FINGERPRINT_MISSING"
            elif version.calculation_fingerprint != expected:
                reason = "VERSION_FINGERPRINT_MISMATCH"
            elif not version.is_complete_valid:
                reason = "CURRENT_VERSION_INVALID"
        if reason is not None:
            selected.append(candidate.recommendation_id)
            reasons.append((candidate.recommendation_id, reason))
    return StaleOutcomeSelection(tuple(selected), tuple(reasons))


class OutcomeAuditKind(StrEnum):
    DUPLICATE = "DUPLICATE"
    ORPHAN = "ORPHAN"
    MISSING_CURRENT = "MISSING_CURRENT"
    FINGERPRINT_MISSING = "FINGERPRINT_MISSING"
    INVALID_CURRENT = "INVALID_CURRENT"


@dataclass(frozen=True, slots=True)
class OutcomeAuditFinding:
    kind: OutcomeAuditKind
    recommendation_id: str
    record_id: str
    detail: str


@dataclass(frozen=True, slots=True)
class OutcomeHistoryAudit:
    findings: tuple[OutcomeAuditFinding, ...]
    preserved_version_ids: tuple[str, ...]
    current_kpi_version_ids: tuple[str, ...]


def audit_outcome_history(
    recommendation_ids: Iterable[str],
    versions: Sequence[OutcomeVersionRecord],
    projections: Iterable[CurrentProjection],
) -> OutcomeHistoryAudit:
    """Audit without deleting, trimming, or rewriting any historical record."""

    known = frozenset(recommendation_ids)
    version_rows = tuple(versions)
    projection_rows = tuple(projections)
    findings: list[OutcomeAuditFinding] = []
    by_version = {item.version_id: item for item in version_rows}
    fingerprint_counts: dict[str, int] = {}
    for item in version_rows:
        if item.calculation_fingerprint is not None:
            fingerprint_counts[item.calculation_fingerprint] = (
                fingerprint_counts.get(item.calculation_fingerprint, 0) + 1
            )
    duplicate_fingerprints = {
        fingerprint for fingerprint, count in fingerprint_counts.items() if count > 1
    }

    for item in version_rows:
        if item.recommendation_id not in known:
            findings.append(OutcomeAuditFinding(OutcomeAuditKind.ORPHAN, item.recommendation_id, item.version_id, "version has no recommendation"))
        if item.calculation_fingerprint is None:
            findings.append(OutcomeAuditFinding(OutcomeAuditKind.FINGERPRINT_MISSING, item.recommendation_id, item.version_id, "version calculation fingerprint is absent"))
        elif item.calculation_fingerprint in duplicate_fingerprints:
            findings.append(OutcomeAuditFinding(OutcomeAuditKind.DUPLICATE, item.recommendation_id, item.version_id, "calculation fingerprint is duplicated"))

    projection_by_recommendation: dict[str, CurrentProjection] = {}
    duplicate_projections: set[str] = set()
    for projection in projection_rows:
        if projection.recommendation_id in projection_by_recommendation:
            duplicate_projections.add(projection.recommendation_id)
            findings.append(OutcomeAuditFinding(OutcomeAuditKind.DUPLICATE, projection.recommendation_id, projection.calculation_version_id, "multiple current projections exist"))
            continue
        projection_by_recommendation[projection.recommendation_id] = projection
        if projection.recommendation_id not in known:
            findings.append(OutcomeAuditFinding(OutcomeAuditKind.ORPHAN, projection.recommendation_id, projection.calculation_version_id, "projection has no recommendation"))
        if projection.current_calculation_fingerprint is None:
            findings.append(OutcomeAuditFinding(OutcomeAuditKind.FINGERPRINT_MISSING, projection.recommendation_id, projection.calculation_version_id, "current projection fingerprint is absent"))

    recommendations_with_versions = {item.recommendation_id for item in version_rows if item.recommendation_id in known}
    for recommendation_id in sorted(recommendations_with_versions):
        projection = projection_by_recommendation.get(recommendation_id)
        if projection is None or projection.calculation_version_id not in by_version:
            findings.append(OutcomeAuditFinding(OutcomeAuditKind.MISSING_CURRENT, recommendation_id, projection.calculation_version_id if projection else "", "current projection or referenced version is absent"))

    eligible: list[str] = []
    for recommendation_id, projection in projection_by_recommendation.items():
        version = by_version.get(projection.calculation_version_id)
        valid = bool(
            recommendation_id in known
            and recommendation_id not in duplicate_projections
            and version is not None
            and version.recommendation_id == recommendation_id
            and version.is_complete_valid
            and projection.current_calculation_fingerprint == version.calculation_fingerprint
            and version.calculation_fingerprint not in duplicate_fingerprints
        )
        if valid:
            eligible.append(version.version_id)  # type: ignore[union-attr]
        elif recommendation_id in known and version is not None:
            findings.append(OutcomeAuditFinding(OutcomeAuditKind.INVALID_CURRENT, recommendation_id, projection.calculation_version_id, "current projection does not reference one unique complete valid version"))

    return OutcomeHistoryAudit(
        findings=tuple(sorted(findings, key=lambda item: (item.kind, item.recommendation_id, item.record_id, item.detail))),
        preserved_version_ids=tuple(item.version_id for item in version_rows),
        current_kpi_version_ids=tuple(sorted(eligible)),
    )


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        error = OutcomeContractError if field.startswith(("recommendation", "market", "calculation", "label", "execution")) else QualityContractError
        raise error(f"{field} must be non-empty text")
    return value.strip()


def _sha256(value: Any, field: str) -> str:
    text = _required_text(value, field)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise OutcomeContractError(f"{field} must be a lowercase SHA-256 fingerprint")
    return text


def _parse_canonical_json(value: str) -> Any:
    import json

    parsed = json.loads(value)
    if canonical_json(parsed) != value:
        raise OutcomeContractError("result_json is not canonical JSON")
    return parsed


__all__ = [
    "AuxiliaryImpact",
    "CurrentProjection",
    "DataQualityDecision",
    "DataQualityReason",
    "DataQualityStatus",
    "DatasetObservation",
    "DatasetRequirement",
    "OUTCOME_CALCULATION_SCHEMA_VERSION",
    "OutcomeAppendDecision",
    "OutcomeAuditFinding",
    "OutcomeAuditKind",
    "OutcomeCalculationIdentity",
    "OutcomeContractError",
    "OutcomeFingerprintConflict",
    "OutcomeHistoryAudit",
    "OutcomeRecomputeCandidate",
    "OutcomeVersionRecord",
    "ProjectionCasDecision",
    "QualityContractError",
    "StaleOutcomeSelection",
    "StaleProjectionWrite",
    "StrategyDataContract",
    "audit_outcome_history",
    "cas_current",
    "decide_quality",
    "select_stale_outcomes",
    "upsert_outcome_version",
]
