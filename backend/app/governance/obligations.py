"""Correctness obligations: the Property 1–54 registry and proof routes.

Every obligation needs a *current* successful proof route: either passing
property-test evidence bound to the live revision binding, or audited
equivalent evidence whose Equivalence_Assessment is still VALID.  Code,
dependency, domain, oracle, or binding drift invalidates an assessment; an
obligation with no valid route makes its optional authoring task mandatory and
blocks CODE_COMPLETE.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum

from app.governance.binding import RevisionBinding
from app.governance.envelope import GovernanceError


MINIMUM_PROPERTY_ITERATIONS = 100


@dataclass(frozen=True, slots=True)
class CorrectnessObligation:
    obligation_id: str
    property_number: int
    title: str
    requirement_refs: tuple[str, ...]
    input_domain_key: str
    oracle_key: str
    revision_binding_scope: str
    minimum_iterations: int = MINIMUM_PROPERTY_ITERATIONS
    property_test_module: str = ""

    def __post_init__(self) -> None:
        if not re.fullmatch(r"CO-PR-0[0-5][0-9]", self.obligation_id or ""):
            raise GovernanceError(
                "invalid_obligation_id",
                f"obligation_id {self.obligation_id!r} must look like CO-PR-0NN",
            )
        if not (1 <= self.property_number <= 54):
            raise GovernanceError(
                "invalid_property_number",
                "property_number must be within 1..54",
            )
        if self.minimum_iterations < MINIMUM_PROPERTY_ITERATIONS:
            raise GovernanceError(
                "iterations_below_minimum",
                f"minimum_iterations must be >= {MINIMUM_PROPERTY_ITERATIONS}",
            )

    def canonical_payload(self) -> dict[str, object]:
        return {
            "input_domain_key": self.input_domain_key,
            "minimum_iterations": self.minimum_iterations,
            "obligation_id": self.obligation_id,
            "oracle_key": self.oracle_key,
            "property_number": self.property_number,
            "property_test_module": self.property_test_module,
            "requirement_refs": list(self.requirement_refs),
            "revision_binding_scope": self.revision_binding_scope,
            "title": self.title,
        }


def _ob(
    number: int,
    title: str,
    refs: tuple[str, ...],
    domain: str,
    oracle: str,
    scope: str,
    module: str,
) -> CorrectnessObligation:
    return CorrectnessObligation(
        obligation_id=f"CO-PR-{number:03d}",
        property_number=number,
        title=title,
        requirement_refs=refs,
        input_domain_key=domain,
        oracle_key=oracle,
        revision_binding_scope=scope,
        property_test_module=module,
    )


_GOV = "s0_governance"
_GOV_MODULE = "backend/tests/governance/test_property_s0_governance.py"

CORRECTNESS_OBLIGATIONS: tuple[CorrectnessObligation, ...] = (
    _ob(1, "Audit, reuse, and gap admission are total", ("1.1", "1.7", "1.8"),
        "domain://governance.audit_inventory", "oracle://governance.total_decision_coverage", _GOV, _GOV_MODULE),
    _ob(2, "Baseline freeze is ordered, immutable, and append-only verified", ("1.2", "1.3", "1.4", "1.5", "1.6"),
        "domain://governance.baseline_freeze", "oracle://governance.immutable_append_only", _GOV, _GOV_MODULE),
    _ob(3, "Traceability is complete and referentially valid", ("1.9",),
        "domain://governance.traceability", "oracle://governance.reference_resolution", _GOV, _GOV_MODULE),
    _ob(4, "Golden harness is deterministic", ("1.10",),
        "domain://testkit.golden_harness", "oracle://testkit.byte_identical_rerun", _GOV, _GOV_MODULE),
    _ob(5, "Safe initialization and unlock rules never imply completion", ("1.11", "1.12", "1.13"),
        "domain://governance.state_machines", "oracle://governance.no_implied_completion", _GOV, _GOV_MODULE),
    _ob(6, "Evidence classification is provenance-derived", ("2.1", "2.2", "2.3", "2.5", "2.9"),
        "domain://evidence.identity_builder", "oracle://evidence.provenance_only_classification", "s1_evidence",
        "backend/tests/evidence/test_property_s1_evidence.py"),
    _ob(7, "Historical preservation and classification backfill are idempotent", ("2.4", "2.10", "2.11"),
        "domain://evidence.classification_backfill", "oracle://evidence.idempotent_preserving_backfill", "s1_evidence",
        "backend/tests/evidence/test_property_s1_evidence.py"),
    _ob(8, "LIVE consumers contain exactly eligible forward evidence", ("2.6", "4.3"),
        "domain://evidence.live_selectors", "oracle://evidence.exact_live_membership", "s1_evidence",
        "backend/tests/evidence/test_property_s1_evidence.py"),
    _ob(9, "Historical queries preserve class and business-date order", ("2.7",),
        "domain://evidence.historical_query", "oracle://evidence.class_and_date_order", "s1_evidence",
        "backend/tests/evidence/test_property_s1_evidence.py"),
    _ob(10, "Historical reads obey as-of causality", ("2.8",),
        "domain://evidence.historical_query", "oracle://evidence.as_of_causality", "s1_evidence",
        "backend/tests/evidence/test_property_s1_evidence.py"),
    _ob(11, "ExecutionPolicy identity is deterministic and versioned", ("3.1", "3.5"),
        "domain://execution.policy_registry", "oracle://execution.deterministic_versioned_identity", "s2_execution",
        "backend/tests/execution/test_property_s2_execution.py"),
    _ob(12, "Canonical adapters preserve parity and T+1", ("3.2", "3.3", "3.4"),
        "domain://execution.adapters", "oracle://execution.trade_by_trade_parity", "s2_execution",
        "backend/tests/execution/test_property_s2_execution.py"),
    _ob(13, "No position can bypass an applicable cap", ("3.6", "3.7", "7.4"),
        "domain://execution.position_caps", "oracle://execution.min_all_applicable_caps", "s2_execution",
        "backend/tests/execution/test_property_s2_execution.py"),
    _ob(14, "Canonical execution matches registered edge semantics", ("3.8",),
        "domain://execution.edge_semantics", "oracle://execution.registered_edge_oracle", "s2_execution",
        "backend/tests/execution/test_property_s2_execution.py"),
    _ob(15, "Active strategies map one-to-one to truthful profiles", ("4.1", "4.2"),
        "domain://profiles.canonical_profile", "oracle://profiles.truthful_accounting", "s3_profiles",
        "backend/tests/profiles/test_property_s3_profiles.py"),
    _ob(16, "Champion state fails closed on insufficient or stale evidence", ("4.4", "4.5", "4.8"),
        "domain://profiles.champion_state", "oracle://profiles.fail_closed_champion", "s3_profiles",
        "backend/tests/profiles/test_property_s3_profiles.py"),
    _ob(17, "Evidence packages preserve population accounting", ("4.6", "4.7"),
        "domain://profiles.evidence_package", "oracle://profiles.population_accounting", "s3_profiles",
        "backend/tests/profiles/test_property_s3_profiles.py"),
    _ob(18, "Holdout identity is canonical and single-use", ("5.1", "5.2", "5.3"),
        "domain://holdout.population_identity", "oracle://holdout.canonical_single_use", "s4_holdout",
        "backend/tests/holdout/test_property_s4_holdout.py"),
    _ob(19, "Research family freeze is immutable", ("5.4",),
        "domain://holdout.research_family", "oracle://holdout.freeze_immutability", "s4_holdout",
        "backend/tests/holdout/test_property_s4_holdout.py"),
    _ob(20, "Evidence scope and research path remain orthogonal and valid", ("5.5", "5.6", "5.7", "5.8"),
        "domain://holdout.evidence_scope", "oracle://holdout.orthogonal_valid_classification", "s4_holdout",
        "backend/tests/holdout/test_property_s4_holdout.py"),
    _ob(21, "Release eligibility is a deterministic conjunction", ("5.9", "5.10", "5.11", "5.12"),
        "domain://release.eligibility", "oracle://release.deterministic_conjunction", "s4_holdout",
        "backend/tests/holdout/test_property_s4_holdout.py"),
    _ob(22, "Research authority cannot publish", ("5.13",),
        "domain://release.authority", "oracle://release.research_cannot_publish", "s4_holdout",
        "backend/tests/holdout/test_property_s4_holdout.py"),
    _ob(23, "Data quality decisions follow declared contracts", ("6.1", "6.2", "6.3", "6.10"),
        "domain://quality.declared_contracts", "oracle://quality.contract_driven_decision", "s5_quality",
        "backend/tests/quality/test_property_s5_quality.py"),
    _ob(24, "Outcome identity is idempotent and append-only", ("6.4", "6.5", "6.6"),
        "domain://outcome.fingerprint", "oracle://outcome.idempotent_append_only", "s5_quality",
        "backend/tests/quality/test_property_s5_quality.py"),
    _ob(25, "Current projection is atomic and preserves audit history", ("6.7", "6.9"),
        "domain://outcome.current_projection", "oracle://outcome.atomic_cas_preserving", "s5_quality",
        "backend/tests/quality/test_property_s5_quality.py"),
    _ob(26, "Stale recomputation is exact", ("6.8",),
        "domain://outcome.stale_recompute", "oracle://outcome.exact_stale_selection", "s5_quality",
        "backend/tests/quality/test_property_s5_quality.py"),
    _ob(27, "Risk policy, state, and decisions are versioned append-only facts", ("7.1", "7.2", "7.7"),
        "domain://risk.policy_state", "oracle://risk.versioned_append_only_facts", "s6_risk",
        "backend/tests/risk/test_property_s6_risk.py"),
    _ob(28, "Risk reservation is atomic and respects loss and locked exposure", ("7.3", "7.5", "7.6", "7.8"),
        "domain://risk.reservation", "oracle://risk.atomic_bounded_reservation", "s6_risk",
        "backend/tests/risk/test_property_s6_risk.py"),
    _ob(29, "Control-plane ownership is exclusive", ("8.1", "8.2"),
        "domain://scheduler.owner_lease", "oracle://scheduler.exclusive_ownership", "s7_scheduler",
        "backend/tests/scheduler/test_property_s7_scheduler.py"),
    _ob(30, "Scheduled occurrence claims are at-most-once", ("8.4", "8.5"),
        "domain://scheduler.occurrence_claim", "oracle://scheduler.at_most_once_claim", "s7_scheduler",
        "backend/tests/scheduler/test_property_s7_scheduler.py"),
    _ob(31, "Misfire planning is bounded", ("8.7",),
        "domain://scheduler.misfire", "oracle://scheduler.bounded_catch_up", "s7_scheduler",
        "backend/tests/scheduler/test_property_s7_scheduler.py"),
    _ob(32, "Dashboard selection and staleness are truthful", ("9.2", "9.5"),
        "domain://dashboard.projection", "oracle://dashboard.truthful_selection_staleness", "s8_dashboard",
        "backend/tests/dashboard/test_property_s8_dashboard.py"),
    _ob(33, "ReleaseScopeManifest is total and canonical", ("10.1", "10.2", "10.3", "10.4", "10.5"),
        "domain://governance.release_scope", "oracle://governance.total_canonical_scope", _GOV,
        "backend/tests/governance/test_property_s9_release.py"),
    _ob(34, "Release scope deterministically constructs completion work", ("10.6", "10.7", "10.8", "11.9"),
        "domain://governance.release_scope", "oracle://governance.deterministic_work_set", _GOV,
        "backend/tests/governance/test_property_s9_release.py"),
    _ob(35, "Release scope revisions are immutable and precede behavior", ("10.9", "10.27"),
        "domain://governance.release_scope", "oracle://governance.immutable_prebehavior_revision", _GOV,
        "backend/tests/governance/test_property_s9_release.py"),
    _ob(36, "Stable flag registry bindings are exact", ("10.10", "10.11", "10.12"),
        "domain://governance.flag_registry", "oracle://governance.exact_flag_bindings", _GOV,
        "backend/tests/governance/test_property_s9_release.py"),
    _ob(37, "Migration plans are read-only prerequisites and additive-only", ("10.13", "10.14", "10.20"),
        "domain://migration.plan", "oracle://migration.read_only_additive", "s9_release",
        "backend/tests/governance/test_property_s9_release.py"),
    _ob(38, "Migration execution requires every safety gate and is repeatable", ("10.15", "10.16", "10.17", "10.18", "10.19"),
        "domain://migration.execution", "oracle://migration.all_gates_repeatable", "s9_release",
        "backend/tests/governance/test_property_s9_release.py"),
    _ob(39, "Approvals are exact and non-transitive", ("10.16", "10.21", "10.22", "11.11"),
        "domain://governance.approval", "oracle://governance.exact_nontransitive_approval", _GOV,
        "backend/tests/governance/test_property_s9_release.py"),
    _ob(40, "Shadow threshold failure preserves the old read path", ("10.22", "10.23"),
        "domain://cutover.shadow_threshold", "oracle://cutover.old_path_preserved", "s9_release",
        "backend/tests/governance/test_property_s9_release.py"),
    _ob(41, "Read-path rollback preserves append-only data", ("10.24",),
        "domain://cutover.rollback", "oracle://cutover.append_only_preserved", "s9_release",
        "backend/tests/governance/test_property_s9_release.py"),
    _ob(42, "Enforcement activation requires its exact evidence and dependency", ("7.9", "10.25"),
        "domain://enforcement.activation", "oracle://enforcement.exact_evidence_dependency", "s9_release",
        "backend/tests/governance/test_property_s9_release.py"),
    _ob(43, "Persisted operational artifacts never contain secrets", ("10.26", "12.5", "13.15"),
        "domain://governance.artifacts", "oracle://governance.no_secret_material", _GOV,
        "backend/tests/governance/test_property_s9_release.py"),
    _ob(44, "Code, environment, and activation states remain independent", ("11.1", "11.7"),
        "domain://governance.state_machines", "oracle://governance.orthogonal_states", _GOV,
        "backend/tests/governance/test_property_s9_release.py"),
    _ob(45, "Task 1.3 code completion is revision-bound and invalidated exactly", ("11.2", "11.4", "11.5"),
        "domain://governance.revision_binding", "oracle://governance.exact_binding_invalidation", _GOV,
        "backend/tests/evidence/test_property_s1_revision_binding.py"),
    _ob(46, "Task 1.3 remains incomplete without all revision-matched Layer 2 evidence", ("11.6", "11.8"),
        "domain://governance.revision_binding", "oracle://governance.all_l2_evidence_required", _GOV,
        "backend/tests/evidence/test_property_s1_revision_binding.py"),
    _ob(47, "Spec completion is exactly the acceptance conjunction", ("11.9", "11.10", "11.11", "11.12"),
        "domain://governance.completion", "oracle://governance.exact_acceptance_conjunction", _GOV,
        "backend/tests/governance/test_property_s9_release.py"),
    _ob(48, "Production-ready requires all six ACTIVE and ENFORCE states", ("11.13", "11.14"),
        "domain://governance.production_ready", "oracle://governance.six_active_enforce", _GOV,
        "backend/tests/governance/test_property_s9_release.py"),
    _ob(49, "Final evidence report is complete and deterministic", ("11.15",),
        "domain://governance.final_report", "oracle://governance.complete_deterministic_report", _GOV,
        "backend/tests/governance/test_property_s9_release.py"),
    _ob(50, "Deferred and brokerage scope is fail-closed", ("12.1", "12.2", "12.3", "12.4", "12.5", "12.6", "12.7"),
        "domain://governance.scope_guard", "oracle://governance.fail_closed_scope", _GOV, _GOV_MODULE),
    _ob(51, "Preregistered manifests are immutable and temporally valid", ("13.1", "13.2", "13.3", "13.4", "13.5", "13.6", "13.9"),
        "domain://governance.preregistered_manifests", "oracle://governance.immutable_temporal_manifest", _GOV, _GOV_MODULE),
    _ob(52, "Layer 2 evidence matches every preregistered binding", ("13.7", "13.8"),
        "domain://governance.layer2_evidence", "oracle://governance.all_bindings_matched", _GOV,
        "backend/tests/governance/test_property_s9_release.py"),
    _ob(53, "Every correctness obligation has a successful current proof route", ("13.10", "13.11", "13.12", "13.13"),
        "domain://governance.correctness_obligations", "oracle://governance.current_proof_route", _GOV, _GOV_MODULE),
    _ob(54, "Equivalent correctness evidence invalidates on semantic change", ("13.14",),
        "domain://governance.equivalence_assessment", "oracle://governance.semantic_change_invalidation", _GOV, _GOV_MODULE),
)


def obligation_for_property(property_number: int) -> CorrectnessObligation:
    for obligation in CORRECTNESS_OBLIGATIONS:
        if obligation.property_number == property_number:
            return obligation
    raise GovernanceError(
        "unknown_property_number",
        f"property {property_number} is not registered",
    )


def validate_obligation_registry() -> None:
    numbers = [item.property_number for item in CORRECTNESS_OBLIGATIONS]
    if sorted(numbers) != list(range(1, 55)):
        raise GovernanceError(
            "obligation_registry_incomplete",
            "obligations must cover properties 1..54 exactly once",
        )
    ids = [item.obligation_id for item in CORRECTNESS_OBLIGATIONS]
    if len(set(ids)) != 54:
        raise GovernanceError(
            "obligation_registry_incomplete",
            "obligation IDs must be unique",
        )


class ProofRouteStatus(StrEnum):
    PROVEN = "PROVEN"
    UNPROVEN = "UNPROVEN"


class EquivalenceConclusion(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"
    STALE = "STALE"


@dataclass(frozen=True, slots=True)
class PropertyTestEvidence:
    """A passing property-test run bound to one revision binding."""

    obligation_id: str
    result: str
    iterations: int
    evidence_uri: str
    revision_binding_hash: str

    def validate(self, obligation: CorrectnessObligation, binding: RevisionBinding) -> list[str]:
        mismatches: list[str] = []
        if self.obligation_id != obligation.obligation_id:
            mismatches.append("obligation_id_mismatch")
        if self.result != "PASS":
            mismatches.append("property_not_passing")
        if self.iterations < obligation.minimum_iterations:
            mismatches.append("iterations_below_minimum")
        if self.revision_binding_hash != binding.binding_hash:
            mismatches.append("revision_binding_mismatch")
        return mismatches


@dataclass(frozen=True, slots=True)
class EquivalenceAssessment:
    """Audit of one Equivalent_Correctness_Evidence candidate."""

    evidence_uri: str
    domain_same_or_stronger: bool
    oracle_equivalent: bool
    result: str
    conclusion: EquivalenceConclusion
    assessed_at: datetime
    revision_binding_hash: str
    semantic_fingerprint: str

    def validate(self, obligation: CorrectnessObligation, binding: RevisionBinding) -> list[str]:
        mismatches: list[str] = []
        if not self.domain_same_or_stronger:
            mismatches.append("domain_weaker_than_obligation")
        if not self.oracle_equivalent:
            mismatches.append("oracle_not_equivalent")
        if self.result != "PASS":
            mismatches.append("equivalent_evidence_not_passing")
        if self.conclusion is not EquivalenceConclusion.VALID:
            mismatches.append("assessment_not_valid")
        if self.revision_binding_hash != binding.binding_hash:
            mismatches.append("revision_binding_mismatch")
        return mismatches


def invalidate_assessment_on_semantic_change(
    assessment: EquivalenceAssessment,
    *,
    current_semantic_fingerprint: str,
) -> EquivalenceAssessment:
    """Any semantic drift (code/dependency/domain/oracle/binding) stales an assessment."""

    if current_semantic_fingerprint == assessment.semantic_fingerprint:
        return assessment
    return EquivalenceAssessment(
        evidence_uri=assessment.evidence_uri,
        domain_same_or_stronger=assessment.domain_same_or_stronger,
        oracle_equivalent=assessment.oracle_equivalent,
        result=assessment.result,
        conclusion=EquivalenceConclusion.STALE,
        assessed_at=assessment.assessed_at,
        revision_binding_hash=assessment.revision_binding_hash,
        semantic_fingerprint=assessment.semantic_fingerprint,
    )


def evaluate_proof_route(
    obligation: CorrectnessObligation,
    *,
    binding: RevisionBinding,
    property_evidence: tuple[PropertyTestEvidence, ...] = (),
    equivalence_assessments: tuple[EquivalenceAssessment, ...] = (),
) -> tuple[ProofRouteStatus, tuple[str, ...]]:
    """Exactly two successful paths exist; everything else stays UNPROVEN."""

    reasons: list[str] = []
    for evidence in property_evidence:
        mismatches = evidence.validate(obligation, binding)
        if not mismatches:
            return ProofRouteStatus.PROVEN, ()
        reasons.extend(f"property:{item}" for item in mismatches)
    for assessment in equivalence_assessments:
        mismatches = assessment.validate(obligation, binding)
        if not mismatches:
            return ProofRouteStatus.PROVEN, ()
        reasons.extend(f"equivalent:{item}" for item in mismatches)
    if not property_evidence and not equivalence_assessments:
        reasons.append("no_proof_route_registered")
    return ProofRouteStatus.UNPROVEN, tuple(reasons)


__all__ = [
    "CORRECTNESS_OBLIGATIONS",
    "CorrectnessObligation",
    "EquivalenceAssessment",
    "EquivalenceConclusion",
    "MINIMUM_PROPERTY_ITERATIONS",
    "ProofRouteStatus",
    "PropertyTestEvidence",
    "evaluate_proof_route",
    "invalidate_assessment_on_semantic_change",
    "obligation_for_property",
    "validate_obligation_registry",
]
