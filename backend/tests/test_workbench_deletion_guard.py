from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api import recommendations as recommendations_api
from app.api import runs as runs_api
from app.models.tables import (
    AgentDecisionLog,
    ApiCallLog,
    IntradayLockMonitor,
    IntradayLockSignal,
    Recommendation,
    RecommendationCollectionSeal,
    RecommendationEvidenceClassificationAudit,
    RecommendationExecutionState,
    RecommendationOutcome,
    RecommendationOutcomeVersion,
    RecommendationRun,
    ReviewNote,
    WatchItem,
)
from app.services.runs.deletion_guard import DownstreamFactDeletionBlocked
from app.services.workbench_service import delete_recommendation, delete_run


_TABLES = (
    RecommendationRun.__table__,
    Recommendation.__table__,
    RecommendationCollectionSeal.__table__,
    RecommendationOutcome.__table__,
    RecommendationOutcomeVersion.__table__,
    RecommendationExecutionState.__table__,
    RecommendationEvidenceClassificationAudit.__table__,
    ReviewNote.__table__,
    WatchItem.__table__,
    AgentDecisionLog.__table__,
    ApiCallLog.__table__,
    IntradayLockMonitor.__table__,
    IntradayLockSignal.__table__,
)


def _database() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for table in _TABLES:
        table.create(engine)
    return Session(engine)


def _run(run_id: str = "run_deletion_guard", **overrides: object) -> RecommendationRun:
    values: dict[str, object] = {
        "run_id": run_id,
        "run_name": "deletion guard fixture",
        "run_mode": "formal",
        "evaluation_scope": "live_forward",
        "trade_date": "2026-08-01",
        "status": "completed",
        "selected_skills_json": "[]",
        "params_json": "{}",
        "market_snapshot_json": "{}",
        "result_summary_json": "{}",
    }
    values.update(overrides)
    return RecommendationRun(**values)


def _recommendation(
    run_id: str,
    recommendation_id: str = "rec_deletion_guard",
) -> Recommendation:
    return Recommendation(
        recommendation_id=recommendation_id,
        run_id=run_id,
        trade_date="2026-08-01",
        recommended_at=datetime(2026, 8, 1, 9, 30),
        code="000001",
        name="deletion guard fixture",
        recommend_price=10.0,
        final_score=80.0,
        consensus_score=80.0,
        feature_snapshot_json="{}",
        market_context_json="{}",
        skill_breakdown_json="[]",
        agent_summary_json="{}",
        risk_tags_json="[]",
        info_tags_json="[]",
    )


def _persist(db: Session, *rows: object) -> None:
    db.add_all(rows)
    db.commit()


def _add_fact(db: Session, *, kind: str, run: RecommendationRun, rec: Recommendation) -> str:
    now = datetime(2026, 8, 2, 9, 30)
    if kind == "outcome_version":
        db.add(
            RecommendationOutcomeVersion(
                version_id="outcome_version_guard",
                recommendation_id=rec.recommendation_id,
                calculation_version="outcome-v1",
                entry_policy_id="entry-v1",
                horizon_days=5,
                label_source="auto",
                result_json="{}",
            )
        )
        return "outcome_version"
    if kind == "outcome":
        db.add(
            RecommendationOutcome(
                recommendation_id=rec.recommendation_id,
                outcome_label="pending",
            )
        )
        return "outcome"
    if kind == "execution_state":
        db.add(
            RecommendationExecutionState(
                recommendation_id=rec.recommendation_id,
                latest_decision_code="confirmed",
                latest_checked_at=now,
            )
        )
        return "execution_state"
    if kind == "intraday_lock":
        monitor = IntradayLockMonitor(
            monitor_id="monitor_guard",
            recommendation_id=rec.recommendation_id,
            code=rec.code,
            trade_date=rec.trade_date,
            entry_date=rec.trade_date,
            entry_price=10.0,
            target_pct=5.0,
        )
        signal = IntradayLockSignal(
            signal_id="signal_guard",
            monitor_id=monitor.monitor_id,
            code=rec.code,
            trade_date=rec.trade_date,
            target_pct=5.0,
            trigger_time="10:00:00",
            trigger_price=10.5,
        )
        db.add_all([monitor, signal])
        return "intraday_lock_monitor"
    if kind == "classification_audit":
        db.add(
            RecommendationEvidenceClassificationAudit(
                plan_hash="a" * 64,
                run_id=run.run_id,
                planned_evidence_class="live_forward",
                classification_reason="fixture",
                recommendation_count=1,
                outcome_count=0,
                planned_at=now,
            )
        )
        return "classification_audit"
    if kind == "agent_log":
        db.add(
            AgentDecisionLog(
                id="agent_log_guard",
                recommendation_id=rec.recommendation_id,
                run_id=run.run_id,
                agent_name="guard",
                output_json="{}",
            )
        )
        return "agent_decision_log"
    if kind == "api_log":
        db.add(
            ApiCallLog(
                run_id=run.run_id,
                skill_id="guard",
                model="fixture",
                endpoint="fixture",
            )
        )
        return "api_call_log"
    raise AssertionError(f"unknown fact kind: {kind}")


