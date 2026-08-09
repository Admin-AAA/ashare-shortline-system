from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
import json
from types import SimpleNamespace

import app.services.backtest.quality_validation as quality_validation_module
from app.engine.backtest import (
    BacktestRequest,
    DateRange,
    ExecutionPolicy,
    Trade,
    TradeOutcome,
    canonical_fingerprint,
)
from app.services.backtest.quality_validation import (
    FrozenQualityAcceptance,
    FrozenQualityProvenance,
    FrozenQualityValidationSpec,
    _evaluate_frozen_quality_holdout,
    evaluate_frozen_quality_backtest,
    quality_policy_fingerprint,
)
from app.services.backtest.strategy_quality import (
    QualityCostPolicy,
    StrategyQualityConfig,
    StrategyTradeEvidence,
)


def _evidence(index: int, *, net: float, regime: str) -> StrategyTradeEvidence:
    signal_date = (date(2025, 1, 1) + timedelta(days=index)).isoformat()
    trade = Trade(
        code=f"{index:06d}",
        signal_date=signal_date,
        entry_date=signal_date,
        exit_date=signal_date,
        entry_price=10.0,
        exit_price=10.0 * (1 + net / 100),
        gross_return_pct=net + 0.067,
        net_return_pct=net,
        position_size=0.1,
        holding_days=1,
        outcome_label=TradeOutcome.SUCCESS if net > 0 else TradeOutcome.FAILED,
        exit_reason="take_profit" if net > 0 else "stop_loss",
    )
    return StrategyTradeEvidence(
        trade=trade,
        score=90,
        regime=regime,
        setup="主线",
        is_main_theme=True,
        industry=str(index),
        theme=str(index),
        signal_price=10.0,
    )


def _config() -> StrategyQualityConfig:
    return StrategyQualityConfig(
        strategy_id="small-cap-thematic-momentum",
        min_score=85,
        allowed_regimes=("强势", "结构性强势"),
        require_main_theme=True,
        required_setup_keywords=("主线",),
        single_name_cap=1.0,
        strategy_cap=1.0,
        gross_exposure_cap=1.0,
        industry_cap=1.0,
        theme_cap=1.0,
        max_positions_per_signal_day=10,
    )


def _spec(config: StrategyQualityConfig, cost: QualityCostPolicy) -> FrozenQualityValidationSpec:
    signal_dates = [
        (date(2025, 1, 1) + timedelta(days=index)).isoformat()
        for index in range(9)
    ]
    return FrozenQualityValidationSpec(
        strategy_id=config.strategy_id,
        strategy_version="frozen-v1",
        holdout_date_from="2025-01-01",
        holdout_date_to="2025-06-30",
        discovery_date_from="2025-07-14",
        expected_signal_days=len(signal_dates),
        signal_dates_fingerprint=canonical_fingerprint(signal_dates),
        quality_fingerprint=quality_policy_fingerprint(config, cost),
        execution_policy_fingerprint="a" * 64,
        parameter_fingerprint="b" * 64,
        acceptance=FrozenQualityAcceptance(
            min_executed_trades=6,
            min_signal_days=6,
            min_represented_regimes=2,
        ),
    )


def _provenance(temporality: str = "point_in_time") -> FrozenQualityProvenance:
    return FrozenQualityProvenance(
        backtest_id="btr_test",
        snapshot_id=f"bts_{'c' * 64}",
        input_manifest_hash="c" * 64,
        snapshot_manifest_hash="d" * 64,
        execution_version="execution-v1",
        request_fingerprint="e" * 64,
        strategy_version="frozen-v1",
        execution_policy_fingerprint="a" * 64,
        parameter_fingerprint="b" * 64,
        holdout_population_fingerprint="f" * 64,
        claim_experiment_id="strategy-quality:test",
        output_evidence_fingerprint="9" * 64,
        stock_metadata_temporality=temporality,
    )


