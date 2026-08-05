"""Pure Layer-1 contracts for canonical holdout and release governance.

The module has no database, network, clock, or ORM dependency.  It emits
immutable values that an adapter can persist in the existing ``backtest_*``
research tables.  In particular a caller token is audit metadata only: it is
hashed in a claim command and is never part of population identity.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum
import hashlib
import json
import math
import re
from typing import Any, Mapping, Sequence

from app.engine.canonical import canonical_fingerprint, canonical_json, canonicalize


HOLDOUT_IDENTITY_SCHEMA_VERSION = 1
RELEASE_DECISION_SCHEMA_VERSION = 1
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class HoldoutContractError(ValueError):
    """A fail-closed holdout/release contract violation with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class EvidenceScope(StrEnum):
    TRAIN = "TRAIN"
    VALIDATION = "VALIDATION"
    PURGED_HISTORICAL_OOS = "PURGED_HISTORICAL_OOS"
    UNTOUCHED_HOLDOUT = "UNTOUCHED_HOLDOUT"
    SHADOW_FORWARD = "SHADOW_FORWARD"
    LIVE_CANARY = "LIVE_CANARY"


class ResearchPathClassification(StrEnum):
    GOVERNED_RELEASE = "GOVERNED_RELEASE"
    EXPLORATORY = "EXPLORATORY"
    PATH_A = "PATH_A"


class AuthorityRole(StrEnum):
    RESEARCH_PROPOSER = "RESEARCH_PROPOSER"
    FORMAL_PUBLISHER = "FORMAL_PUBLISHER"


class ReleaseAction(StrEnum):
    PROPOSE = "PROPOSE"
    FORMAL_PUBLISH = "FORMAL_PUBLISH"


def _nonempty(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HoldoutContractError("HOLDOUT_INPUT_INVALID", f"{name} must be non-empty")
    return value.strip()


def _sha256(value: object, name: str) -> str:
    text = _nonempty(value, name)
    if not _SHA256.fullmatch(text):
        raise HoldoutContractError("HOLDOUT_INPUT_INVALID", f"{name} must be lowercase SHA-256")
    return text


def _canonical_document(value: object, name: str) -> str:
    if hasattr(value, "canonical_payload") and callable(value.canonical_payload):
        value = value.canonical_payload()
    try:
        rendered = canonical_json(value)
    except (TypeError, ValueError) as exc:
        raise HoldoutContractError("HOLDOUT_INPUT_INVALID", f"{name} is not canonical: {exc}") from exc
    if rendered in {"null", '""', "[]", "{}"}:
        raise HoldoutContractError("HOLDOUT_INPUT_INVALID", f"{name} must not be empty")
    return rendered


def _document_value(rendered: str) -> Any:
    return json.loads(rendered)


def _actual_dates(values: Sequence[date | str]) -> tuple[str, ...]:
    normalized: set[str] = set()
    for value in values:
        try:
            parsed = value if isinstance(value, date) else date.fromisoformat(str(value))
        except (TypeError, ValueError) as exc:
            raise HoldoutContractError("HOLDOUT_INPUT_INVALID", "actual_dates must be ISO dates") from exc
        normalized.add(parsed.isoformat())
    if not normalized:
        raise HoldoutContractError("HOLDOUT_INPUT_INVALID", "actual_dates must not be empty")
    return tuple(sorted(normalized))


def _universe(values: Sequence[str]) -> tuple[str, ...]:
    normalized = tuple(sorted({_nonempty(value, "universe member") for value in values}))
    if not normalized:
        raise HoldoutContractError("HOLDOUT_INPUT_INVALID", "universe must not be empty")
    return normalized


@dataclass(frozen=True, slots=True)
class HoldoutPopulationIdentity:
    """Server-derived identity of the underlying holdout population."""

    snapshot_json: str
    actual_dates: tuple[str, ...]
    universe: tuple[str, ...]
    label_policy_json: str
    execution_policy_json: str
    schema_version: int = HOLDOUT_IDENTITY_SCHEMA_VERSION

    @classmethod
    def build(
        cls,
        *,
        snapshot: object,
        actual_dates: Sequence[date | str],
        universe: Sequence[str],
        label_policy: object,
        execution_policy: object,
    ) -> "HoldoutPopulationIdentity":
        return cls(
            snapshot_json=_canonical_document(snapshot, "snapshot"),
            actual_dates=_actual_dates(actual_dates),
            universe=_universe(universe),
            label_policy_json=_canonical_document(label_policy, "label_policy"),
            execution_policy_json=_canonical_document(execution_policy, "execution_policy"),
        )

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "snapshot": _document_value(self.snapshot_json),
            "actual_dates": list(self.actual_dates),
            "universe": list(self.universe),
            "label_policy": _document_value(self.label_policy_json),
            "execution_policy": _document_value(self.execution_policy_json),
        }

    @property
    def population_fingerprint(self) -> str:
        return canonical_fingerprint(self.canonical_payload())

    @property
    def snapshot_fingerprint(self) -> str:
        return canonical_fingerprint(_document_value(self.snapshot_json))

    @property
    def actual_dates_hash(self) -> str:
        return canonical_fingerprint(list(self.actual_dates))

    @property
    def universe_hash(self) -> str:
        return canonical_fingerprint(list(self.universe))

    @property
    def label_policy_fingerprint(self) -> str:
        return canonical_fingerprint(_document_value(self.label_policy_json))

    @property
    def execution_policy_fingerprint(self) -> str:
        return canonical_fingerprint(_document_value(self.execution_policy_json))


