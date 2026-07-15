"""Request expand/backfill/cutover tests for recommendation_runs."""
from __future__ import annotations

import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.tables import RecommendationRun
from app.services.runs.request_persistence import (
    RunRequestCutoverError,
    RunRequestPersistenceError,
    backfill_canonical_run_requests,
    capture_run_partition_counts,
    load_canonical_run_request,
    verify_run_request_cutover,
)


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    RecommendationRun.__table__.create(engine)
    return Session(engine)


def _legacy_run(
    run_id: str,
    *,
    run_mode: str,
    status: str = "completed",
    params: dict[str, object] | None = None,
) -> RecommendationRun:
    values = {
        "run_mode": run_mode,
        "run_name": f"{run_id}-name",
        "preset_id": None,
        "market_scope": "all",
        "market_scope_value": None,
        "risk_template": "balanced",
        "score_line": 72.0,
        "output_count": 6,
        "data_source": "auto",
        "skills": [],
    }
    values.update(params or {})
    return RecommendationRun(
        run_id=run_id,
        run_name=str(values["run_name"]),
        run_mode=run_mode,
        trade_date="2026-07-14",
        started_at=datetime(2026, 7, 14),
        preset_id=values["preset_id"],
        market_scope=str(values["market_scope"]),
        market_scope_value=values["market_scope_value"],
        risk_template=str(values["risk_template"]),
        score_line=float(values["score_line"]),
        output_count=int(values["output_count"]),
        params_json=json.dumps(values, ensure_ascii=False),
        status=status,
    )


def test_backfill_is_batched_idempotent_and_preserves_legacy_params():
    session = _session()
    try:
        rows = [
            _legacy_run("run_formal", run_mode="formal"),
            _legacy_run("run_experiment", run_mode="experiment"),
            _legacy_run("run_backtest", run_mode="backtest"),
        ]
        session.add_all(rows)
        session.commit()
        before_params = {row.run_id: row.params_json for row in rows}
        before_counts = capture_run_partition_counts(session)

        report = backfill_canonical_run_requests(session, batch_size=1)

        assert report.scanned_rows == 3
        assert report.backfilled_rows == 3
        assert report.skipped_invalid_rows == 0
        assert report.skipped_mismatch_rows == 0
        assert report.counts_unchanged
        assert report.final_counts == before_counts
        assert report.remaining_unresolved_rows == 0
        for row in session.query(RecommendationRun).all():
            assert row.request_schema_version == 1
            assert row.request_json is not None
            assert row.params_json == before_params[row.run_id]
            assert json.loads(row.request_json)["run_mode"] == row.run_mode

        second = backfill_canonical_run_requests(session, batch_size=2)
        assert second.scanned_rows == 0
        assert second.backfilled_rows == 0
        assert second.final_counts == before_counts
    finally:
        session.close()


def test_backfill_does_not_version_invalid_or_mismatched_rows_and_cutover_blocks_active_run():
    session = _session()
    try:
        invalid = RecommendationRun(
            run_id="run_invalid",
            run_name="invalid",
            run_mode="formal",
            trade_date="2026-07-14",
            started_at=datetime(2026, 7, 14),
            params_json="not-json",
            status="completed",
        )
        mismatch = _legacy_run(
            "run_mismatch",
            run_mode="formal",
            status="running",
            params={"run_mode": "experiment"},
        )
        session.add_all([invalid, mismatch])
        session.commit()

        report = backfill_canonical_run_requests(session, batch_size=10)

        assert report.backfilled_rows == 0
        assert report.skipped_invalid_rows == 1
        assert report.skipped_mismatch_rows == 1
        assert report.remaining_unresolved_rows == 2
        assert invalid.request_schema_version is None
        assert invalid.request_json is None
        assert mismatch.request_schema_version is None
        assert mismatch.request_json is None
        with pytest.raises(RunRequestPersistenceError, match="unrecoverable active run"):
            verify_run_request_cutover(session)
    finally:
        session.close()


def test_canonical_worker_loader_rejects_missing_request_and_index_drift():
    session = _session()
    try:
        row = _legacy_run("run_cutover", run_mode="formal", status="running")
        session.add(row)
        session.commit()

        with pytest.raises(RunRequestCutoverError, match="backfill is required"):
            load_canonical_run_request(row)

        report = backfill_canonical_run_requests(session)
        assert report.backfilled_rows == 1
        row.run_mode = "experiment"
        with pytest.raises(RunRequestCutoverError, match="mismatch"):
            load_canonical_run_request(row)
    finally:
        session.close()