def test_frozen_holdout_can_historically_qualify_but_never_self_promotes() -> None:
    config = _config()
    cost = QualityCostPolicy()
    evidences = tuple(
        _evidence(index, net=2.0 if index % 3 else -0.5, regime=("强势", "结构性强势")[index % 2])
        for index in range(9)
    )

    result = _evaluate_frozen_quality_holdout(
        evidences,
        signal_dates=(item.trade.signal_date for item in evidences),
        spec=_spec(config, cost),
        config=config,
        cost_policy=cost,
        provenance=_provenance(),
    )

    assert result["status"] == "HISTORICALLY_QUALIFIED"
    assert result["historically_qualified"] is True
    assert result["production_release_ready"] is False
    assert result["production_blocker"] == "forward_canary_required"
    assert result["positive_fold_count"] == 3
    assert all(result["acceptance_gates"].values())


def test_frozen_holdout_fails_closed_on_policy_drift() -> None:
    config = _config()
    cost = QualityCostPolicy()
    drifted = StrategyQualityConfig(strategy_id=config.strategy_id, min_score=86)

    try:
        _evaluate_frozen_quality_holdout(
            (),
            signal_dates=("2025-01-01",),
            spec=_spec(config, cost),
            config=drifted,
            cost_policy=cost,
            provenance=_provenance(),
        )
    except ValueError as exc:
        assert "differs from frozen spec" in str(exc)
    else:
        raise AssertionError("policy drift must fail closed")


def test_frozen_holdout_fails_closed_on_signal_date_population_drift() -> None:
    config = _config()
    cost = QualityCostPolicy()
    evidences = (_evidence(0, net=1.0, regime="强势"),)

    try:
        _evaluate_frozen_quality_holdout(
            evidences,
            signal_dates=(item.trade.signal_date for item in evidences),
            spec=_spec(config, cost),
            config=config,
            cost_policy=cost,
            provenance=_provenance(),
        )
    except ValueError as exc:
        assert "count differs" in str(exc)
    else:
        raise AssertionError("signal-date population drift must fail closed")


def test_current_static_stock_metadata_blocks_historical_qualification() -> None:
    config = _config()
    cost = QualityCostPolicy()
    spec = _spec(config, cost)
    evidences = tuple(
        _evidence(index, net=2.0, regime=("强势", "结构性强势")[index % 2])
        for index in range(9)
    )

    result = _evaluate_frozen_quality_holdout(
        evidences,
        signal_dates=(item.trade.signal_date for item in evidences),
        spec=spec,
        config=config,
        cost_policy=cost,
        provenance=_provenance("current_static"),
    )

    assert result["acceptance_gates"]["point_in_time_stock_metadata"] is False
    assert result["status"] == "INVALID_EVIDENCE"
    assert result["historically_qualified"] is False
    assert "point_in_time_stock_metadata" in result["failed_gates"]


def test_frozen_evidence_fingerprint_binds_snapshot_and_run_provenance() -> None:
    config = _config()
    cost = QualityCostPolicy()
    evidences = tuple(
        _evidence(index, net=2.0, regime=("强势", "结构性强势")[index % 2])
        for index in range(9)
    )
    kwargs = {
        "signal_dates": (item.trade.signal_date for item in evidences),
        "spec": _spec(config, cost),
        "config": config,
        "cost_policy": cost,
    }

    first = _evaluate_frozen_quality_holdout(
        evidences,
        provenance=_provenance(),
        **kwargs,
    )
    second_provenance = replace(_provenance(), backtest_id="btr_second")
    second = _evaluate_frozen_quality_holdout(
        evidences,
        provenance=second_provenance,
        **{**kwargs, "signal_dates": (item.trade.signal_date for item in evidences)},
    )

    assert first["evidence_fingerprint"] != second["evidence_fingerprint"]


def test_spec_fingerprint_changes_with_acceptance_or_identity() -> None:
    config = _config()
    cost = QualityCostPolicy()
    first = _spec(config, cost)
    second = replace(
        first,
        parameter_fingerprint=canonical_fingerprint({"minimum_signal_score": 71}),
    )

    assert first.fingerprint != second.fingerprint