@dataclass(frozen=True, slots=True)
class HoldoutClaimRequest:
    identity: HoldoutPopulationIdentity
    experiment_id: str
    hypothesis_id: str
    research_family_fingerprint: str
    principal_id: str
    caller_token: str | None = None
    supplied_population_fingerprint: str | None = None

    def __post_init__(self) -> None:
        _nonempty(self.experiment_id, "experiment_id")
        _nonempty(self.hypothesis_id, "hypothesis_id")
        _sha256(self.research_family_fingerprint, "research_family_fingerprint")
        _nonempty(self.principal_id, "principal_id")
        if self.caller_token is not None:
            _nonempty(self.caller_token, "caller_token")
        if (
            self.supplied_population_fingerprint is not None
            and self.supplied_population_fingerprint != self.identity.population_fingerprint
        ):
            raise HoldoutContractError(
                "HOLDOUT_CALLER_IDENTITY_REJECTED",
                "caller-supplied identity does not match the server-derived population",
            )


@dataclass(frozen=True, slots=True)
class HoldoutClaimRecord:
    population_fingerprint: str
    experiment_id: str
    hypothesis_id: str
    research_family_fingerprint: str
    principal_id: str
    caller_token_hash: str | None
    claim_revision: int = 1


@dataclass(frozen=True, slots=True)
class HoldoutClaimCAS:
    """Insert-if-absent command; adapters map conflict to zero affected rows."""

    population_fingerprint: str
    expected_claim_revision: int
    next_claim_revision: int
    values: Mapping[str, Any]

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "population_fingerprint": self.population_fingerprint,
            "expected_claim_revision": self.expected_claim_revision,
            "next_claim_revision": self.next_claim_revision,
            "values": canonicalize(self.values),
        }


def prepare_holdout_claim(request: HoldoutClaimRequest) -> HoldoutClaimCAS:
    """Prepare the sole canonical insert; the caller token is audit-only."""

    identity = request.identity
    token_hash = (
        hashlib.sha256(request.caller_token.encode("utf-8")).hexdigest()
        if request.caller_token is not None
        else None
    )
    return HoldoutClaimCAS(
        population_fingerprint=identity.population_fingerprint,
        expected_claim_revision=0,
        next_claim_revision=1,
        values={
            # Reuse the legacy primary key with a server-derived value for all new claims.
            "holdout_fingerprint": identity.population_fingerprint,
            "holdout_population_fingerprint": identity.population_fingerprint,
            "identity_schema_version": identity.schema_version,
            "verification_status": "canonical_verified",
            "snapshot_id": identity.snapshot_fingerprint,
            "signal_dates_hash": identity.actual_dates_hash,
            "actual_signal_dates_json": canonical_json(list(identity.actual_dates)),
            "universe_hash": identity.universe_hash,
            "universe_json": canonical_json(list(identity.universe)),
            "label_policy_fingerprint": identity.label_policy_fingerprint,
            "label_policy_json": identity.label_policy_json,
            "execution_policy_fingerprint": identity.execution_policy_fingerprint,
            "execution_policy_json": identity.execution_policy_json,
            "experiment_id": request.experiment_id,
            "hypothesis_id": request.hypothesis_id,
            "research_family_fingerprint": request.research_family_fingerprint,
            "claimed_by_principal_id": request.principal_id,
            "caller_token_hash": token_hash,
            "claim_revision": 1,
            "phase": "untouched_holdout",
            "status": "consumed",
        },
    )


