from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
import json
from types import SimpleNamespace

import pytest

from app.engine.backtest import BacktestRequest, DateRange, ExecutionPolicy, Trade, TradeOutcome
from app.services.backtest.canonical_experiment import (
    CanonicalBacktestExperiment,
    CanonicalExperimentSpec,
    CanonicalExperimentValidationError,
    _holdout_population_fingerprint,
)
from app.services.backtest.comparison import _legacy_projection


DATES = tuple((date(2025, 1, 1) + timedelta(days=index)).isoformat() for index in range(150))


def _request(*, score: float, policy: ExecutionPolicy | None = None) -> BacktestRequest:
    return BacktestRequest(
        strategy_id="daily-stock-analysis-skill",
        date_selector=DateRange(DATES[0], DATES[-1]),
        execution_policy=policy or ExecutionPolicy(
            commission_rate=0,
            stamp_tax_rate=0,
            entry_slippage_bps=0,
            exit_slippage_bps=0,
        ),
        parameter_set={"minimum_signal_score": score},
    )


def _trade(day: str, value: float, slot: int) -> SimpleNamespace:
    trade = Trade(
        code=f"{day.replace('-', '')}_{slot}",
        signal_date=day,
        entry_date=day,
        exit_date=day,
        entry_price=10.0,
        exit_price=10.0 * (1 + value / 100),
        gross_return_pct=value,
        net_return_pct=value,
        position_size=1.0,
        holding_days=1,
        outcome_label=TradeOutcome.SUCCESS if value > 0 else TradeOutcome.FAILED,
        exit_reason="take_profit" if value > 0 else "stop_loss",
    )
    return SimpleNamespace(signal_date=day, trade_json=json.dumps(trade.canonical_payload()))


class FakeRepository:
    def __init__(self, log: list[str], *, suffix: str = "one") -> None:
        self.log = log
        self.suffix = suffix
        self.completed: dict | None = None
        self.holdout_fingerprint: str | None = None
        self._trades = {
            f"btr_champion_{suffix}": [
                _trade(day, -1.0 if index % 2 == 0 else 1.0, slot)
                for index, day in enumerate(DATES)
                for slot in range(5)
            ],
            f"btr_challenger_{suffix}": [
                _trade(day, 1.0, slot) for day in DATES for slot in range(5)
            ],
        }

    def create_experiment(self, **kwargs):
        self.log.append("create")
        return SimpleNamespace(
            experiment_id=kwargs["experiment_id"],
            snapshot_id=kwargs["snapshot_id"],
            execution_policy_fingerprint=kwargs["execution_policy_fingerprint"],
            split_hash="f" * 64,
            frozen_candidate_fingerprint=kwargs["frozen_candidate_fingerprint"],
        )

    def freeze_experiment(self, experiment_id: str, *, frozen_candidate_fingerprint: str):
        del experiment_id, frozen_candidate_fingerprint
        self.log.append("freeze")

    def claim_holdout(self, *, holdout_fingerprint: str, experiment_id: str, phase: str):
        del experiment_id, phase
        self.holdout_fingerprint = holdout_fingerprint
        self.log.append("claim_holdout")
        return SimpleNamespace(holdout_fingerprint=holdout_fingerprint)

    def complete_experiment(self, experiment_id: str, **kwargs):
        del experiment_id
        self.log.append("complete")
        self.completed = kwargs

    def list_trades(self, backtest_id: str):
        return self._trades[backtest_id]


class FakeRunner:
    def __init__(
        self,
        log: list[str],
        *,
        suffix: str = "one",
        champion_validation: bool = True,
        challenger_validation: bool = True,
    ) -> None:
        self.log = log
        self.suffix = suffix
        self.champion_validation = champion_validation
        self.challenger_validation = challenger_validation
        self.snapshot_received: str | None = None
        self.stock_metadata_temporality = "point_in_time"

    def snapshot_stock_metadata_temporality(self, snapshot_id: str) -> str:
        assert snapshot_id == "bts_shared_content_hash"
        return self.stock_metadata_temporality

    def resolve_request(self, request: BacktestRequest) -> BacktestRequest:
        return replace(request, strategy_version="strategy-v1")

    def run(self, request: BacktestRequest) -> dict:
        self.log.append("champion_run")
        return self._result("champion", request, self.champion_validation)

    def run_on_snapshot(self, request: BacktestRequest, snapshot_id: str) -> dict:
        assert "freeze" in self.log
        self.log.append("challenger_run")
        self.snapshot_received = snapshot_id
        return self._result("challenger", request, self.challenger_validation)

    def _result(self, arm: str, request: BacktestRequest, validation: bool) -> dict:
        return {
            "backtest_id": f"btr_{arm}_{self.suffix}",
            "status": "completed",
            "snapshot_id": "bts_shared_content_hash",
            "strategy_id": request.strategy_id,
            "strategy_version": request.strategy_version,
            "result": {
                "status": "completed",
                "signal_dates": list(DATES),
                "cross_validation": {"passed": validation, "absolute_difference": 0.0},
            },
        }


