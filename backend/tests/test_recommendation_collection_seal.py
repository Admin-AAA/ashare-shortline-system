from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session

from app.models.tables import Recommendation, RecommendationCollectionSeal
from app.services.runs.collection_seal import (
    COLLECTION_SEAL_SCHEMA_VERSION,
    RecommendationCollectionSealError,
    collection_side_effect_key,
    recommendation_collection_snapshot,
    seal_recommendation_collection,
    verify_recommendation_collection_seal,
)
from app.services.runs.contracts import RunErrorCode


NOW = datetime(2026, 8, 7, 9, 30, 0, 123456)


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Recommendation.__table__.create(engine)
    RecommendationCollectionSeal.__table__.create(engine)
    return Session(engine)


def _recommendation(
    recommendation_id: str,
    *,
    run_id: str = "run-seal",
    code: str = "000001",
    final_score: float = 80.0,
    status: str = "pending",
) -> Recommendation:
    return Recommendation(
        recommendation_id=recommendation_id,
        run_id=run_id,
        trade_date="2026-08-07",
        recommended_at=NOW,
        code=code,
        name=f"股票{code}",
        industry="银行",
        recommend_price=10.25,
        bucket="daily-stock-analysis-skill",
        final_score=final_score,
        consensus_score=78.5,
        setup_type="breakout",
        entry_rule="站稳均线",
        invalid_rule="跌破止损",
        rationale="测试推荐",
        risk_tags_json='["波动"]',
        info_tags_json='["测试"]',
        recent_repeat=False,
        feature_snapshot_json='{"a":1}',
        market_context_json='{"regime":"active"}',
        skill_breakdown_json='[{"score":80}]',
        agent_summary_json='{"stance":"support"}',
        status=status,
    )


def test_empty_collection_is_a_valid_persisted_seal() -> None:
    db = _session()
    try:
        sealed = seal_recommendation_collection(db, "run-empty", sealed_at=NOW)
        verification = verify_recommendation_collection_seal(
            db,
            "run-empty",
            expected_side_effect_key=sealed.checkpoint_side_effect_key,
        )

        assert sealed.schema_version == COLLECTION_SEAL_SCHEMA_VERSION
        assert sealed.recommendation_row_count == 0
        assert sealed.checkpoint_side_effect_key == collection_side_effect_key(
            "run-empty", sealed.collection_digest
        )
        assert verification.valid is True
        assert verification.observed_row_count == 0
    finally:
        db.close()


def test_digest_is_order_independent_and_ignores_mutable_status() -> None:
    first = _session()
    second = _session()
    try:
        first.add_all(
            [
                _recommendation("rec-b", code="000002"),
                _recommendation("rec-a", code="000001"),
            ]
        )
        second.add_all(
            [
                _recommendation("rec-a", code="000001", status="success"),
                _recommendation("rec-b", code="000002", status="failed"),
            ]
        )
        first.flush()
        second.flush()

        first_snapshot = recommendation_collection_snapshot(first, "run-seal")
        second_snapshot = recommendation_collection_snapshot(second, "run-seal")

        assert first_snapshot.row_count == second_snapshot.row_count == 2
        assert first_snapshot.collection_digest == second_snapshot.collection_digest
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    (
        ("immutable_field", RunErrorCode.RESUME_SEAL_DIGEST_MISMATCH),
        ("row_count", RunErrorCode.RESUME_SEAL_ROW_COUNT_MISMATCH),
        ("checkpoint_key", RunErrorCode.RESUME_SEAL_KEY_MISMATCH),
    ),
)
def test_seal_verification_rejects_any_collection_or_key_change(
    mutation: str,
    expected_code: RunErrorCode,
) -> None:
    db = _session()
    try:
        db.add(_recommendation("rec-a"))
        db.flush()
        seal = seal_recommendation_collection(db, "run-seal", sealed_at=NOW)

        if mutation == "immutable_field":
            db.execute(
                update(Recommendation)
                .where(Recommendation.recommendation_id == "rec-a")
                .values(final_score=81.0)
            )
        elif mutation == "row_count":
            db.add(_recommendation("rec-b", code="000002"))
        else:
            pass
        db.flush()

        verification = verify_recommendation_collection_seal(
            db,
            "run-seal",
            expected_side_effect_key=(
                "recommendation-seal:v1:tampered"
                if mutation == "checkpoint_key"
                else collection_side_effect_key("run-seal", seal.collection_digest)
            ),
        )

        assert verification.valid is False
        assert verification.block_code == expected_code.value
    finally:
        db.close()


def test_existing_seal_is_idempotent_only_for_the_identical_collection() -> None:
    db = _session()
    try:
        db.add(_recommendation("rec-a"))
        db.flush()
        first = seal_recommendation_collection(db, "run-seal", sealed_at=NOW)
        second = seal_recommendation_collection(db, "run-seal", sealed_at=NOW)

        assert second is first

        db.add(_recommendation("rec-b", code="000002"))
        db.flush()
        with pytest.raises(RecommendationCollectionSealError) as exc_info:
            seal_recommendation_collection(db, "run-seal", sealed_at=NOW)
        assert exc_info.value.code == RunErrorCode.RESUME_SEAL_ROW_COUNT_MISMATCH.value
    finally:
        db.close()