def test_formal_evaluator_derives_provenance_from_verified_repository(monkeypatch) -> None:
    config = _config()
    cost = QualityCostPolicy()
    policy = ExecutionPolicy()
    parameter_set = {"minimum_signal_score": 70.0}
    request = BacktestRequest(
        strategy_id=config.strategy_id,
        strategy_version="frozen-v1",
        date_selector=DateRange("2025-01-01", "2025-06-30"),
        execution_policy=policy,
        parameter_set=parameter_set,
    )
    input_manifest_hash = "c" * 64
    snapshot_id = f"bts_{input_manifest_hash}"
    execution_version = "execution-v1"
    request_fingerprint = request.request_fingerprint(
        snapshot_id=snapshot_id,
        effective_strategy_version="frozen-v1",
        execution_version=execution_version,
    )
    evidences = tuple(
        _evidence(index, net=2.0, regime=("强势", "结构性强势")[index % 2])
        for index in range(9)
    )
    signal_dates = tuple(item.trade.signal_date for item in evidences)
    spec = replace(
        _spec(config, cost),
        execution_policy_fingerprint=policy.fingerprint(),
        parameter_fingerprint=canonical_fingerprint(parameter_set),
    )
    run = SimpleNamespace(
        backtest_id="btr_verified",
        status="completed",
        strategy_id=config.strategy_id,
        strategy_version="frozen-v1",
        execution_policy_fingerprint=policy.fingerprint(),
        snapshot_id=snapshot_id,
        execution_version=execution_version,
        request_fingerprint=request_fingerprint,
        request_json=request.to_canonical_json(),
        result_json=json.dumps(
            {
                "signal_dates": signal_dates,
                "output_evidence_fingerprint": "9" * 64,
            }
        ),
    )

    class FakeRepository:
        def __init__(self, db):
            self.db = db

        def get_run(self, backtest_id):
            assert backtest_id == "btr_verified"
            return run

        def get_snapshot(self, requested_snapshot_id):
            assert requested_snapshot_id == snapshot_id
            return SimpleNamespace(
                manifest_hash="d" * 64,
                partition_manifest_json=json.dumps(
                    {
                        "schema_version": 2,
                        "partitions": [],
                        "artifacts_by_signal_date": {
                            signal_date: [] for signal_date in signal_dates
                        },
                    }
                ),
            )

        def require_holdout_claim(self, *, holdout_fingerprint, experiment_id):
            assert len(holdout_fingerprint) == 64
            assert experiment_id.startswith("strategy-quality:")
            return SimpleNamespace(status="consumed")

        def output_evidence_fingerprint(self, backtest_id):
            assert backtest_id == "btr_verified"
            return "9" * 64

    class FakeRunner:
        def __init__(self, repository):
            assert isinstance(repository, FakeRepository)

        def load_snapshot_bundle(self, requested_snapshot_id):
            assert requested_snapshot_id == snapshot_id
            return SimpleNamespace(
                signal_dates=signal_dates,
                snapshot=SimpleNamespace(input_manifest_hash=input_manifest_hash),
            )

        def snapshot_stock_metadata_temporality(self, requested_snapshot_id):
            assert requested_snapshot_id == snapshot_id
            return "point_in_time"

    class FakeResult:
        def all(self):
            return [SimpleNamespace(trade_json=item.trade.to_canonical_json()) for item in evidences]

    class FakeDb:
        def scalars(self, statement):
            del statement
            return FakeResult()

    monkeypatch.setattr(quality_validation_module, "ResearchBacktestRepository", FakeRepository)
    monkeypatch.setattr(quality_validation_module, "ResearchBacktestRunner", FakeRunner)
    monkeypatch.setattr(
        "app.services.backtest.strategy_profile.quality_evidences_for_run",
        lambda db, loaded_run, trades: evidences,
    )

    result = evaluate_frozen_quality_backtest(
        FakeDb(),
        backtest_id="btr_verified",
        spec=spec,
        config=config,
        cost_policy=cost,
    )

    assert result["status"] == "HISTORICALLY_QUALIFIED"
    assert result["provenance"]["snapshot_id"] == snapshot_id
    assert result["provenance"]["request_fingerprint"] == request_fingerprint