def _run_experiment(*, suffix: str = "one", experiment_id: str = "btexp_one") -> tuple[dict, FakeRepository, FakeRunner]:
    log: list[str] = []
    repository = FakeRepository(log, suffix=suffix)
    runner = FakeRunner(log, suffix=suffix)
    artifact = CanonicalBacktestExperiment(repository, runner).run(
        CanonicalExperimentSpec(
            experiment_id=experiment_id,
            hypothesis="降低评分线是否改善同一窗口的正式净收益",
            champion=_request(score=72),
            challenger=_request(score=65),
        )
    )
    return artifact, repository, runner


def test_experiment_freezes_plan_then_runs_both_arms_on_one_snapshot() -> None:
    artifact, repository, runner = _run_experiment()

    assert runner.snapshot_received == "bts_shared_content_hash"
    assert repository.log == [
        "champion_run",
        "create",
        "freeze",
        "claim_holdout",
        "challenger_run",
        "complete",
    ]
    assert repository.holdout_fingerprint == artifact["holdout_fingerprint"]
    assert artifact["shared_snapshot_id"] == "bts_shared_content_hash"
    assert artifact["decision"]["decision"] == "FORWARD_CANDIDATE"
    assert artifact["decision"]["historically_qualified"] is True
    assert artifact["decision"]["should_publish"] is False
    assert artifact["decision"]["production_blocker"] == "forward_canary_required"
    assert artifact["truth_validation"]["passed"] is True
    assert artifact["split"] == {
        "train": list(DATES[:90]),
        "validation": list(DATES[97:120]),
        "out_of_sample": list(DATES[127:]),
    }
    assert artifact["population_split"] == {
        "train": list(DATES[:90]),
        "validation": list(DATES[90:120]),
        "out_of_sample": list(DATES[120:]),
    }
    assert artifact["purge_signal_days"] == 7
    assert artifact["arms"]["champion"]["split_metrics"]["out_of_sample"]["executed_trade_count"] == 115
    assert repository.completed is not None
    assert repository.completed["conclusion"]["evidence_fingerprint"] == artifact["evidence_fingerprint"]
    projection = _legacy_projection(
        artifact,
        strategy_id="daily-stock-analysis-skill",
        start_date=DATES[0],
        end_date=DATES[-1],
        baseline_score_line=72,
        candidate_score_line=65,
        elapsed_ms=1,
    )
    assert projection["dates_scanned"] == len(DATES)


def test_evidence_fingerprint_excludes_attempt_and_experiment_ids() -> None:
    first, _, _ = _run_experiment(suffix="one", experiment_id="btexp_one")
    second, _, _ = _run_experiment(suffix="two", experiment_id="btexp_two")

    assert first["arms"]["champion"]["backtest_id"] != second["arms"]["champion"]["backtest_id"]
    assert first["evidence_fingerprint"] == second["evidence_fingerprint"]


def test_holdout_population_identity_does_not_change_with_strategy_id() -> None:
    first = _request(score=72)
    second = replace(first, strategy_id="another-strategy")
    kwargs = {
        "snapshot_id": "bts_shared_content_hash",
        "out_of_sample_dates": DATES[127:],
        "stock_metadata_temporality": "point_in_time",
    }

    assert _holdout_population_fingerprint(request=first, **kwargs) == (
        _holdout_population_fingerprint(request=second, **kwargs)
    )


