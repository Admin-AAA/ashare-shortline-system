#!/usr/bin/env python
"""Execute the one-time S0 governance freeze (tasks 0.1–0.4).

Read-only toward every production surface: this script only reads git facts
and the captured baseline artifacts, then appends immutable objects and events
into the governance store.  Re-running after a completed freeze is a no-op
that prints the existing receipt; superseding requires an explicit new
revision workflow, never a refresh of frozen objects.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.governance.events import GovernanceEventLog  # noqa: E402
from app.governance.freeze import (  # noqa: E402
    InventoryEntry,
    audit_baseline_artifact,
    freeze_s0,
)
from app.governance.scope import (  # noqa: E402
    CompletionImpact,
    EvidenceBinding,
    EvidenceBindingDirection,
    ReleaseInclusion,
    ReleaseScopeEntry,
)
from app.governance.store import GovernanceObjectStore  # noqa: E402

SPEC_DIR = REPO_ROOT / ".kiro" / "specs" / "production-evidence-risk-hardening"
ACTOR = "kimi-code-cli:freeze_s0"


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _status_lines() -> list[str]:
    return [line for line in _git("status", "--porcelain").splitlines() if line]


def _status_digest() -> str:
    from app.governance.freeze import _digest

    return _digest(_status_lines())


def _revision_exists(sha: str) -> bool:
    completed = subprocess.run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
        cwd=REPO_ROOT,
        capture_output=True,
    )
    return completed.returncode == 0


def _commits_between(old: str, new: str) -> list[str]:
    try:
        out = _git("log", "--oneline", f"{old}..{new}")
    except subprocess.CalledProcessError:
        return ["<unreachable>"]
    return [line for line in out.splitlines() if line]


def _file_hash(path: Path) -> str | None:
    import hashlib

    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _delta_note() -> str:
    return (
        "Baseline captures predate this freeze session (file mtimes and captured_at prove order). "
        "Delta between captured state and freeze time is fully enumerated: (a) upstream commits "
        "033be22..HEAD (replay display fixes), (b) prior-session hardening artifacts audited in the "
        "inventory below, (c) three regression repairs registered as gap records g1-g3, "
        "(d) the governance plane itself (app/governance, tests/governance). No baseline byte was "
        "rewritten; section digests recompute exactly."
    )


def _gap_records() -> tuple[dict, ...]:
    return (
        {
            "gap_id": "g1-evidence-identity-policy-list",
            "affected_acceptance": "S1 replay identity / test_replay_persists_explicit_identity_then_finalizes_canonical_provenance",
            "minimal_change_boundary": ["backend/app/services/evidence/identity.py"],
            "reproducible_failure": (
                "pytest tests/test_historical_reconstruction_replay.py::"
                "test_replay_persists_explicit_identity_then_finalizes_canonical_provenance "
                "failed with EvidenceIdentityError('strategy_policy must be a SHA-256 digest or "
                "expose fingerprint') at identity.py:436: replay passes strategy_fingerprints as a "
                "list of {strategy_id, strategy_version, fingerprint} mappings."
            ),
            "before_evidence": "pytest traceback captured in session log; pre-edit bytes were uncommitted and are recorded as unrecoverable",
            "resolution": "Additive normalization: Mapping entries expose .fingerprint via key lookup; list/tuple policies fold into a deterministic composite digest over canonical JSON entries. Single-digest and .fingerprint behavior unchanged.",
        },
        {
            "gap_id": "g2-model-router-stale-client-fake",
            "affected_acceptance": "pre-existing HEAD failure: test_model_route_uses_stored_key_when_editing_without_new_key",
            "minimal_change_boundary": ["backend/tests/test_model_router_service.py"],
            "reproducible_failure": (
                "Committed app/services/model_router_service.py passes default_headers=OPENAI_CLIENT_HEADERS "
                "to AsyncOpenAI; the test FakeAsyncOpenAI rejected the kwarg, returning ok=False."
            ),
            "before_evidence": "test file is tracked; pre-fix bytes equal git HEAD version",
            "resolution": "Test fake accepts default_headers to match the committed client signature. No service change.",
        },
        {
            "gap_id": "g3-research-runner-nested-policy-parse",
            "affected_acceptance": "research gateway replay: POST /api/backtests/{id}/replay returned 400 'entry must be EntryWindowPolicy'",
            "minimal_change_boundary": ["backend/app/services/backtest/research_runner.py"],
            "reproducible_failure": (
                "pytest tests/test_research_gateway_vertical_slice.py::"
                "test_real_declarative_strategy_gateway_vertical_slice_is_research_only: replay re-parsed "
                "the stored nested canonical execution_policy payload through the raw ExecutionPolicy "
                "constructor, leaving entry as a dict."
            ),
            "before_evidence": "pytest traceback in session log; pre-edit bytes were uncommitted and are recorded as unrecoverable",
            "resolution": "Nested canonical payloads parse via execution_policy_from_payload (strict, lossless-verified); legacy flat payloads keep the constructor path.",
        },
    )


def _inventory_entries() -> tuple[InventoryEntry, ...]:
    def H(rel: str) -> str | None:
        return _file_hash(REPO_ROOT / rel)

    rows: list[InventoryEntry] = [
        InventoryEntry(
            path=".kiro/specs/production-evidence-risk-hardening/baseline-task-0.1.json",
            workspace_status="??",
            content_sha256=H(".kiro/specs/production-evidence-risk-hardening/baseline-task-0.1.json"),
            decision="REUSE",
            rationale="canonical Baseline_Artifact candidate; section digests recompute, revision in history, capture order proven",
            linked_tests=("backend/tests/test_capture_production_evidence_baseline.py",),
            requirement_refs=("1.2", "1.3", "1.4", "1.5", "1.6"),
        ),
        InventoryEntry(
            path=".kiro/specs/production-evidence-risk-hardening/baseline-task-7.1.json",
            workspace_status="??",
            content_sha256=H(".kiro/specs/production-evidence-risk-hardening/baseline-task-7.1.json"),
            decision="REUSE",
            rationale="Prefect control-plane baseline; stale-worker/missing-concurrency facts must not be re-reported as current evidence",
            linked_tests=("backend/tests/test_capture_prefect_scheduler_baseline.py",),
            requirement_refs=("8.1", "8.2"),
        ),
        InventoryEntry(
            path=".kiro/specs/production-evidence-risk-hardening/migration-preflight-task-0.3.json",
            workspace_status="??",
            content_sha256=H(".kiro/specs/production-evidence-risk-hardening/migration-preflight-task-0.3.json"),
            decision="REUSE",
            rationale="read-only migration plan/preflight artifact with apply_gate=plan_only_not_authorized; holds classification plan hash 1e2255b4",
            linked_tests=("backend/tests/test_production_hardening_migration_preflight.py",),
            requirement_refs=("10.13", "10.14", "10.15"),
        ),
        InventoryEntry(
            path="backend/app/core/production_hardening_flags.py",
            workspace_status="??",
            content_sha256=H("backend/app/core/production_hardening_flags.py"),
            decision="REUSE",
            rationale="six-flag rollout mode contract with staged-transition validator; safe defaults non-enforced",
            linked_tests=("backend/tests/test_production_hardening_flags.py",),
            requirement_refs=("10.10", "10.11", "10.12"),
        ),
        InventoryEntry(
            path="backend/app/services/evidence/identity.py",
            workspace_status="??",
            content_sha256=H("backend/app/services/evidence/identity.py"),
            decision="RECONCILE",
            rationale="evidence identity builder reused; gap g1 (list policy normalization) reconciled and hash-recorded",
            linked_tests=("backend/tests/test_evidence_identity.py", "backend/tests/test_historical_reconstruction_replay.py"),
            requirement_refs=("2.1", "2.2", "2.3", "2.5"),
            gap_record_id="g1-evidence-identity-policy-list",
        ),
        InventoryEntry(
            path="backend/alembic/versions/0004_evidence_provenance.py",
            workspace_status="??",
            content_sha256=H("backend/alembic/versions/0004_evidence_provenance.py"),
            decision="REUSE",
            rationale="additive provenance migration (Task 1.3 migration contract); chained on 0003; second-run no-op covered by tests",
            linked_tests=("backend/tests/test_evidence_provenance_migration.py",),
            requirement_refs=("2.1", "10.13", "10.14"),
        ),
        InventoryEntry(
            path="backend/app/db/production_hardening_migration_plan.py",
            workspace_status="??",
            content_sha256=H("backend/app/db/production_hardening_migration_plan.py"),
            decision="REUSE",
            rationale="read-only additive migration planner with allowlist + checksum",
            linked_tests=("backend/tests/test_production_hardening_migration_preflight.py",),
            requirement_refs=("10.13", "10.14", "10.20"),
        ),
        InventoryEntry(
            path="backend/app/db/production_hardening_preflight.py",
            workspace_status="??",
            content_sha256=H("backend/app/db/production_hardening_preflight.py"),
            decision="REUSE",
            rationale="read-only preflight (locks/capacity/duplicates/pre-post conditions)",
            linked_tests=("backend/tests/test_production_hardening_migration_preflight.py",),
            requirement_refs=("10.15", "10.16", "10.17", "10.18", "10.19"),
        ),
        InventoryEntry(
            path="backend/scripts/preflight_production_hardening_migrations.py",
            workspace_status="??",
            content_sha256=H("backend/scripts/preflight_production_hardening_migrations.py"),
            decision="REUSE",
            rationale="preflight runner; reruns before every future apply per apply_gate",
            linked_tests=("backend/tests/test_production_hardening_migration_preflight.py",),
            requirement_refs=("10.15", "13.2"),
        ),
        InventoryEntry(
            path="backend/app/engine/canonical.py",
            workspace_status="??",
            content_sha256=H("backend/app/engine/canonical.py"),
            decision="REUSE",
            rationale="single canonical JSON/fingerprint primitive shared by contracts and governance plane (no second hashing infrastructure)",
            linked_tests=("backend/tests/test_execution_policy.py",),
            requirement_refs=("3.1", "3.5"),
        ),
        InventoryEntry(
            path="backend/app/engine/execution_policy.py",
            workspace_status="??",
            content_sha256=H("backend/app/engine/execution_policy.py"),
            decision="REUSE",
            rationale="immutable nested ExecutionPolicy + registry + artifact loader with lossless round-trip verification",
            linked_tests=("backend/tests/test_execution_policy.py",),
            requirement_refs=("3.1", "3.5"),
        ),
        InventoryEntry(
            path="backend/app/policies/execution_policy.v1.json",
            workspace_status="??",
            content_sha256=H("backend/app/policies/execution_policy.v1.json"),
            decision="REUSE",
            rationale="versioned policy artifact with declared fingerprint",
            linked_tests=("backend/tests/test_execution_policy.py",),
            requirement_refs=("3.1", "3.5"),
        ),
        InventoryEntry(
            path="backend/app/policies/execution_policy.registry.json",
            workspace_status="??",
            content_sha256=H("backend/app/policies/execution_policy.registry.json"),
            decision="REUSE",
            rationale="policy registry manifest (identity/fingerprint index)",
            linked_tests=("backend/tests/test_execution_policy.py",),
            requirement_refs=("3.1", "3.5"),
        ),
        InventoryEntry(
            path="backend/app/engine/backtest/contracts.py",
            workspace_status=" M",
            content_sha256=H("backend/app/engine/backtest/contracts.py"),
            decision="RECONCILE",
            rationale="canonical primitives deduplicated onto app.engine.canonical; replay parse gap g3 reconciled in research_runner",
            linked_tests=("backend/tests/test_research_gateway_vertical_slice.py",),
            requirement_refs=("3.2", "3.4"),
            gap_record_id="g3-research-runner-nested-policy-parse",
        ),
        InventoryEntry(
            path="backend/app/services/backtest/research_runner.py",
            workspace_status=" M",
            content_sha256=H("backend/app/services/backtest/research_runner.py"),
            decision="RECONCILE",
            rationale="nested canonical execution_policy payload now parsed via lossless-verified from_payload; legacy flat path retained",
            linked_tests=("backend/tests/test_research_gateway_vertical_slice.py",),
            requirement_refs=("3.2", "3.4"),
            gap_record_id="g3-research-runner-nested-policy-parse",
        ),
        InventoryEntry(
            path="backend/tests/test_model_router_service.py",
            workspace_status=" M",
            content_sha256=H("backend/tests/test_model_router_service.py"),
            decision="RECONCILE",
            rationale="stale client fake aligned to committed default_headers signature (pre-existing HEAD failure)",
            linked_tests=("backend/tests/test_model_router_service.py",),
            requirement_refs=(),
            gap_record_id="g2-model-router-stale-client-fake",
        ),
        InventoryEntry(
            path="backend/scripts/replay_all_days.py",
            workspace_status=" M",
            content_sha256=H("backend/scripts/replay_all_days.py"),
            decision="REUSE",
            rationale="explicit HISTORICAL_RECONSTRUCTION writer with batch identity and provenance finalization (Task 1.3 code completion evidence)",
            linked_tests=("backend/tests/test_historical_reconstruction_replay.py",),
            requirement_refs=("2.2", "2.3", "2.4", "2.5"),
        ),
        InventoryEntry(
            path="backend/testkit/golden_harness.py",
            workspace_status="??",
            content_sha256=H("backend/testkit/golden_harness.py"),
            decision="REUSE",
            rationale="deterministic offline golden harness; fixed fixtures, no network/mutable DB",
            linked_tests=("backend/tests/test_production_hardening_golden_harness.py",),
            requirement_refs=("1.10", "3.8", "7.8"),
        ),
        InventoryEntry(
            path="backend/testkit/mysql_sandbox.py",
            workspace_status="??",
            content_sha256=H("backend/testkit/mysql_sandbox.py"),
            decision="REUSE",
            rationale="isolated MySQL sandbox helper for future Layer 2 rehearsals; not used as Layer 2 evidence itself",
            linked_tests=(),
            requirement_refs=("13.3",),
        ),
        InventoryEntry(
            path="backend/scripts/capture_production_evidence_baseline.py",
            workspace_status="??",
            content_sha256=H("backend/scripts/capture_production_evidence_baseline.py"),
            decision="REUSE",
            rationale="baseline capture script (read-only, sanitized, fail-closed)",
            linked_tests=("backend/tests/test_capture_production_evidence_baseline.py",),
            requirement_refs=("1.2", "1.3"),
        ),
        InventoryEntry(
            path="backend/scripts/capture_prefect_scheduler_baseline.py",
            workspace_status="??",
            content_sha256=H("backend/scripts/capture_prefect_scheduler_baseline.py"),
            decision="REUSE",
            rationale="Prefect control-plane capture script (read-only, SQLite mode=ro, process allowlist)",
            linked_tests=("backend/tests/test_capture_prefect_scheduler_baseline.py",),
            requirement_refs=("8.1", "8.2"),
        ),
        InventoryEntry(
            path="backend/tests/data/production_hardening_golden_v1.json",
            workspace_status="??",
            content_sha256=H("backend/tests/data/production_hardening_golden_v1.json"),
            decision="REUSE",
            rationale="fixed golden fixture set for harness determinism",
            linked_tests=("backend/tests/test_production_hardening_golden_harness.py",),
            requirement_refs=("1.10",),
        ),
        InventoryEntry(
            path="backend/app/services/official_sample_scope.py",
            workspace_status=" M",
            content_sha256=H("backend/app/services/official_sample_scope.py"),
            decision="REUSE",
            rationale="existing official sample scope service; LIVE selector alignment audited in S1",
            linked_tests=("backend/tests/test_official_sample_scope.py",),
            requirement_refs=("2.5", "2.6", "4.3"),
        ),
        InventoryEntry(
            path="backend/app/services/outcome_simulation.py",
            workspace_status=" M",
            content_sha256=H("backend/app/services/outcome_simulation.py"),
            decision="REUSE",
            rationale="existing outcome simulation; canonical core收口 audited under S2 gap records",
            linked_tests=("backend/tests/test_outcome_simulation.py",),
            requirement_refs=("3.2", "3.3", "3.4"),
        ),
        InventoryEntry(
            path=".kiro/specs/production-evidence-risk-hardening/traceability.md",
            workspace_status="??",
            content_sha256=H(".kiro/specs/production-evidence-risk-hardening/traceability.md"),
            decision="RECONCILE",
            rationale="written against superseded task numbering; task 0.5 rewrites it to Requirements 1-13 / S0-S9 mapping",
            linked_tests=(),
            requirement_refs=("1.9",),
        ),
    ]
    return tuple(rows)


def _scope_entries() -> tuple[ReleaseScopeEntry, ...]:
    env_key = "real_env.local_mysql_prefect.v1"
    produces = EvidenceBindingDirection.PRODUCES
    consumes = EvidenceBindingDirection.CONSUMES

    def binding(direction, key):
        return EvidenceBinding(direction=direction, evidence_set_key=key)

    return (
        ReleaseScopeEntry(
            operation_id="migration_apply.task_1_3_schema_v1",
            inclusion=ReleaseInclusion.INCLUDED,
            completion_impact=CompletionImpact.COMPLETION_CRITICAL,
            target_environment_key=env_key,
            operation_plan_key="migration_plan.production_hardening.v1",
            evidence_bindings=(binding(produces, "L2_MIGRATION_APPLY_NO_OP_V1"),),
            dependency_operation_ids=(),
            declaration_reason_code="FORCED_BY_SPEC_INCLUDED_COMPLETION_CRITICAL",
        ),
        ReleaseScopeEntry(
            operation_id="classification_backfill.provenance_v1",
            inclusion=ReleaseInclusion.INCLUDED,
            completion_impact=CompletionImpact.COMPLETION_CRITICAL,
            target_environment_key=env_key,
            operation_plan_key="classification_plan.provenance_v1@sha256:1e2255b4daf2ed18559f7911ff2aabeb849471ed6ea0b46153c723cc111e38e8",
            evidence_bindings=(binding(produces, "L2_CLASSIFICATION_BACKFILL_V1"),),
            dependency_operation_ids=("migration_apply.task_1_3_schema_v1",),
            declaration_reason_code="USER_DECISION_FULL_SCOPE_2026-08-04",
        ),
        ReleaseScopeEntry(
            operation_id="projection_cutover.production_read_v1",
            inclusion=ReleaseInclusion.INCLUDED,
            completion_impact=CompletionImpact.COMPLETION_CRITICAL,
            target_environment_key=env_key,
            operation_plan_key="projection_cutover_plan.production_read.v1",
            evidence_bindings=(
                binding(consumes, "L2_FLAG_EVIDENCE_PROJECTION_V1"),
                binding(consumes, "L2_FLAG_CANONICAL_EXECUTION_V1"),
                binding(consumes, "L2_FLAG_DASHBOARD_OBSERVABILITY_V1"),
                binding(produces, "L2_PROJECTION_CUTOVER_V1"),
            ),
            dependency_operation_ids=("migration_apply.task_1_3_schema_v1",),
            declaration_reason_code="USER_DECISION_FULL_SCOPE_2026-08-04",
        ),
        ReleaseScopeEntry(
            operation_id="enforcement.evidence_projection",
            inclusion=ReleaseInclusion.INCLUDED,
            completion_impact=CompletionImpact.ACTIVATION_ONLY,
            target_environment_key=env_key,
            operation_plan_key="enforcement_plan.evidence_projection.v1",
            evidence_bindings=(binding(consumes, "L2_FLAG_EVIDENCE_PROJECTION_V1"),),
            dependency_operation_ids=("projection_cutover.production_read_v1",),
            declaration_reason_code="USER_DECISION_FULL_SCOPE_2026-08-04",
        ),
        ReleaseScopeEntry(
            operation_id="enforcement.canonical_execution",
            inclusion=ReleaseInclusion.INCLUDED,
            completion_impact=CompletionImpact.ACTIVATION_ONLY,
            target_environment_key=env_key,
            operation_plan_key="enforcement_plan.canonical_execution.v1",
            evidence_bindings=(binding(consumes, "L2_FLAG_CANONICAL_EXECUTION_V1"),),
            dependency_operation_ids=(),
            declaration_reason_code="USER_DECISION_FULL_SCOPE_2026-08-04",
        ),
        ReleaseScopeEntry(
            operation_id="enforcement.release_gate",
            inclusion=ReleaseInclusion.INCLUDED,
            completion_impact=CompletionImpact.ACTIVATION_ONLY,
            target_environment_key=env_key,
            operation_plan_key="enforcement_plan.release_gate.v1",
            evidence_bindings=(binding(consumes, "L2_FLAG_RELEASE_GOVERNANCE_V1"),),
            dependency_operation_ids=(),
            declaration_reason_code="USER_DECISION_FULL_SCOPE_2026-08-04",
        ),
        ReleaseScopeEntry(
            operation_id="enforcement.portfolio_risk",
            inclusion=ReleaseInclusion.INCLUDED,
            completion_impact=CompletionImpact.ACTIVATION_ONLY,
            target_environment_key=env_key,
            operation_plan_key="enforcement_plan.portfolio_risk.v1",
            evidence_bindings=(binding(consumes, "L2_FLAG_PORTFOLIO_RISK_V1"),),
            dependency_operation_ids=(),
            declaration_reason_code="USER_DECISION_FULL_SCOPE_2026-08-04",
        ),
        ReleaseScopeEntry(
            operation_id="enforcement.scheduler_claim",
            inclusion=ReleaseInclusion.INCLUDED,
            completion_impact=CompletionImpact.ACTIVATION_ONLY,
            target_environment_key=env_key,
            operation_plan_key="enforcement_plan.scheduler_claim.v1",
            evidence_bindings=(binding(consumes, "L2_FLAG_SCHEDULER_CLAIM_V1"),),
            dependency_operation_ids=(),
            declaration_reason_code="USER_DECISION_FULL_SCOPE_2026-08-04",
        ),
        ReleaseScopeEntry(
            operation_id="enforcement.dashboard_refresh",
            inclusion=ReleaseInclusion.INCLUDED,
            completion_impact=CompletionImpact.ACTIVATION_ONLY,
            target_environment_key=env_key,
            operation_plan_key="enforcement_plan.dashboard_refresh.v1",
            evidence_bindings=(binding(consumes, "L2_FLAG_DASHBOARD_OBSERVABILITY_V1"),),
            dependency_operation_ids=("projection_cutover.production_read_v1",),
            declaration_reason_code="USER_DECISION_FULL_SCOPE_2026-08-04",
        ),
    )


def main() -> int:
    store = GovernanceObjectStore()
    events = GovernanceEventLog(store)
    existing = events.first_event_of_type("RELEASE_SCOPE_FROZEN")
    if existing is not None:
        print(json.dumps({
            "status": "already_frozen",
            "release_scope_manifest_id": existing.payload["payload"]["release_scope_manifest_id"],
            "note": "scope changes require a new superseding revision; frozen objects are never refreshed",
        }, indent=2, ensure_ascii=False))
        return 0

    head = _git("rev-parse", "HEAD")
    branch = _git("branch", "--show-current")
    now = datetime.now(timezone.utc)
    status_digest = _status_digest()

    baselines = []
    for filename, task_prefix in (
        ("baseline-task-0.1.json", "0.1"),
        ("baseline-task-7.1.json", "7.1"),
    ):
        result, document = audit_baseline_artifact(
            SPEC_DIR / filename,
            expected_task_prefix=task_prefix,
            revision_exists=_revision_exists,
            commits_between=_commits_between,
            current_revision=head,
            current_branch=branch,
            current_status_digest=status_digest,
            first_new_behavior_change_at=None,
            delta_note=_delta_note(),
        )
        baselines.append((result, document))
        print(f"[audit] {filename}: reusable={result.reusable_as_canonical_baseline} "
              f"digests={result.section_digests_match} revision={result.revision_exists_in_history} "
              f"order={result.freeze_order_provable}")

    bootstrap_note = {
        "as_found_state": (
            "Prior session artifacts were uncommitted on branch fix/show-replay-in-records; "
            "baseline captures (2026-08-03T12:17Z/13:29Z) predate every write of this freeze session."
        ),
        "pre_freeze_repairs": (
            "Three regression repairs executed during governance bootstrap are registered as gap "
            "records g1-g3 with before-evidence; no other behavior change preceded this freeze."
        ),
        "frozen_at": now.isoformat(),
        "head": head,
        "branch": branch,
        "worktree_status_digest": status_digest,
    }

    receipt = freeze_s0(
        store=store,
        events=events,
        now=now,
        actor=ACTOR,
        code_revision=head,
        baseline_audits=tuple(baselines),
        bootstrap_note=bootstrap_note,
        inventory_entries=_inventory_entries(),
        gap_records=_gap_records(),
        scope_entries=_scope_entries(),
    )
    print(json.dumps({"status": "frozen", **receipt.canonical_payload()}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
