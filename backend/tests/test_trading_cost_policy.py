from app.engine.trading_costs import ADDITIONAL_COST_PCT, is_net_profitable
from app.models.tables import RecommendationOutcome
from app.services.evaluation_metrics import _is_accurate_record
from app.services.outcome_service import audit_trading_cost_boundary_labels
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def test_profitability_requires_strictly_covering_all_costs():
    assert ADDITIONAL_COST_PCT == 0.07
    assert not is_net_profitable(0.0699)
    assert not is_net_profitable(0.07)
    assert is_net_profitable(0.0701)


def test_evaluation_accuracy_uses_the_shared_cost_policy():
    """Metric records already carry post-fee net returns."""
    base = {"executed": True, "signal_valid": True}
    assert not _is_accurate_record({**base, "net_return_pct": 0.0})
    assert _is_accurate_record({**base, "net_return_pct": 0.0001})


def test_cost_policy_boundary_audit_is_bounded_and_idempotent():
    engine = create_engine("sqlite:///:memory:")
    RecommendationOutcome.__table__.create(engine)
    with Session(engine) as db:
        db.add_all(
            [
                RecommendationOutcome(
                    recommendation_id="exact",
                    entry_triggered=True,
                    executed_return=0.07,
                    outcome_label="failed",
                    outcome_label_legacy="failed",
                ),
                RecommendationOutcome(
                    recommendation_id="affected",
                    entry_triggered=True,
                    executed_return=0.08,
                    outcome_label="failed",
                    outcome_label_legacy="failed",
                ),
                RecommendationOutcome(
                    recommendation_id="outside",
                    entry_triggered=True,
                    executed_return=0.11,
                    outcome_label="failed",
                    outcome_label_legacy="failed",
                ),
            ]
        )
        db.commit()

        first = audit_trading_cost_boundary_labels(db)
        second = audit_trading_cost_boundary_labels(db)

        assert first["scanned_count"] == 1
        assert first["updated_count"] == 1
        assert db.get(RecommendationOutcome, "affected").outcome_label == "success"
        assert db.get(RecommendationOutcome, "exact").outcome_label == "failed"
        assert db.get(RecommendationOutcome, "outside").outcome_label == "failed"
        assert second["updated_count"] == 0