@dataclass(frozen=True, slots=True)
class HoldoutClaimResult:
    accepted: bool
    record: HoldoutClaimRecord | None
    reason_codes: tuple[str, ...]


def apply_holdout_claim_cas(
    current: HoldoutClaimRecord | None,
    command: HoldoutClaimCAS,
) -> HoldoutClaimResult:
    """Pure oracle for DB unique-key/CAS behavior under any interleaving."""

    if current is not None or command.expected_claim_revision != 0:
        return HoldoutClaimResult(False, current, ("HOLDOUT_POPULATION_ALREADY_CONSUMED",))
    values = command.values
    record = HoldoutClaimRecord(
        population_fingerprint=command.population_fingerprint,
        experiment_id=str(values["experiment_id"]),
        hypothesis_id=str(values["hypothesis_id"]),
        research_family_fingerprint=str(values["research_family_fingerprint"]),
        principal_id=str(values["claimed_by_principal_id"]),
        caller_token_hash=(str(values["caller_token_hash"]) if values["caller_token_hash"] else None),
        claim_revision=command.next_claim_revision,
    )
    return HoldoutClaimResult(True, record, ())


def claim_holdout(
    current: HoldoutClaimRecord | None,
    request: HoldoutClaimRequest,
) -> HoldoutClaimResult:
    return apply_holdout_claim_cas(current, prepare_holdout_claim(request))


@dataclass(frozen=True, slots=True)
class ResearchFamilyDefinition:
    research_family_id: str
    holdout_population_fingerprint: str
    baseline_policy_fingerprint: str
    hypothesis_family: str
    candidate_fingerprints: tuple[str, ...]
    metrics_json: str
    thresholds_json: str
    allowed_evidence_scopes: tuple[EvidenceScope, ...]

    @classmethod
    def build(
        cls,
        *,
        research_family_id: str,
        holdout_population_fingerprint: str,
        baseline_policy_fingerprint: str,
        hypothesis_family: str,
        candidate_fingerprints: Sequence[str],
        metrics: object,
        thresholds: object,
        allowed_evidence_scopes: Sequence[EvidenceScope | str],
    ) -> "ResearchFamilyDefinition":
        candidates = tuple(sorted({_sha256(value, "candidate_fingerprint") for value in candidate_fingerprints}))
        if not candidates:
            raise HoldoutContractError("RESEARCH_FAMILY_INVALID", "candidate set must not be empty")
        try:
            scopes = tuple(sorted({EvidenceScope(value) for value in allowed_evidence_scopes}, key=lambda x: x.value))
        except (TypeError, ValueError) as exc:
            raise HoldoutContractError("RELEASE_CLASSIFICATION_INVALID", "invalid allowed evidence scope") from exc
        if not scopes:
            raise HoldoutContractError("RESEARCH_FAMILY_INVALID", "allowed scopes must not be empty")
        return cls(
            research_family_id=_nonempty(research_family_id, "research_family_id"),
            holdout_population_fingerprint=_sha256(holdout_population_fingerprint, "holdout_population_fingerprint"),
            baseline_policy_fingerprint=_sha256(baseline_policy_fingerprint, "baseline_policy_fingerprint"),
            hypothesis_family=_nonempty(hypothesis_family, "hypothesis_family"),
            candidate_fingerprints=candidates,
            metrics_json=_canonical_document(metrics, "metrics"),
            thresholds_json=_canonical_document(thresholds, "thresholds"),
            allowed_evidence_scopes=scopes,
        )

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "research_family_id": self.research_family_id,
            "holdout_population_fingerprint": self.holdout_population_fingerprint,
            "baseline_policy_fingerprint": self.baseline_policy_fingerprint,
            "hypothesis_family": self.hypothesis_family,
            "candidate_fingerprints": list(self.candidate_fingerprints),
            "metrics": _document_value(self.metrics_json),
            "thresholds": _document_value(self.thresholds_json),
            "allowed_evidence_scopes": [scope.value for scope in self.allowed_evidence_scopes],
        }

    @property
    def research_family_fingerprint(self) -> str:
        return canonical_fingerprint(self.canonical_payload())

    @property
    def candidate_manifest_hash(self) -> str:
        return canonical_fingerprint(list(self.candidate_fingerprints))


