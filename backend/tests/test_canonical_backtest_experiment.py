from __future__ import annotations

from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from app.engine.backtest import BacktestRequest, DateRange, ExecutionPolicy, Trade, TradeOutcome
from app.services.backtest.canonical_experiment import (
    CanonicalBacktestExperiment,
    CanonicalExperimentSpec,
    CanonicalExperimentValidationError,
)


DATES = tuple(f"2026-01-{day:02d}" for day in range(1, 7))


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


def _trade(day: str, value: float) -> SimpleNamespace:
    trade = Trade(
        code=f"{int(day[-2:]):06d}",
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
        self._trades = {
            f"btr_champion_{suffix}": [_trade(day, value) for day, value in zip(DATES, (-1, 1, -1, 1, -1, 1))],
            f"btr_challenger_{suffix}": [_trade(day, 1.0) for day in DATES],
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
    assert repository.log == ["champion_run", "create", "freeze", "challenger_run", "complete"]
    assert artifact["shared_snapshot_id"] == "bts_shared_content_hash"
    assert artifact["decision"]["decision"] == "PRODUCTION"
    assert artifact["decision"]["should_publish"] is True
    assert artifact["truth_validation"]["passed"] is True
    assert artifact["split"] == {
        "train": list(DATES[:3]),
        "validation": list(DATES[3:4]),
        "out_of_sample": list(DATES[4:]),
    }
    assert artifact["arms"]["champion"]["split_metrics"]["out_of_sample"]["executed_trade_count"] == 2
    assert repository.completed is not None
    assert repository.completed["conclusion"]["evidence_fingerprint"] == artifact["evidence_fingerprint"]


def test_evidence_fingerprint_excludes_attempt_and_experiment_ids() -> None:
    first, _, _ = _run_experiment(suffix="one", experiment_id="btexp_one")
    second, _, _ = _run_experiment(suffix="two", experiment_id="btexp_two")

    assert first["arms"]["champion"]["backtest_id"] != second["arms"]["champion"]["backtest_id"]
    assert first["evidence_fingerprint"] == second["evidence_fingerprint"]


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
