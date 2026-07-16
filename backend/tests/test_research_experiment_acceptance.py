"""Frozen OOS acceptance tests for research-only backtest experiments."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.tables import (
    BacktestDataSnapshot,
    BacktestExperiment,
    BacktestRequestClaim,
    BacktestRunV2,
)
from app.services.backtest.research_repository import (
    ExperimentValidationError,
    ResearchBacktestRepository,
)


SNAPSHOT_ID = "snapshot_experiment_fixture"
POLICY_FINGERPRINT = "a" * 64
CANDIDATE_FINGERPRINT = "b" * 64


@pytest.fixture
def frozen_experiment():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            BacktestDataSnapshot.__table__,
            BacktestRequestClaim.__table__,
            BacktestRunV2.__table__,
            BacktestExperiment.__table__,
        ],
    )
    session = sessionmaker(bind=engine)()
    try:
        repository = ResearchBacktestRepository(session)
        repository.persist_snapshot(
            snapshot_id=SNAPSHOT_ID,
            snapshot_version=2,
            partition_manifest={
                "schema_version": 2,
                "partitions": [],
                "artifacts_by_signal_date": {},
            },
            coverage={},
            missing_requirements=[],
        )
        claim = repository.claim_request(
            request_fingerprint="c" * 64,
            backtest_id="btr_experiment_baseline",
            snapshot_id=SNAPSHOT_ID,
            strategy_id="baseline-strategy",
            strategy_version="baseline-v1",
            request_schema_version=1,
            request_json={"strategy_id": "baseline-strategy"},
            execution_version="execution-v1:baseline",
            execution_policy_fingerprint=POLICY_FINGERPRINT,
            date_from="2026-01-01",
            date_to="2026-01-03",
        )
        repository.mark_running(claim.backtest_id)
        repository.complete_run(
            claim.backtest_id,
            result={
                "status": "completed",
                "signal_dates": ["2026-01-01", "2026-01-02", "2026-01-03"],
                "metrics": {"executed_trade_count": 3},
            },
        )
        experiment = repository.create_experiment(
            experiment_id="btexp_acceptance_fixture",
            hypothesis="OOS acceptance must be evaluated against frozen thresholds.",
            baseline_backtest_id=claim.backtest_id,
            snapshot_id=SNAPSHOT_ID,
            execution_policy_fingerprint=POLICY_FINGERPRINT,
            split_json={
                "train": ["2026-01-01"],
                "validation": ["2026-01-02"],
                "out_of_sample": ["2026-01-03"],
            },
            split_hash=None,
            candidate_strategy_id="candidate-strategy",
            candidate_strategy_version="candidate-v1",
            frozen_candidate_fingerprint=CANDIDATE_FINGERPRINT,
            acceptance={
                "min_trade_count": 2,
                "min_win_rate_pct": 55.0,
                "min_profit_loss_ratio": 1.2,
                "min_average_net_return_pct": 0.1,
                "max_drawdown_pct": 8.0,
            },
        )
        repository.freeze_experiment(
            experiment.experiment_id,
            frozen_candidate_fingerprint=CANDIDATE_FINGERPRINT,
        )
        session.commit()
        yield repository, experiment.experiment_id
    finally:
        session.close()
        engine.dispose()


def _complete_promotion(repository: ResearchBacktestRepository, experiment_id: str, metrics: dict[str, float]):
    experiment = repository.get_experiment(experiment_id)
    return repository.complete_experiment(
        experiment_id,
        status="promotion_candidate",
        conclusion={"out_of_sample_metrics": metrics},
        snapshot_id=experiment.snapshot_id,
        execution_policy_fingerprint=experiment.execution_policy_fingerprint,
        split_hash=experiment.split_hash,
        frozen_candidate_fingerprint=experiment.frozen_candidate_fingerprint,
    )


def test_promotion_candidate_rejects_oos_metrics_below_frozen_acceptance(frozen_experiment) -> None:
    repository, experiment_id = frozen_experiment

    with pytest.raises(ExperimentValidationError) as rejected:
        _complete_promotion(
            repository,
            experiment_id,
            {
                "executed_trade_count": 1,
                "win_rate_pct": 50.0,
                "profit_loss_ratio": 1.0,
                "average_net_return_pct": 0.0,
                "max_drawdown_pct": 9.0,
            },
        )

    assert set(rejected.value.details["failed_gates"]) == {
        "min_trade_count",
        "min_win_rate_pct",
        "min_profit_loss_ratio",
        "min_average_net_return_pct",
        "max_drawdown_pct",
    }
    assert repository.get_experiment(experiment_id).status == "frozen"


def test_promotion_candidate_accepts_qualifying_frozen_oos_evidence_idempotently(frozen_experiment) -> None:
    repository, experiment_id = frozen_experiment
    metrics = {
        "executed_trade_count": 3,
        "win_rate_pct": 66.7,
        "profit_loss_ratio": 1.5,
        "average_net_return_pct": 0.8,
        "max_drawdown_pct": 6.0,
    }

    completed = _complete_promotion(repository, experiment_id, metrics)
    again = _complete_promotion(repository, experiment_id, metrics)

    assert completed.status == again.status == "promotion_candidate"
    assert completed.conclusion_json == again.conclusion_json
    assert "out_of_sample_metrics" in completed.conclusion_json


def test_promotion_candidate_requires_complete_oos_metric_evidence(frozen_experiment) -> None:
    repository, experiment_id = frozen_experiment

    with pytest.raises(ExperimentValidationError, match="executed_trade_count"):
        _complete_promotion(repository, experiment_id, {})