@dataclass(frozen=True, slots=True)
class ResearchFamilyFreeze:
    definition: ResearchFamilyDefinition
    frozen_at: str
    unsealed_at: str
    freeze_revision: int = 1

    @property
    def research_family_fingerprint(self) -> str:
        return self.definition.research_family_fingerprint


def freeze_research_family(
    current: ResearchFamilyFreeze | None,
    proposed: ResearchFamilyDefinition,
    *,
    frozen_at: str,
) -> ResearchFamilyFreeze:
    """Freeze exactly once; identical retries reuse, mutations fail closed."""

    moment = _nonempty(frozen_at, "frozen_at")
    if current is None:
        return ResearchFamilyFreeze(proposed, moment, moment)
    if current.definition == proposed:
        return current
    raise HoldoutContractError(
        "RESEARCH_FAMILY_FROZEN",
        "baseline, candidates, metrics, thresholds and allowed scopes are immutable after unseal",
    )


@dataclass(frozen=True, slots=True)
class EvidenceClassification:
    evidence_scope: EvidenceScope
    research_path: ResearchPathClassification
    production_eligibility: bool


def classify_evidence(
    evidence_scope: EvidenceScope | str | None,
    research_path: ResearchPathClassification | str | None,
    *,
    requested_production_eligibility: bool,
) -> EvidenceClassification:
    try:
        scope = EvidenceScope(evidence_scope)
        path = ResearchPathClassification(research_path)
    except (TypeError, ValueError) as exc:
        raise HoldoutContractError(
            "RELEASE_CLASSIFICATION_INVALID",
            "evidence scope and research path must be independent authoritative enums",
        ) from exc
    eligible = bool(requested_production_eligibility)
    if path in {ResearchPathClassification.EXPLORATORY, ResearchPathClassification.PATH_A}:
        eligible = False
    return EvidenceClassification(scope, path, eligible)


@dataclass(frozen=True, slots=True)
class ReleaseEligibilityInput:
    evidence_scope: str
    research_path: str
    production_eligibility: bool
    research_family_id: str
    complete_provenance_id: str
    complete_provenance_valid: bool
    evidence_package_id: str
    current_policy_id: str
    current_state_id: str
    current_deployment_id: str
    preregistered_gate_manifest_id: str


@dataclass(frozen=True, slots=True)
class EvidencePackageSnapshot:
    evidence_package_id: str
    research_family_id: str
    complete_provenance_id: str
    policy_id: str
    state_id: str
    deployment_id: str
    gate_manifest_id: str
    sample_count: int
    distinct_signal_days: int
    coverage: float
    confidence_interval_low: float
    confidence_interval_high: float
    confidence_valid: bool
    mdd_regression: float
    multiple_testing_passed: bool

    def __post_init__(self) -> None:
        for field_name in (
            "evidence_package_id", "research_family_id", "complete_provenance_id",
            "policy_id", "state_id", "deployment_id", "gate_manifest_id",
        ):
            _nonempty(getattr(self, field_name), field_name)
        if self.sample_count < 0 or self.distinct_signal_days < 0:
            raise HoldoutContractError("RELEASE_INPUT_INVALID", "counts must be non-negative")
        for field_name in ("coverage", "confidence_interval_low", "confidence_interval_high", "mdd_regression"):
            if not math.isfinite(getattr(self, field_name)):
                raise HoldoutContractError("RELEASE_INPUT_INVALID", f"{field_name} must be finite")


