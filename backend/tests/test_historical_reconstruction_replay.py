"""Task 1.3 authorized historical reconstruction regression tests."""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.models.tables import Recommendation, RecommendationOutcome, RecommendationRun
from app.services.evidence import EvidenceClass, PROVENANCE_SCHEMA_VERSION
from app.services.official_sample_scope import official_sample_conditions
from app.services.workbench_service import (
    invalidate_recommendation_history_cache,
    list_recommendation_history,
)
from scripts.replay_all_days import (
    _finalize_reconstruction_run,
    _historical_payload,
    _initialize_reconstruction_run,
    _run_exists,
)


class _FakeSession:
    def __init__(self, run: SimpleNamespace) -> None:
        self.run = run
        self.commit_count = 0

    def get(self, _model, run_id: str):
        return self.run if self.run.run_id == run_id else None

    def commit(self) -> None:
        self.commit_count += 1


def _queued_run() -> SimpleNamespace:
    return SimpleNamespace(
        run_id="run_reconstruction_unit",
        run_name="REPLAY-历史推荐重建",
        run_mode="formal",
        evaluation_scope="historical_replay",
        evidence_class=None,
        actual_generated_at=None,
        evidence_identity_hash=None,
        strategy_policy_fingerprint=None,
        execution_policy_fingerprint=None,
        code_revision=None,
        reconstruction_batch_id=None,
        provenance_schema_version=None,
        classification_reason=None,
        trade_date="2026-07-01",
        started_at=datetime(2026, 8, 4, 2, 3, 4, 567890),
        status="queued",
        data_as_of=None,
        source_snapshot_id=None,
        result_summary_json="{}",
    )


def _complete_run(run: SimpleNamespace, *, data_as_of: str = "2026-07-01") -> None:
    run.status = "completed"
    run.data_as_of = data_as_of
    run.source_snapshot_id = "snapshot_pool_2026-07-01_test"
    run.result_summary_json = json.dumps(
        {
            "execution": {
                "strategy_fingerprints": [
                    {
                        "strategy_id": "chen_xiaoqun",
                        "strategy_version": "v1",
                        "fingerprint": "a" * 64,
                    }
                ],
                "execution_policy": {
                    "policy_id": "production-recommendation-execution",
                    "policy_version": "v1",
                    "fingerprint": "b" * 64,
                },
            }
        }
    )


@pytest.mark.parametrize(
    "unsafe_source",
    ["realtime", "local_pool_realtime", "local_pool_preferred", "auto"],
)
def test_replay_payload_always_uses_signal_date_as_of_boundary(unsafe_source: str):
    """**Validates: Requirements 1.4, 1.9**"""
    base = {
        "run_name": "old name",
        "run_mode": "formal",
        "evaluation_scope": "live_forward",
        "data_source": unsafe_source,
        "skip_aux_refresh": False,
        "matrix_run": True,
    }

    payload = _historical_payload(base, "2026-07-01")

    assert payload["trade_date_override"] == "2026-07-01"
    assert payload["data_source"] == "local_pool"
    assert payload["skip_aux_refresh"] is True
    assert payload["matrix_run"] is False
    assert "evaluation_scope" not in payload
    assert base["data_source"] == unsafe_source


def test_replay_persists_explicit_identity_then_finalizes_canonical_provenance():
    """**Validates: Requirements 1.1, 1.3, 1.4**"""
    run = _queued_run()
    db = _FakeSession(run)
    batch_id = "replay-20260701-20260731-20260804T020304Z-deadbeef"

    _initialize_reconstruction_run(db, run.run_id, batch_id)

    assert run.evidence_class == EvidenceClass.HISTORICAL_RECONSTRUCTION.value
    assert run.actual_generated_at == run.started_at
    assert run.reconstruction_batch_id == batch_id
    assert run.provenance_schema_version == PROVENANCE_SCHEMA_VERSION
    assert run.evaluation_scope == "historical_replay"
    assert db.commit_count == 1

    _complete_run(run)
    identity = _finalize_reconstruction_run(db, run.run_id, code_revision="c" * 40)

    assert identity.evidence_class is EvidenceClass.HISTORICAL_RECONSTRUCTION
    assert identity.signal_date == "2026-07-01"
    assert identity.data_as_of == "2026-07-01"
    assert identity.reconstruction_batch_id == batch_id
    assert run.evidence_identity_hash == identity.identity_hash
    assert run.strategy_policy_fingerprint == identity.strategy_policy_fingerprint
    assert run.execution_policy_fingerprint == "b" * 64
    assert run.code_revision == "c" * 40
    assert run.evaluation_scope == "historical_replay"
    assert db.commit_count == 2


def test_replay_rejects_signal_snapshot_newer_than_signal_date():
    """**Validates: Requirements 1.9**"""
    run = _queued_run()
    db = _FakeSession(run)
    _initialize_reconstruction_run(db, run.run_id, "replay-asof-boundary")
    _complete_run(run, data_as_of="2026-07-02")

    with pytest.raises(RuntimeError, match="signal reader 越界"):
        _finalize_reconstruction_run(db, run.run_id, code_revision="c" * 40)

    assert run.evidence_identity_hash is None
    assert db.commit_count == 1


