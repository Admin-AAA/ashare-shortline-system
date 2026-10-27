"""S1-b formal run Complete_Provenance writer regression tests (task 1.1/1.2).

**Validates: Requirements 2.1–2.5, 2.9**
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.evidence import (
    FORMAL_PROVENANCE_REASON,
    INCOMPLETE_PROVENANCE_REASON,
    PROVENANCE_SCHEMA_VERSION,
    EvidenceClass,
    apply_formal_run_provenance,
    resolve_code_revision,
)

_CODE_REVISION = "c" * 40
_STRATEGY_FINGERPRINT = "a" * 64
_EXECUTION_FINGERPRINT = "b" * 64


def _completed_formal_run(
    *,
    run_name: str = "每日开盘正式推荐",
    evaluation_scope: str = "live_forward",
    trade_date: str = "2026-08-04",
    started_at: datetime | None = datetime(2026, 8, 4, 1, 30, 0, 123456),
    data_as_of: str | None = "2026-08-04",
    source_snapshot_id: str | None = "snapshot_pool_2026-08-04_formal",
    with_execution_audit: bool = True,
) -> SimpleNamespace:
    summary: dict = {"agent_runtime": "auditable_skill_agents"}
    if with_execution_audit:
        summary["execution"] = {
            "strategy_fingerprints": [
                {
                    "strategy_id": "chen_xiaoqun",
                    "strategy_version": "v1",
                    "fingerprint": _STRATEGY_FINGERPRINT,
                }
            ],
            "execution_policy": {
                "policy_id": "recommendation-run",
                "policy_version": "v1",
                "fingerprint": _EXECUTION_FINGERPRINT,
            },
        }
    return SimpleNamespace(
        run_id="run_formal_provenance_unit",
        run_name=run_name,
        run_mode="formal",
        evaluation_scope=evaluation_scope,
        evidence_class=None,
        actual_generated_at=None,
        evidence_identity_hash=None,
        strategy_policy_fingerprint=None,
        execution_policy_fingerprint=None,
        code_revision=None,
        reconstruction_batch_id=None,
        provenance_schema_version=None,
        classification_reason=None,
        trade_date=trade_date,
        started_at=started_at,
        status="completed",
        data_as_of=data_as_of,
        source_snapshot_id=source_snapshot_id,
        result_summary_json=json.dumps(summary),
    )


def _provenance_snapshot(run: SimpleNamespace) -> dict:
    return {
        field: getattr(run, field)
        for field in (
            "evidence_class",
            "actual_generated_at",
            "data_as_of",
            "source_snapshot_id",
            "evidence_identity_hash",
            "strategy_policy_fingerprint",
            "execution_policy_fingerprint",
            "code_revision",
            "provenance_schema_version",
            "classification_reason",
        )
    }


def test_same_day_formal_run_classifies_live_forward_and_persists_all_columns():
    """**Validates: Requirements 2.1, 2.5**"""
    run = _completed_formal_run()

    identity = apply_formal_run_provenance(run, code_revision=_CODE_REVISION)

    assert identity is not None
    assert identity.evidence_class is EvidenceClass.LIVE_FORWARD
    assert identity.is_true_forward
    # 01:30 UTC == 09:30 Asia/Shanghai on the signal business date.
    assert identity.actual_generated_at == datetime(
        2026, 8, 4, 1, 30, 0, 123456, tzinfo=timezone.utc
    )
    assert run.evidence_class == EvidenceClass.LIVE_FORWARD.value
    assert run.actual_generated_at == datetime(2026, 8, 4, 1, 30, 0, 123456)
    assert run.data_as_of == "2026-08-04"
    assert run.source_snapshot_id == "snapshot_pool_2026-08-04_formal"
    assert run.evidence_identity_hash == identity.identity_hash
    assert run.strategy_policy_fingerprint == identity.strategy_policy_fingerprint
    assert run.execution_policy_fingerprint == _EXECUTION_FINGERPRINT
    assert run.code_revision == _CODE_REVISION
    assert run.provenance_schema_version == PROVENANCE_SCHEMA_VERSION
    assert run.classification_reason == FORMAL_PROVENANCE_REASON
    # Replay-only identity component is never written by the formal adapter.
    assert run.reconstruction_batch_id is None


def test_next_morning_before_open_is_live_after_open_is_unverified():
    """2026-10-27：信号日收盘后到入场日 09:15 前生成（凌晨/安全网补跑）算实盘；开盘后才生成不算。"""
    # 2026-08-05 01:00 UTC == 09:00 Beijing，入场日开盘前
    early = _completed_formal_run(started_at=datetime(2026, 8, 5, 1, 0))
    assert apply_formal_run_provenance(early, code_revision=_CODE_REVISION).evidence_class is EvidenceClass.LIVE_FORWARD


def test_next_day_generation_classifies_unverified_with_full_identity():
    """**Validates: Requirements 2.2, 2.5**"""
    # 2026-08-05 01:30 UTC == 2026-08-05 09:30 Beijing：入场日已开盘
    run = _completed_formal_run(started_at=datetime(2026, 8, 5, 1, 30))

    identity = apply_formal_run_provenance(run, code_revision=_CODE_REVISION)

    assert identity is not None
    assert identity.evidence_class is EvidenceClass.UNVERIFIED
    assert not identity.is_true_forward
    assert run.evidence_class == EvidenceClass.UNVERIFIED.value
    # Full provenance is still persisted; only the classification degrades.
    assert run.evidence_identity_hash == identity.identity_hash
    assert run.strategy_policy_fingerprint == identity.strategy_policy_fingerprint
    assert run.code_revision == _CODE_REVISION


def test_unknown_snapshot_id_classifies_unverified():
    """**Validates: Requirements 2.5, 2.9**"""
    run = _completed_formal_run(source_snapshot_id="unknown")

    identity = apply_formal_run_provenance(run, code_revision=_CODE_REVISION)

    assert identity is not None
    assert identity.evidence_class is EvidenceClass.UNVERIFIED
    assert run.evidence_class == EvidenceClass.UNVERIFIED.value
    assert run.evidence_identity_hash == identity.identity_hash


def test_missing_snapshot_id_fails_closed_to_unknown_not_live():
    """**Validates: Requirement 2.9**"""
    run = _completed_formal_run(source_snapshot_id=None)

    identity = apply_formal_run_provenance(run, code_revision=_CODE_REVISION)

    assert identity is not None
    assert identity.evidence_class is EvidenceClass.UNVERIFIED
    assert run.source_snapshot_id == "unknown"
    assert run.evidence_class == EvidenceClass.UNVERIFIED.value


def test_missing_execution_audit_fails_closed_without_fabricated_hash():
    """**Validates: Requirement 2.9**"""
    run = _completed_formal_run(with_execution_audit=False)

    identity = apply_formal_run_provenance(run, code_revision=_CODE_REVISION)

    assert identity is None
    assert run.evidence_class == EvidenceClass.UNVERIFIED.value
    assert run.evidence_identity_hash is None
    assert run.strategy_policy_fingerprint is None
    assert run.execution_policy_fingerprint is None
    assert run.classification_reason == INCOMPLETE_PROVENANCE_REASON
    # Idempotent: a second call must not rewrite the fail-closed marker.
    run.code_revision = "tampered"
    assert apply_formal_run_provenance(run, code_revision=_CODE_REVISION) is None
    assert run.code_revision == "tampered"


def test_idempotent_rewrite_never_changes_identity_hash():
    """**Validates: Requirements 2.4, 2.9**"""
    run = _completed_formal_run()
    first = apply_formal_run_provenance(run, code_revision=_CODE_REVISION)
    assert first is not None
    persisted = _provenance_snapshot(run)

    # A matching hash short-circuits before any column is touched: tampering
    # with a non-hash column must survive the second call unchanged.
    run.classification_reason = "tampered-by-other-writer"
    second = apply_formal_run_provenance(run, code_revision=_CODE_REVISION)

    assert second is not None
    assert second.identity_hash == first.identity_hash
    assert run.evidence_identity_hash == first.identity_hash
    assert run.classification_reason == "tampered-by-other-writer"
    persisted["classification_reason"] = "tampered-by-other-writer"
    assert _provenance_snapshot(run) == persisted


def test_historical_replay_runs_never_pass_through_formal_adapter():
    """**Validates: Requirements 2.2, 2.3**"""
    replay_run = _completed_formal_run(
        run_name="REPLAY-历史推荐重建",
        evaluation_scope="historical_replay",
        trade_date="2026-07-01",
        started_at=datetime(2026, 8, 4, 2, 3, 4),
        data_as_of="2026-07-01",
    )
    before = _provenance_snapshot(replay_run)

    assert apply_formal_run_provenance(replay_run, code_revision=_CODE_REVISION) is None
    assert _provenance_snapshot(replay_run) == before

    # A row already owned by the replay adapter is also left untouched, even
    # if its scope column were ever misread as live.
    owned = _completed_formal_run()
    owned.evidence_class = EvidenceClass.HISTORICAL_RECONSTRUCTION.value
    owned.reconstruction_batch_id = "replay-owned-batch"
    assert apply_formal_run_provenance(owned, code_revision=_CODE_REVISION) is None
    assert owned.evidence_class == EvidenceClass.HISTORICAL_RECONSTRUCTION.value
    assert owned.evidence_identity_hash is None


@pytest.mark.parametrize("run_mode", ["experiment", "backtest"])
def test_non_formal_run_modes_are_not_owned_by_this_adapter(run_mode: str):
    """**Validates: Requirement 2.5**"""
    run = _completed_formal_run()
    run.run_mode = run_mode
    before = _provenance_snapshot(run)

    assert apply_formal_run_provenance(run, code_revision=_CODE_REVISION) is None
    assert _provenance_snapshot(run) == before


def test_unfinished_run_is_not_classified():
    """**Validates: Requirement 2.1**"""
    run = _completed_formal_run()
    run.status = "running"
    before = _provenance_snapshot(run)

    assert apply_formal_run_provenance(run, code_revision=_CODE_REVISION) is None
    assert _provenance_snapshot(run) == before


@pytest.mark.parametrize(
    "run_name",
    ["REPLAY-历史推荐重建", "LIVE 官方样本", "backtest 冠军", ""],
)
def test_run_name_and_scope_metadata_never_influence_classification(run_name: str):
    """**Validates: Requirement 2.5**"""
    live_run = _completed_formal_run(run_name=run_name)
    identity = apply_formal_run_provenance(live_run, code_revision=_CODE_REVISION)
    assert identity is not None
    assert identity.evidence_class is EvidenceClass.LIVE_FORWARD

    # A display name claiming "live" cannot rescue generation after the entry session opened.
    late_run = _completed_formal_run(
        run_name=run_name,
        started_at=datetime(2026, 8, 5, 1, 30),
    )
    late_identity = apply_formal_run_provenance(late_run, code_revision=_CODE_REVISION)
    assert late_identity is not None
    assert late_identity.evidence_class is EvidenceClass.UNVERIFIED


def test_resolve_code_revision_prefers_valid_env():
    # An explicit valid env value wins and never needs git.
    assert resolve_code_revision({"ASHARE_APPLICATION_REVISION": "AbC1234"}) == "abc1234"


def test_resolve_code_revision_explicit_environ_is_pure_and_fails_closed(monkeypatch):
    import app.core.deployment_identity as deployment_identity

    # An injected environ mapping stays pure: it must never invoke git.
    monkeypatch.setattr(
        deployment_identity,
        "_cached_git_head_revision",
        lambda: (_ for _ in ()).throw(AssertionError("explicit environ must not touch git")),
    )
    assert resolve_code_revision({}) == "unknown"
    assert resolve_code_revision({"ASHARE_APPLICATION_REVISION": "not a revision!"}) == "unknown"


def test_resolve_code_revision_default_path_uses_cached_git_head(monkeypatch):
    import app.core.deployment_identity as deployment_identity

    monkeypatch.delenv("ASHARE_APPLICATION_REVISION", raising=False)
    monkeypatch.setattr(deployment_identity, "_cached_git_head_revision", lambda: "deadbeefcafe1234")
    assert resolve_code_revision() == "deadbeefcafe1234"