def test_fresh_run_delete_removes_only_mutable_decorations() -> None:
    db = _database()
    try:
        run = _run()
        rec = _recommendation(run.run_id)
        _persist(
            db,
            run,
            rec,
            ReviewNote(
                note_id="review_guard",
                recommendation_id=rec.recommendation_id,
            ),
            WatchItem(
                watch_id="watch_guard",
                code=rec.code,
                source_run_id=run.run_id,
                source_recommendation_id=rec.recommendation_id,
            ),
        )

        assert delete_run(db, run.run_id) is True

        assert db.get(RecommendationRun, run.run_id) is None
        assert db.get(Recommendation, rec.recommendation_id) is None
        assert db.get(ReviewNote, "review_guard") is None
        assert db.get(WatchItem, "watch_guard") is None
    finally:
        db.close()


@pytest.mark.parametrize(
    ("kind", "expected_blocker"),
    [
        ("outcome_version", "outcome_version"),
        ("outcome", "outcome"),
        ("execution_state", "execution_state"),
        ("intraday_lock", "intraday_lock_monitor"),
        ("classification_audit", "classification_audit"),
        ("agent_log", "agent_decision_log"),
        ("api_log", "api_call_log"),
    ],
)
def test_irreversible_downstream_facts_block_run_delete(
    kind: str,
    expected_blocker: str,
) -> None:
    db = _database()
    try:
        run = _run(run_id=f"run_guard_{kind}")
        rec = _recommendation(run.run_id, f"rec_guard_{kind}")
        _persist(db, run, rec)
        assert _add_fact(db, kind=kind, run=run, rec=rec) == expected_blocker
        db.commit()

        with pytest.raises(DownstreamFactDeletionBlocked) as raised:
            delete_run(db, run.run_id)

        assert expected_blocker in raised.value.blockers
        assert db.get(RecommendationRun, run.run_id) is not None
        assert db.get(Recommendation, rec.recommendation_id) is not None
    finally:
        db.close()


def test_direct_recommendation_delete_checks_parent_run_audit_and_outcome_version() -> None:
    db = _database()
    try:
        run = _run()
        rec = _recommendation(run.run_id)
        _persist(db, run, rec)
        _add_fact(db, kind="classification_audit", run=run, rec=rec)
        _add_fact(db, kind="outcome_version", run=run, rec=rec)
        db.commit()

        with pytest.raises(DownstreamFactDeletionBlocked) as raised:
            delete_recommendation(db, rec.recommendation_id)

        assert raised.value.subject_kind == "recommendation"
        assert set(raised.value.blockers) >= {"classification_audit", "outcome_version"}
        assert db.get(Recommendation, rec.recommendation_id) is not None
    finally:
        db.close()


def test_run_delete_preflights_every_recommendation_before_deleting_decorations() -> None:
    db = _database()
    try:
        run = _run()
        safe = _recommendation(run.run_id, "rec_guard_safe")
        protected = _recommendation(run.run_id, "rec_guard_protected")
        _persist(
            db,
            run,
            safe,
            protected,
            ReviewNote(note_id="review_safe", recommendation_id=safe.recommendation_id),
            WatchItem(
                watch_id="watch_safe",
                code=safe.code,
                source_run_id=run.run_id,
                source_recommendation_id=safe.recommendation_id,
            ),
        )
        _add_fact(db, kind="outcome_version", run=run, rec=protected)
        db.commit()

        with pytest.raises(DownstreamFactDeletionBlocked):
            delete_run(db, run.run_id)

        assert db.get(Recommendation, safe.recommendation_id) is not None
        assert db.get(ReviewNote, "review_safe") is not None
        assert db.get(WatchItem, "watch_safe") is not None
    finally:
        db.close()


@pytest.mark.parametrize(
    "run_overrides",
    [
        {"status": "running"},
        {"reconstruction_batch_id": "recon-v1-fixture"},
    ],
)
def test_active_or_reconstruction_runs_cannot_be_physically_deleted(
    run_overrides: dict[str, object],
) -> None:
    db = _database()
    try:
        run = _run(**run_overrides)
        rec = _recommendation(run.run_id)
        _persist(db, run, rec)

        with pytest.raises(DownstreamFactDeletionBlocked) as raised:
            delete_run(db, run.run_id)

        assert (
            "active_run" in raised.value.blockers
            or "reconstruction_batch_member" in raised.value.blockers
        )
    finally:
        db.close()


def test_delete_apis_return_stable_conflict_details_for_protected_facts() -> None:
    db = _database()
    try:
        run = _run()
        rec = _recommendation(run.run_id)
        _persist(db, run, rec)
        _add_fact(db, kind="outcome_version", run=run, rec=rec)
        db.commit()

        with pytest.raises(HTTPException) as run_error:
            runs_api.delete(run.run_id, db=db)
        with pytest.raises(HTTPException) as recommendation_error:
            recommendations_api.delete(rec.recommendation_id, db=db)

        assert run_error.value.status_code == 409
        assert recommendation_error.value.status_code == 409
        assert run_error.value.detail == {
            "code": "downstream_facts_exist",
            "subject": "run",
            "id": run.run_id,
            "blockers": ["outcome_version"],
        }
        assert recommendation_error.value.detail == {
            "code": "downstream_facts_exist",
            "subject": "recommendation",
            "id": rec.recommendation_id,
            "blockers": ["outcome_version"],
        }
    finally:
        db.close()