def test_replay_skip_existing_uses_run_provenance_and_accepts_zero_recommendations(db):
    trade_date = "2099-02-04"
    live_run = RecommendationRun(
        run_id="run_replay_exists_live_only",
        run_name="LIVE fixture",
        run_mode="formal",
        evaluation_scope="live_forward",
        evidence_class=EvidenceClass.LIVE_FORWARD.value,
        actual_generated_at=datetime(2099, 2, 4, 2, 0),
        evidence_identity_hash="d" * 64,
        strategy_policy_fingerprint="a" * 64,
        execution_policy_fingerprint="b" * 64,
        code_revision="c" * 40,
        provenance_schema_version=PROVENANCE_SCHEMA_VERSION,
        trade_date=trade_date,
        data_as_of=trade_date,
        source_snapshot_id="snapshot-live-only",
        started_at=datetime(2099, 2, 4, 2, 0),
        status="completed",
    )
    db.add(live_run)
    db.flush()

    assert _run_exists(db, trade_date) is False

    replay_run = RecommendationRun(
        run_id="run_replay_exists_zero_recommendations",
        run_name="REPLAY zero recommendation fixture",
        run_mode="formal",
        evaluation_scope="historical_replay",
        evidence_class=EvidenceClass.HISTORICAL_RECONSTRUCTION.value,
        actual_generated_at=datetime(2099, 2, 8, 2, 0),
        evidence_identity_hash="e" * 64,
        strategy_policy_fingerprint="f" * 64,
        execution_policy_fingerprint="1" * 64,
        code_revision="2" * 40,
        reconstruction_batch_id="replay-zero-recommendations",
        provenance_schema_version=PROVENANCE_SCHEMA_VERSION,
        trade_date=trade_date,
        data_as_of=trade_date,
        source_snapshot_id="snapshot-replay-zero",
        started_at=datetime(2099, 2, 8, 2, 0),
        status="completed",
        recommended_count=0,
    )
    db.add(replay_run)
    db.flush()

    assert _run_exists(db, trade_date) is True


def test_historical_page_queries_reconstruction_by_trade_date_without_rewriting_time(db):
    """**Validates: Requirements 1.1, 1.3, 1.4, 1.6**"""
    run_id = "run_task_1_3_historical_query"
    recommendation_id = "rec_task_1_3_historical_query"
    trade_date = "2099-02-03"
    actual_generated_at = datetime(2099, 2, 8, 2, 3, 4, 567890)
    run = RecommendationRun(
        run_id=run_id,
        run_name="REPLAY-历史推荐重建",
        run_mode="formal",
        evaluation_scope="historical_replay",
        evidence_class=EvidenceClass.HISTORICAL_RECONSTRUCTION.value,
        actual_generated_at=actual_generated_at,
        evidence_identity_hash="d" * 64,
        strategy_policy_fingerprint="a" * 64,
        execution_policy_fingerprint="b" * 64,
        code_revision="c" * 40,
        reconstruction_batch_id="replay-task-1-3-query",
        provenance_schema_version=PROVENANCE_SCHEMA_VERSION,
        classification_reason="trusted_historical_replay_entrypoint",
        trade_date=trade_date,
        started_at=actual_generated_at,
        finished_at=actual_generated_at,
        data_as_of=trade_date,
        source_snapshot_id="snapshot_pool_2099-02-03_task_1_3",
        filter_audit_json=json.dumps({"candidate_audit": {"retained": True}}),
        status="completed",
    )
    recommendation = Recommendation(
        recommendation_id=recommendation_id,
        run_id=run_id,
        trade_date=trade_date,
        # Real wall-clock generation remains five days after signal date.
        recommended_at=actual_generated_at,
        code="688889",
        name="历史重建查询样本",
        recommend_price=10.0,
        final_score=88.0,
        setup_type="replay_setup_fixture",
    )
    outcome = RecommendationOutcome(
        recommendation_id=recommendation_id,
        entry_triggered=True,
        executed_return=1.5,
        outcome_label="success",
    )
    db.add_all([run, recommendation, outcome])
    db.flush()
    invalidate_recommendation_history_cache()

    result = list_recommendation_history(
        db,
        code="688889",
        date_from=trade_date,
        date_to=trade_date,
        limit=50,
    )

    assert result["total"] == 1
    assert result["items"][0]["trade_date"] == trade_date
    assert result["items"][0]["recommended_at"] == actual_generated_at.isoformat()
    assert result["items"][0]["outcome"]["outcome_label"] == "success"
    assert "replay_setup_fixture" in result["facets"]["setup_types"]
    assert json.loads(run.filter_audit_json)["candidate_audit"]["retained"] is True
    assert run.evaluation_scope == "historical_replay"

    live_count = db.scalar(
        select(func.count())
        .select_from(Recommendation)
        .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
        .where(
            Recommendation.recommendation_id == recommendation_id,
            *official_sample_conditions(),
        )
    )
    assert live_count == 0
