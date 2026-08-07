from datetime import datetime
import uuid

from app.api.recommendations import outcome_summary
from app.models.tables import Recommendation, RecommendationOutcome, RecommendationRun
from app.services.evidence import identity_from_persisted_run
from app.services.outcome_service import _precision_at_k


def test_outcome_summary_excludes_unverified_formal_rows(db) -> None:
    suffix = uuid.uuid4().hex[:8]
    code = f"99{suffix[:4]}"
    canonical_run_id = f"run_summary_canonical_{suffix}"
    unverified_run_id = f"run_summary_unverified_{suffix}"
    canonical_rec_id = f"rec_summary_canonical_{suffix}"
    unverified_rec_id = f"rec_summary_unverified_{suffix}"

    canonical_run = RecommendationRun(
        run_id=canonical_run_id,
        run_name="canonical summary",
        run_mode="formal",
        trade_date="2026-07-24",
        status="completed",
        evidence_class="live_forward",
        data_as_of="2026-07-24",
        source_snapshot_id=f"summary-snapshot-{suffix}",
        actual_generated_at=datetime(2026, 7, 24, 6, 0),
        evidence_identity_hash="d" * 64,
        strategy_policy_fingerprint="a" * 64,
        execution_policy_fingerprint="b" * 64,
        code_revision="c" * 40,
        provenance_schema_version=1,
    )
    canonical_run.evidence_identity_hash = identity_from_persisted_run(
        canonical_run
    ).identity_hash
    db.add_all(
        [
            canonical_run,
            RecommendationRun(
                run_id=unverified_run_id,
                run_name="unverified summary",
                run_mode="formal",
                trade_date="2026-07-25",
                status="completed",
            ),
        ]
    )
    db.add_all(
        [
            Recommendation(
                recommendation_id=canonical_rec_id,
                run_id=canonical_run_id,
                trade_date="2026-07-24",
                code=code,
                name="canonical",
                final_score=80,
            ),
            Recommendation(
                recommendation_id=unverified_rec_id,
                run_id=unverified_run_id,
                trade_date="2026-07-25",
                code=code,
                name="unverified",
                final_score=90,
            ),
        ]
    )
    db.add_all(
        [
            RecommendationOutcome(
                recommendation_id=canonical_rec_id,
                outcome_label="success",
                entry_triggered=True,
                executed_return=5.0,
            ),
            RecommendationOutcome(
                recommendation_id=unverified_rec_id,
                outcome_label="failed",
                entry_triggered=True,
                executed_return=-9.0,
            ),
        ]
    )
    db.flush()

    result = outcome_summary(codes=[code], db=db)

    assert result[code]["settled"] == 1
    assert result[code]["wins"] == 1


def test_default_precision_at_k_excludes_unverified_formal_rows(db) -> None:
    suffix = uuid.uuid4().hex[:8]
    trade_date = "2099-02-01"
    canonical_run_id = f"run_precision_canonical_{suffix}"
    unverified_run_id = f"run_precision_unverified_{suffix}"
    canonical_rec_id = f"rec_precision_canonical_{suffix}"
    unverified_rec_id = f"rec_precision_unverified_{suffix}"
    db.add_all(
        [
            RecommendationRun(
                run_id=canonical_run_id,
                run_name="canonical precision",
                run_mode="formal",
                trade_date=trade_date,
                status="completed",
                evidence_class="live_forward",
                data_as_of=trade_date,
                source_snapshot_id=f"precision-snapshot-{suffix}",
                actual_generated_at=datetime(2099, 2, 1, 6, 0),
                evidence_identity_hash="d" * 64,
                strategy_policy_fingerprint="a" * 64,
                execution_policy_fingerprint="b" * 64,
                code_revision="c" * 40,
                provenance_schema_version=1,
            ),
            RecommendationRun(
                run_id=unverified_run_id,
                run_name="unverified precision",
                run_mode="formal",
                trade_date=trade_date,
                status="completed",
            ),
            Recommendation(
                recommendation_id=canonical_rec_id,
                run_id=canonical_run_id,
                trade_date=trade_date,
                code="990101",
                name="canonical",
                final_score=80,
            ),
            Recommendation(
                recommendation_id=unverified_rec_id,
                run_id=unverified_run_id,
                trade_date=trade_date,
                code="990102",
                name="unverified",
                final_score=99,
            ),
            RecommendationOutcome(
                recommendation_id=canonical_rec_id,
                outcome_label="success",
                entry_triggered=True,
                executed_return=5.0,
            ),
            RecommendationOutcome(
                recommendation_id=unverified_rec_id,
                outcome_label="failed",
                entry_triggered=True,
                executed_return=-9.0,
            ),
        ]
    )
    db.flush()

    result = _precision_at_k(
        db,
        (1,),
        start_date=trade_date,
        end_date=trade_date,
    )

    assert result[0]["sample_runs"] == 1
    assert result[0]["evaluated_count"] == 1
    assert result[0]["success_count"] == 1
    assert result[0]["precision"] == 1.0