def test_vectorbt_failure_invalidates_evidence_before_publish_decision(monkeypatch) -> None:
    log: list[str] = []
    repository = FakeRepository(log)
    runner = FakeRunner(log, challenger_validation=False)

    def unexpected_decision(*args, **kwargs):
        raise AssertionError("production decision must not run on invalid evidence")

    monkeypatch.setattr(
        "app.services.backtest.canonical_experiment.evaluate_challenger",
        unexpected_decision,
    )
    artifact = CanonicalBacktestExperiment(repository, runner).run(
        CanonicalExperimentSpec(
            experiment_id="btexp_invalid",
            hypothesis="校验失败必须阻止发布判定",
            champion=_request(score=72),
            challenger=_request(score=65),
        )
    )

    assert artifact["status"] == "failed"
    assert artifact["decision"] == {
        "decision": "INVALID_EVIDENCE",
        "should_publish": False,
        "improvements": [],
        "regressions": [],
        "deltas": {},
    }
    assert repository.completed["status"] == "failed"


def test_full_window_improvement_cannot_override_losing_oos() -> None:
    log: list[str] = []
    repository = FakeRepository(log)
    repository._trades["btr_challenger_one"] = [
        _trade(day, 2.0 if index < 127 else -2.0, slot)
        for index, day in enumerate(DATES)
        for slot in range(5)
    ]
    artifact = CanonicalBacktestExperiment(repository, FakeRunner(log)).run(
        CanonicalExperimentSpec(
            experiment_id="btexp_losing_oos",
            hypothesis="全窗改善不能掩盖 OOS 亏损",
            champion=_request(score=72),
            challenger=_request(score=65),
        )
    )

    assert artifact["arms"]["challenger"]["metrics"]["average_net_return_pct"] > 0
    assert (
        artifact["arms"]["challenger"]["split_metrics"]["out_of_sample"][
            "average_net_return_pct"
        ]
        < 0
    )
    assert artifact["decision"]["decision_scope"] == "out_of_sample"
    assert artifact["decision"]["historically_qualified"] is False
    assert artifact["decision"]["should_publish"] is False


def test_oos_sample_gate_requires_one_hundred_executed_trades() -> None:
    log: list[str] = []
    repository = FakeRepository(log)
    repository._trades["btr_challenger_one"] = [
        _trade(day, 1.0, 0) for day in DATES
    ]
    artifact = CanonicalBacktestExperiment(repository, FakeRunner(log)).run(
        CanonicalExperimentSpec(
            experiment_id="btexp_small_oos",
            hypothesis="稀疏 OOS 不得晋级",
            champion=_request(score=72),
            challenger=_request(score=65),
        )
    )

    assert artifact["decision"]["gates"]["minimum_oos_executed_trades"] is False
    assert "minimum_oos_executed_trades" in artifact["decision"]["failed_gates"]
    assert artifact["decision"]["historically_qualified"] is False


def test_current_static_stock_metadata_blocks_canonical_historical_qualification() -> None:
    log: list[str] = []
    repository = FakeRepository(log)
    runner = FakeRunner(log)
    runner.stock_metadata_temporality = "current_static"

    artifact = CanonicalBacktestExperiment(repository, runner).run(
        CanonicalExperimentSpec(
            experiment_id="btexp_current_static",
            hypothesis="非时点化股票元数据不得授予历史合格",
            champion=_request(score=72),
            challenger=_request(score=65),
        )
    )

    assert artifact["decision"]["historically_qualified"] is False
    assert artifact["decision"]["gates"]["point_in_time_stock_metadata"] is False
    assert artifact["decision"]["production_blocker"] == "point_in_time_stock_metadata_required"


def test_experiment_rejects_execution_policy_drift_before_running() -> None:
    log: list[str] = []
    repository = FakeRepository(log)
    runner = FakeRunner(log)
    different_policy = ExecutionPolicy(
        horizon_days=7,
        commission_rate=0,
        stamp_tax_rate=0,
        entry_slippage_bps=0,
        exit_slippage_bps=0,
    )

    with pytest.raises(CanonicalExperimentValidationError, match="execution_policy"):
        CanonicalBacktestExperiment(repository, runner).run(
            CanonicalExperimentSpec(
                experiment_id="btexp_drift",
                hypothesis="不同成交口径不可比较",
                champion=_request(score=72),
                challenger=_request(score=65, policy=different_policy),
            )
        )

    assert log == []
