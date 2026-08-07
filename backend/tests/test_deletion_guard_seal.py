"""Deletion guard: sealed recommendation collections are not physically deletable."""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.session import Base
from app.models.tables import Recommendation, RecommendationCollectionSeal, RecommendationRun
from app.services.runs.collection_seal import seal_recommendation_collection
from app.services.runs.deletion_guard import (
    DownstreamFactDeletionBlocked,
    assert_recommendation_physically_deletable,
    assert_run_physically_deletable,
)


NOW = datetime(2026, 8, 7, 10, 0, 0)


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _run(run_id: str, *, status: str = "completed") -> RecommendationRun:
    return RecommendationRun(
        run_id=run_id,
        run_name="seal guard fixture",
        run_mode="formal",
        evaluation_scope="live_forward",
        trade_date="2026-08-07",
        status=status,
        started_at=NOW,
    )


def _recommendation(
    recommendation_id: str,
    *,
    run_id: str = "run-sealed",
) -> Recommendation:
    return Recommendation(
        recommendation_id=recommendation_id,
        run_id=run_id,
        trade_date="2026-08-07",
        recommended_at=NOW,
        code="000001",
        name="股票000001",
        industry="银行",
        recommend_price=10.25,
        bucket="daily-stock-analysis-skill",
        final_score=80.0,
        consensus_score=78.5,
        setup_type="breakout",
        status="pending",
    )


def test_run_with_persisted_seal_is_not_physically_deletable() -> None:
    engine = _engine()
    with Session(engine) as db:
        db.add(_run("run-sealed"))
        db.add(_recommendation("rec-sealed"))
        db.flush()
        seal_recommendation_collection(db, "run-sealed", sealed_at=NOW)
        db.commit()

        with pytest.raises(DownstreamFactDeletionBlocked) as exc_info:
            assert_run_physically_deletable(db, db.get(RecommendationRun, "run-sealed"))
        assert exc_info.value.subject_kind == "run"
        assert "collection_seal" in exc_info.value.blockers
        assert "collection_seal" in exc_info.value.detail()["blockers"]


def test_unsealed_run_is_not_blocked_by_collection_seal() -> None:
    engine = _engine()
    with Session(engine) as db:
        db.add(_run("run-plain"))
        db.add(_recommendation("rec-plain", run_id="run-plain"))
        db.commit()

        recs = assert_run_physically_deletable(db, db.get(RecommendationRun, "run-plain"))
        assert tuple(rec.recommendation_id for rec in recs) == ("rec-plain",)


def test_recommendation_under_sealed_run_is_not_physically_deletable() -> None:
    engine = _engine()
    with Session(engine) as db:
        db.add(_run("run-sealed"))
        db.add(_recommendation("rec-sealed"))
        db.flush()
        seal_recommendation_collection(db, "run-sealed", sealed_at=NOW)
        db.commit()

        with pytest.raises(DownstreamFactDeletionBlocked) as exc_info:
            assert_recommendation_physically_deletable(
                db,
                db.get(Recommendation, "rec-sealed"),
            )
        assert exc_info.value.subject_kind == "recommendation"
        assert "collection_seal" in exc_info.value.blockers
