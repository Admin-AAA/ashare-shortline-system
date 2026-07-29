from __future__ import annotations

from datetime import datetime

from app.models.tables import Recommendation, RecommendationOutcome, RecommendationRun
from app.services.workbench_service import (
    invalidate_recommendation_history_cache,
    list_recommendation_history,
)
from app.services.outcome_service import invalidate_outcome_metrics_cache, recent_formal_win_rate
from app.services.official_sample_scope import evaluation_scope_for_run


def test_new_run_scope_is_explicitly_derived_from_execution_intent():
    assert evaluation_scope_for_run(
        run_mode="formal", trade_date_override=None, matrix_run=False
    ) == "live_forward"
    assert evaluation_scope_for_run(
        run_mode="formal", trade_date_override="2026-07-01", matrix_run=False
    ) == "historical_replay"
    assert evaluation_scope_for_run(
        run_mode="formal", trade_date_override=None, matrix_run=True
    ) == "historical_replay"
    assert evaluation_scope_for_run(
        run_mode="experiment", trade_date_override=None
    ) == "experiment"


def _add_settled_recommendation(
    db,
    *,
    suffix: str,
    run_status: str,
    trade_date: str,
    recommended_at: datetime,
    code: str = "999991",
    final_score: float = 80.0,
    executed_return: float = 1.0,
    data_as_of: str | None = None,
) -> None:
    run_id = f"test_official_scope_run_{suffix}"
    recommendation_id = f"rec_test_official_scope_{suffix}"
    db.add(
        RecommendationRun(
            run_id=run_id,
            run_name="official sample scope contract",
            run_mode="formal",
            evaluation_scope="live_forward",
            trade_date=trade_date,
            data_as_of=data_as_of or trade_date,
            source_snapshot_id=f"snapshot_{suffix}",
            status=run_status,
        )
    )
    db.add(
        Recommendation(
            recommendation_id=recommendation_id,
            run_id=run_id,
            trade_date=trade_date,
            recommended_at=recommended_at,
            code=code,
            name="scope fixture",
            recommend_price=10.0,
            final_score=final_score,
        )
    )
    db.add(
        RecommendationOutcome(
            recommendation_id=recommendation_id,
            entry_triggered=True,
            executed_return=executed_return,
            outcome_label="success" if executed_return > 0.07 else "failed",
        )
    )
    db.flush()


def test_history_excludes_interrupted_formal_runs(db):
    _add_settled_recommendation(
        db,
        suffix="interrupted",
        run_status="interrupted",
        trade_date="2099-01-04",
        recommended_at=datetime(2099, 1, 4, 6, 0),
    )
    invalidate_recommendation_history_cache()

    result = list_recommendation_history(
        db,
        code="999991",
        date_from="2099-01-04",
        date_to="2099-01-04",
        limit=50,
    )

    assert result["total"] == 0
    assert result["summary"]["trade_count"] == 0


def test_recent_win_rate_uses_the_same_completed_forward_deduplicated_scope(db):
    common = {
        "trade_date": "2099-01-05",
        "recommended_at": datetime(2099, 1, 5, 6, 0),
        "code": "999992",
    }
    _add_settled_recommendation(
        db,
        suffix="recent_live_winner",
        run_status="completed",
        final_score=90.0,
        executed_return=1.0,
        **common,
    )
    _add_settled_recommendation(
        db,
        suffix="recent_duplicate_loser",
        run_status="completed",
        final_score=70.0,
        executed_return=-2.0,
        **common,
    )
    _add_settled_recommendation(
        db,
        suffix="recent_interrupted",
        run_status="interrupted",
        final_score=95.0,
        executed_return=3.0,
        **common,
    )
    _add_settled_recommendation(
        db,
        suffix="recent_replay",
        run_status="completed",
        trade_date="2099-01-05",
        recommended_at=datetime(2099, 1, 6, 6, 0),
        code="999992",
        final_score=99.0,
        executed_return=4.0,
    )
    invalidate_outcome_metrics_cache()

    result = recent_formal_win_rate(db, window_trade_days=1)

    assert result["trade_dates"] == ["2099-01-05"]
    assert result["sample_count"] == 1
    assert result["success_count"] == 1
    assert result["net_win_rate"] == 100.0


def test_history_excludes_recommendations_generated_after_the_claimed_trade_date(db):
    _add_settled_recommendation(
        db,
        suffix="historical_replay",
        run_status="completed",
        trade_date="2099-01-04",
        recommended_at=datetime(2099, 1, 5, 6, 0),
    )
    invalidate_recommendation_history_cache()

    result = list_recommendation_history(
        db,
        code="999991",
        date_from="2099-01-04",
        date_to="2099-01-04",
        limit=50,
    )

    assert result["total"] == 0
    assert result["summary"]["trade_count"] == 0


def test_history_excludes_runs_whose_market_data_is_newer_than_signal_date(db):
    _add_settled_recommendation(
        db,
        suffix="future_market_data",
        run_status="completed",
        trade_date="2099-01-04",
        recommended_at=datetime(2099, 1, 4, 6, 0),
        data_as_of="2099-01-05",
    )
    invalidate_recommendation_history_cache()

    result = list_recommendation_history(
        db,
        code="999991",
        date_from="2099-01-04",
        date_to="2099-01-04",
        limit=50,
    )

    assert result["total"] == 0
