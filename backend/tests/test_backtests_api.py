"""Route-level contract tests for the unregistered Agent Research Gateway.

The application does not register this router until the P0 runner is composed in
main.py.  These tests build a tiny FastAPI application and override both public
Gateway dependencies, so they do not initialise a real database or call any
network-backed market-data provider.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import backtests
from app.engine.backtest.contracts import BacktestDomainError
from app.services.backtest.research_repository import (
    ImmutableRecordConflict,
    ResearchRecordNotFound,
)


class FakeRunner:
    def __init__(self) -> None:
        self.preflight_requests: list[Any] = []
        self.run_requests: list[Any] = []
        self.replay_requests: list[tuple[str, bool]] = []
        self.error: Exception | None = None

    def preflight(self, request: Any) -> dict[str, Any]:
        if self.error is not None:
            raise self.error
        self.preflight_requests.append(request)
        return {
            "status": "ready",
            "strategy_id": request.strategy_id,
            "signal_date": request.date_selector.start_date,
            "snapshot_id": "snapshot_preflight_1",
        }

    def run(self, request: Any) -> dict[str, Any]:
        if self.error is not None:
            raise self.error
        self.run_requests.append(request)
        return {
            "backtest_id": "bt_created_1",
            "attempt_no": 1,
            "reused": False,
            "status": "completed",
            "strategy_id": request.strategy_id,
            "snapshot_id": "snapshot_1",
            "diagnostic_scope": request.diagnostic_scope,
        }

    def replay(self, backtest_id: str, *, force_rerun: bool = True) -> dict[str, Any]:
        if self.error is not None:
            raise self.error
        self.replay_requests.append((backtest_id, force_rerun))
        return {
            "backtest_id": "bt_replay_2",
            "parent_backtest_id": backtest_id,
            "attempt_no": 2,
            "reused": False,
            "status": "completed",
            "snapshot_id": "snapshot_1",
        }


class FakeRepository:
    def __init__(self) -> None:
        self.error: Exception | None = None
        self.created_experiment: dict[str, Any] | None = None
        self.baseline = SimpleNamespace(
            backtest_id="bt_baseline_1",
            snapshot_id="snapshot_1",
            execution_policy_fingerprint="a" * 64,
            strategy_id="baseline-strategy",
            status="completed",
        )

    def _raise_if_configured(self) -> None:
        if self.error is not None:
            raise self.error

    def get_run(self, backtest_id: str) -> Any:
        self._raise_if_configured()
        if backtest_id == "bt_baseline_1":
            return self.baseline
        return SimpleNamespace(
            backtest_id=backtest_id,
            attempt_no=1,
            status="completed",
            snapshot_id="snapshot_1",
            strategy_id="strategy-alpha",
            result_json='{"metrics":{"trade_count":1}}',
        )

    def list_candidates(self, backtest_id: str) -> list[Any]:
        self._raise_if_configured()
        return [
            SimpleNamespace(
                candidate_id="candidate_1",
                backtest_id=backtest_id,
                signal_date="2026-05-20",
                code="000001",
                decision_status="signal",
                audit_json='{"filter":"passed"}',
            )
        ]

    def list_trades(self, backtest_id: str) -> list[Any]:
        self._raise_if_configured()
        return [
            SimpleNamespace(
                trade_id="trade_1",
                backtest_id=backtest_id,
                signal_date="2026-05-20",
                code="000001",
                outcome_label="failed",
                trade_json='{"net_return_pct":-2.5}',
            )
        ]

    def list_attributions(self, backtest_id: str, *, reason_code: str | None = None) -> list[Any]:
        self._raise_if_configured()
        return [
            SimpleNamespace(
                attribution_id="attr_1",
                backtest_id=backtest_id,
                trade_id="trade_1",
                reason_code=reason_code or "stop_loss",
                confidence=0.9,
                is_unknown=False,
            )
        ]

    def aggregate_attributions(self, backtest_id: str) -> list[dict[str, Any]]:
        self._raise_if_configured()
        return [
            {
                "reason_code": "stop_loss",
                "count": 1,
                "ratio": 1.0,
                "net_return": -2.5,
                "evidence_references": ["artifact_1"],
            }
        ]

    def create_experiment(self, **kwargs: Any) -> Any:
        self._raise_if_configured()
        self.created_experiment = kwargs
        return SimpleNamespace(
            experiment_id=kwargs["experiment_id"],
            status="draft",
            baseline_backtest_id=kwargs["baseline_backtest_id"],
            snapshot_id=kwargs["snapshot_id"],
            split_json=kwargs["split_json"],
        )

    def get_experiment(self, experiment_id: str) -> Any:
        self._raise_if_configured()
        return SimpleNamespace(
            experiment_id=experiment_id,
            status="frozen",
            conclusion_json='{"promotion":"exploratory"}',
        )


@pytest.fixture
def gateway() -> tuple[TestClient, FakeRunner, FakeRepository]:
    runner = FakeRunner()
    repository = FakeRepository()
    app = FastAPI()
    app.include_router(backtests.router)
    app.dependency_overrides[backtests.get_research_backtest_runner] = lambda: runner
    app.dependency_overrides[backtests.get_research_repository] = lambda: repository
    with TestClient(app) as client:
        yield client, runner, repository


def _backtest_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "strategy_id": "strategy-alpha",
        "signal_date": "2026-05-20",
        "execution_policy": {
            "horizon_days": 5,
            "commission_rate": 0.0001,
            "stamp_tax_rate": 0.0005,
        },
    }
    payload.update(overrides)
    return payload


def _experiment_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "hypothesis": "A tighter entry gap lowers drawdown without erasing return.",
        "baseline_backtest_id": "bt_baseline_1",
        "candidate_strategy_id": "candidate-strategy",
        "candidate_strategy_version": "manifest-2026-05-20",
        "frozen_candidate_fingerprint": "b" * 64,
        "splits": {
            "train": ["2026-01-02", "2026-01-05"],
            "validation": ["2026-02-02"],
            "out_of_sample": ["2026-03-02", "2026-03-03"],
        },
        "acceptance": {
            "min_trade_count": 20,
            "min_win_rate_pct": 55.0,
            "min_profit_loss_ratio": 1.2,
            "min_average_net_return_pct": 0.1,
            "max_drawdown_pct": 10.0,
        },
    }
    payload.update(overrides)
    return payload


def test_preflight_and_create_use_typed_single_strategy_domain_request(gateway):
    client, runner, _ = gateway

    preflight = client.post("/api/backtests/preflight", json=_backtest_payload())
    created = client.post(
        "/api/backtests",
        json=_backtest_payload(
            start_date=None,
            signal_date=None,
            end_date="2026-05-23",
            target_codes=["000001", "600000"],
            force_rerun=True,
        )
        | {"start_date": "2026-05-20"},
    )

    assert preflight.status_code == 200
    assert preflight.json()["snapshot_id"] == "snapshot_preflight_1"
    assert len(runner.preflight_requests) == 1
    assert runner.preflight_requests[0].strategy_id == "strategy-alpha"
    assert runner.preflight_requests[0].date_selector.signal_date == "2026-05-20"
    assert runner.preflight_requests[0].force_rerun is False

    assert created.status_code == 200
    assert created.json()["backtest_id"] == "bt_created_1"
    assert created.json()["diagnostic_scope"] is True
    assert len(runner.run_requests) == 1
    request = runner.run_requests[0]
    assert request.date_selector.start_date == "2026-05-20"
    assert request.date_selector.end_date == "2026-05-23"
    assert request.target_codes == ("000001", "600000")
    assert request.force_rerun is True


@pytest.mark.parametrize(
    "payload",
    [
        {},
        _backtest_payload(signal_date="2026-05-20", start_date="2026-05-01", end_date="2026-05-20"),
        _backtest_payload(signal_date=None, start_date="2026-05-01"),
        _backtest_payload(strategy_id="strategy-a,strategy-b"),
        _backtest_payload(force_rerun=1),
        _backtest_payload(target_codes=["000001", "000001"]),
        _backtest_payload(extra_parameter_override={"unsafe": True}),
    ],
)
def test_backtest_request_rejects_ambiguous_or_unbounded_input(gateway, payload):
    client, runner, _ = gateway

    response = client.post("/api/backtests", json=payload)

    assert response.status_code == 422
    assert runner.run_requests == []


def test_preflight_rejects_force_rerun_and_replay_only_allows_forced_snapshot_reuse(gateway):
    client, runner, _ = gateway

    preflight = client.post("/api/backtests/preflight", json=_backtest_payload(force_rerun=True))
    replay = client.post("/api/backtests/bt_source_1/replay", json={})
    replay_override = client.post(
        "/api/backtests/bt_source_1/replay",
        json={"snapshot_id": "different-snapshot"},
    )

    assert preflight.status_code == 422
    assert replay.status_code == 200
    assert replay.json()["parent_backtest_id"] == "bt_source_1"
    assert runner.replay_requests == [("bt_source_1", True)]
    assert replay_override.status_code == 422


def test_backtest_read_routes_project_isolated_repository_records(gateway):
    client, _, _ = gateway

    detail = client.get("/api/backtests/bt_result_1")
    candidates = client.get("/api/backtests/bt_result_1/candidates")
    trades = client.get("/api/backtests/bt_result_1/trades")
    attributions = client.get("/api/backtests/bt_result_1/attributions?reason_code=stop_loss")

    assert detail.status_code == 200
    assert detail.json()["result"] == {"metrics": {"trade_count": 1}}
    assert candidates.json()["items"][0]["audit"] == {"filter": "passed"}
    assert trades.json()["items"][0]["trade"] == {"net_return_pct": -2.5}
    assert attributions.json()["items"][0]["reason_code"] == "stop_loss"
    assert attributions.json()["aggregates"][0]["ratio"] == 1.0


def test_experiment_routes_freeze_baseline_identity_and_expose_detail(gateway):
    client, _, repository = gateway

    created = client.post("/api/backtest-experiments", json=_experiment_payload())
    detail = client.get("/api/backtest-experiments/btexp_existing_1")

    assert created.status_code == 200
    assert created.json()["status"] == "draft"
    assert repository.created_experiment is not None
    assert repository.created_experiment["snapshot_id"] == "snapshot_1"
    assert repository.created_experiment["execution_policy_fingerprint"] == "a" * 64
    assert repository.created_experiment["split_hash"] is None
    assert repository.created_experiment["split_json"]["out_of_sample"] == ["2026-03-02", "2026-03-03"]
    assert detail.status_code == 200
    assert detail.json()["conclusion"] == {"promotion": "exploratory"}


@pytest.mark.parametrize(
    "payload",
    [
        _experiment_payload(
            splits={
                "train": ["2026-01-02"],
                "validation": ["2026-01-02"],
                "out_of_sample": ["2026-03-02"],
            }
        ),
        _experiment_payload(
            splits={
                "train": ["2026-02-02"],
                "validation": ["2026-01-02"],
                "out_of_sample": ["2026-03-02"],
            }
        ),
        _experiment_payload(snapshot_id="client-cannot-override-baseline"),
    ],
)
def test_experiment_request_rejects_overlapping_or_client_selected_identity(gateway, payload):
    client, _, repository = gateway

    response = client.post("/api/backtest-experiments", json=payload)

    assert response.status_code == 422
    assert repository.created_experiment is None


def test_known_domain_errors_have_stable_http_status_and_code(gateway):
    client, runner, repository = gateway

    repository.error = ResearchRecordNotFound("missing attempt", details={"backtest_id": "bt_missing"})
    not_found = client.get("/api/backtests/bt_missing")
    assert not_found.status_code == 404
    assert not_found.json()["detail"]["code"] == "research_record_not_found"

    repository.error = None
    runner.error = ImmutableRecordConflict("request already claimed")
    conflict = client.post("/api/backtests", json=_backtest_payload())
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "research_immutable_record_conflict"

    runner.error = BacktestDomainError("invalid_backtest_request", "invalid strategy parameter")
    invalid = client.post("/api/backtests", json=_backtest_payload())
    assert invalid.status_code == 400
    assert invalid.json()["detail"]["code"] == "invalid_backtest_request"


def test_uninstalled_runner_returns_a_stable_dependency_error_without_database_access(monkeypatch):
    """Composition still exposes a stable 503 if the concrete runner is absent."""
    monkeypatch.setattr(backtests, "_RUNNER_IMPORT_CANDIDATES", ())
    app = FastAPI()
    app.include_router(backtests.router)
    app.dependency_overrides[backtests.get_research_repository] = FakeRepository
    with TestClient(app) as client:
        response = client.post("/api/backtests", json=_backtest_payload())

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "research_backtest_runner_unavailable"
