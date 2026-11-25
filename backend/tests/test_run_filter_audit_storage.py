"""过滤审计快照独立表 recommendation_run_filter_audits 的读写往返。"""
from __future__ import annotations

from app.models.tables import RecommendationRun, RecommendationRunFilterAudit
from app.services.workbench_service import get_run_filter_audit, store_filter_audit


def _run(run_id: str) -> RecommendationRun:
    return RecommendationRun(
        run_id=run_id,
        run_name="audit storage",
        trade_date="2099-03-02",
        status="completed",
        params_json="{}",
        market_snapshot_json="{}",
        result_summary_json="{}",
    )


def test_store_and_read_filter_audit_roundtrip(db):
    run_id = "__test_audit_roundtrip__"
    db.add(_run(run_id))
    db.flush()

    store_filter_audit(db, run_id, {"empty_reason": "first", "rows": []})
    db.flush()
    assert get_run_filter_audit(db, run_id)["empty_reason"] == "first"

    store_filter_audit(db, run_id, {"empty_reason": "second", "rows": []})
    db.flush()
    assert get_run_filter_audit(db, run_id)["empty_reason"] == "second"
    assert db.query(RecommendationRunFilterAudit).filter_by(run_id=run_id).count() == 1


def test_missing_audit_row_returns_fallback_dict(db):
    run_id = "__test_audit_missing__"
    db.add(_run(run_id))
    db.flush()

    audit = get_run_filter_audit(db, run_id)

    assert isinstance(audit, dict)


def test_unknown_run_returns_none(db):
    assert get_run_filter_audit(db, "__test_audit_no_such_run__") is None