@dataclass(frozen=True, slots=True)
class ReleaseThresholds:
    minimum_trades: int
    minimum_distinct_days: int
    minimum_coverage: float
    minimum_confidence_lower_bound: float
    maximum_mdd_regression: float

    def __post_init__(self) -> None:
        if self.minimum_trades < 0 or self.minimum_distinct_days < 0:
            raise HoldoutContractError("RELEASE_INPUT_INVALID", "threshold counts must be non-negative")
        for field_name in ("minimum_coverage", "minimum_confidence_lower_bound", "maximum_mdd_regression"):
            if not math.isfinite(getattr(self, field_name)):
                raise HoldoutContractError("RELEASE_INPUT_INVALID", f"{field_name} must be finite")


@dataclass(frozen=True, slots=True)
class ReleaseEligibilityDecision:
    decision_id: str
    input_snapshot: ReleaseEligibilityInput
    eligible: bool
    gate_results: tuple[tuple[str, bool], ...]
    reason_codes: tuple[str, ...]
    schema_version: int = RELEASE_DECISION_SCHEMA_VERSION

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "input_snapshot": canonicalize(self.input_snapshot),
            "eligible": self.eligible,
            "gate_results": [[code, passed] for code, passed in self.gate_results],
            "reason_codes": list(self.reason_codes),
        }


def _decision(
    request: ReleaseEligibilityInput,
    gates: tuple[tuple[str, bool], ...],
    reasons: tuple[str, ...],
) -> ReleaseEligibilityDecision:
    eligible = all(passed for _, passed in gates)
    payload = {
        "schema_version": RELEASE_DECISION_SCHEMA_VERSION,
        "input_snapshot": canonicalize(request),
        "eligible": eligible,
        "gate_results": [[code, passed] for code, passed in gates],
        "reason_codes": list(reasons),
    }
    return ReleaseEligibilityDecision(canonical_fingerprint(payload), request, eligible, gates, reasons)


def decide_release_eligibility(
    request: ReleaseEligibilityInput,
    family: ResearchFamilyDefinition,
    package: EvidencePackageSnapshot,
    thresholds: ReleaseThresholds,
) -> ReleaseEligibilityDecision:
    """Return a deterministic, explicit conjunction in stable gate order."""

    try:
        classification = classify_evidence(
            request.evidence_scope,
            request.research_path,
            requested_production_eligibility=request.production_eligibility,
        )
    except HoldoutContractError:
        gates = (("RELEASE_CLASSIFICATION_VALID", False),)
        return _decision(request, gates, ("RELEASE_CLASSIFICATION_INVALID",))

    effective = replace(
        request,
        evidence_scope=classification.evidence_scope.value,
        research_path=classification.research_path.value,
        production_eligibility=classification.production_eligibility,
    )
    confidence_passed = (
        package.confidence_valid
        and package.confidence_interval_low <= package.confidence_interval_high
        and package.confidence_interval_low > thresholds.minimum_confidence_lower_bound
    )
    gates = (
        ("SCOPE_ALLOWED", classification.evidence_scope in family.allowed_evidence_scopes),
        ("GOVERNED_PATH", classification.research_path is ResearchPathClassification.GOVERNED_RELEASE),
        ("PRODUCTION_ELIGIBLE", classification.production_eligibility is True),
        ("PROVENANCE_VALID", effective.complete_provenance_valid is True),
        ("PROVENANCE_MATCH", package.complete_provenance_id == effective.complete_provenance_id),
        ("PACKAGE_MATCH", package.evidence_package_id == effective.evidence_package_id),
        ("RESEARCH_FAMILY_MATCH", package.research_family_id == family.research_family_id == effective.research_family_id),
        ("CURRENT_POLICY_MATCH", package.policy_id == effective.current_policy_id),
        ("CURRENT_STATE_MATCH", package.state_id == effective.current_state_id),
        ("CURRENT_DEPLOYMENT_MATCH", package.deployment_id == effective.current_deployment_id),
        ("GATE_MANIFEST_MATCH", package.gate_manifest_id == effective.preregistered_gate_manifest_id),
        ("MIN_TRADES", package.sample_count >= thresholds.minimum_trades),
        ("MIN_DISTINCT_DAYS", package.distinct_signal_days >= thresholds.minimum_distinct_days),
        ("MIN_COVERAGE", package.coverage >= thresholds.minimum_coverage),
        ("CONFIDENCE", confidence_passed),
        ("MDD_REGRESSION", package.mdd_regression <= thresholds.maximum_mdd_regression),
        ("MULTIPLE_TESTING", package.multiple_testing_passed is True),
    )
    reasons = tuple(code for code, passed in gates if not passed)
    return _decision(effective, gates, reasons)


@dataclass(frozen=True, slots=True)
class PrincipalAuthority:
    principal_id: str
    roles: tuple[AuthorityRole, ...]

    @classmethod
    def build(cls, principal_id: str, roles: Sequence[AuthorityRole | str]) -> "PrincipalAuthority":
        try:
            normalized = tuple(sorted({AuthorityRole(role) for role in roles}, key=lambda role: role.value))
        except (TypeError, ValueError) as exc:
            raise HoldoutContractError("RELEASE_AUTHORITY_INVALID", "unknown authority role") from exc
        return cls(_nonempty(principal_id, "principal_id"), normalized)


@dataclass(frozen=True, slots=True)
class ReleaseAuthorityDecision:
    authority_decision_id: str
    release_decision_id: str
    principal_id: str
    action: ReleaseAction
    authorized: bool
    reason_codes: tuple[str, ...]
    roles: tuple[AuthorityRole, ...]


def authorize_release_action(
    release_decision: ReleaseEligibilityDecision,
    principal: PrincipalAuthority,
    action: ReleaseAction | str,
) -> ReleaseAuthorityDecision:
    try:
        normalized_action = ReleaseAction(action)
    except (TypeError, ValueError) as exc:
        raise HoldoutContractError("RELEASE_AUTHORITY_INVALID", "unknown release action") from exc
    reasons: list[str] = []
    if normalized_action is ReleaseAction.PROPOSE:
        authorized = bool(
            {AuthorityRole.RESEARCH_PROPOSER, AuthorityRole.FORMAL_PUBLISHER}.intersection(principal.roles)
        )
        if not authorized:
            reasons.append("RESEARCH_PROPOSAL_AUTHORITY_REQUIRED")
    else:
        if not release_decision.eligible:
            reasons.append("RELEASE_NOT_ELIGIBLE")
        if AuthorityRole.FORMAL_PUBLISHER not in principal.roles:
            reasons.append("FORMAL_PUBLISH_AUTHORITY_REQUIRED")
        authorized = not reasons
    payload = {
        "release_decision_id": release_decision.decision_id,
        "principal_id": principal.principal_id,
        "action": normalized_action.value,
        "authorized": authorized,
        "reason_codes": reasons,
        "roles": [role.value for role in principal.roles],
    }
    return ReleaseAuthorityDecision(
        canonical_fingerprint(payload),
        release_decision.decision_id,
        principal.principal_id,
        normalized_action,
        authorized,
        tuple(reasons),
        principal.roles,
    )


__all__ = [
    "AuthorityRole", "EvidenceClassification", "EvidencePackageSnapshot", "EvidenceScope",
    "HoldoutClaimCAS", "HoldoutClaimRecord", "HoldoutClaimRequest", "HoldoutClaimResult",
    "HoldoutContractError", "HoldoutPopulationIdentity", "PrincipalAuthority",
    "ReleaseAction", "ReleaseAuthorityDecision", "ReleaseEligibilityDecision",
    "ReleaseEligibilityInput", "ReleaseThresholds", "ResearchFamilyDefinition",
    "ResearchFamilyFreeze", "ResearchPathClassification", "apply_holdout_claim_cas",
    "authorize_release_action", "claim_holdout", "classify_evidence",
    "decide_release_eligibility", "freeze_research_family", "prepare_holdout_claim",
]
