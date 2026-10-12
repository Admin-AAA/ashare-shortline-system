"""Real HTTP vertical slice for the isolated P0 Agent Research Gateway."""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import backtests
from app.db.session import Base
from app.models.tables import (
    BacktestCandidate,
    BacktestDataSnapshot,
    BacktestExperiment,
    BacktestFailureAttribution,
    BacktestInputArtifact,
    BacktestRequestClaim,
    BacktestRunV2,
    BacktestTrade,
    DailyKbar,
    Stock,
)
from app.services.backtest.research_repository import (
    BacktestStateConflict,
    ResearchBacktestRepository,
)


STRATEGY_ID = "chao-gu-yang-jia-skill"


@pytest.fixture
def gateway():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            Stock.__table__,
            DailyKbar.__table__,
            BacktestDataSnapshot.__table__,
            BacktestInputArtifact.__table__,
            BacktestRequestClaim.__table__,
            BacktestRunV2.__table__,
            BacktestCandidate.__table__,
            BacktestTrade.__table__,
            BacktestFailureAttribution.__table__,
            BacktestExperiment.__table__,
        ],
    )
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    session = SessionLocal()
    try:
        start = date(2026, 1, 1)
        session.add(
            Stock(
                code="000001",
                name="垂直切片样本",
                industry="测试行业",
                area="测试地区",
                market="主板",
                list_date="2010-01-01",
                is_st=False,
            )
        )
        session.add_all(
            [
                DailyKbar(
                    code="000001",
                    trade_date=(start + timedelta(days=index)).isoformat(),
                    open=100,
                    high=101,
                    low=95 if index in {62, 63, 64} else 99,
                    close=100,
                    volume=1_000_000,
                    amount=500_000_000,
                    pct_chg=1.0,
                    turnover_rate=5.0,
                    macd_dif=1.0,
                    macd_dea=0.0,
                    rsi_12=55.0,
                )
                for index in range(70)
            ]
        )
        session.commit()
    finally:
        session.close()

    app = FastAPI()
    app.include_router(backtests.router)

    def get_repository():
        db = SessionLocal()
        try:
            yield ResearchBacktestRepository(db)
        finally:
            db.close()

    app.dependency_overrides[backtests.get_research_repository] = get_repository
    try:
        with TestClient(app) as client:
            yield client, SessionLocal
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _range_payload() -> dict[str, object]:
    return {
        "strategy_id": STRATEGY_ID,
        "start_date": "2026-03-01",
        "end_date": "2026-03-03",
        "target_codes": ["000001"],
        "parameter_set": {"minimum_signal_score": 0.0},
        "execution_policy": {
            "entry_lookahead_days": 1,
            "horizon_days": 3,
            "stop_loss_pct": 5.0,
            "take_profit_pct": 10.0,
        },
    }


def test_main_composes_the_agent_research_gateway() -> None:
    from app.main import app

    operations = app.openapi()["paths"]
    assert "post" in operations["/api/backtests"]
    assert "post" in operations["/api/backtests/preflight"]
    assert "post" in operations["/api/backtest-experiments"]


def test_real_declarative_strategy_gateway_vertical_slice_is_research_only(gateway) -> None:
    client, SessionLocal = gateway
    payload = _range_payload()

    preflight = client.post("/api/backtests/preflight", json=payload)
    assert preflight.status_code == 200, preflight.text
    assert preflight.json()["status"] == "ready"
    assert preflight.json()["strategy_id"] == STRATEGY_ID
    assert preflight.json()["local_only"] is True

    created = client.post("/api/backtests", json=payload)
    assert created.status_code == 200, created.text
    created_body = created.json()
    assert created_body["status"] == "completed"
    assert created_body["reused"] is False
    backtest_id = created_body["backtest_id"]

    detail = client.get(f"/api/backtests/{backtest_id}")
    candidates = client.get(f"/api/backtests/{backtest_id}/candidates")
    trades = client.get(f"/api/backtests/{backtest_id}/trades")
    attributions = client.get(f"/api/backtests/{backtest_id}/attributions")
    assert detail.status_code == candidates.status_code == trades.status_code == attributions.status_code == 200
    assert candidates.json()["items"]
    assert trades.json()["items"]
        # v5（act 0.8%）后部分亏损单的出场原因从 stop_loss_hit 变为 trailing_stop_gap，
    # 而归因映射对"reason 含 gap"的亏损单一律标 chase_high（app/engine/simulation.py::attribute_failure）。
    assert {item["reason_code"] for item in attributions.json()["items"]} == {"stop_loss", "chase_high"}

    replay = client.post(f"/api/backtests/{backtest_id}/replay", json={})
    assert replay.status_code == 200, replay.text
    assert replay.json()["reused"] is False
    assert replay.json()["attempt_no"] == 2

    experiment = client.post(
        "/api/backtest-experiments",
        json={
            "hypothesis": "Verify a frozen real-strategy baseline through the research gateway.",
            "baseline_backtest_id": backtest_id,
            "candidate_strategy_id": STRATEGY_ID,
            "candidate_strategy_version": detail.json()["strategy_version"],
            "frozen_candidate_fingerprint": "c" * 64,
            "splits": {
                "train": ["2026-03-01"],
                "validation": ["2026-03-02"],
                "out_of_sample": ["2026-03-03"],
            },
            "acceptance": {
                "min_trade_count": 1,
                "min_win_rate_pct": 0.0,
                "min_profit_loss_ratio": 0.0,
                "min_average_net_return_pct": -100.0,
                "max_drawdown_pct": 100.0,
            },
        },
    )
    assert experiment.status_code == 200, experiment.text
    experiment_id = experiment.json()["experiment_id"]
    assert client.get(f"/api/backtest-experiments/{experiment_id}").status_code == 200

    session = SessionLocal()
    try:
        assert session.query(BacktestRunV2).count() == 2
        assert session.query(BacktestCandidate).count() >= 3
        assert session.query(BacktestTrade).count() >= 3
        assert session.query(BacktestFailureAttribution).count() >= 3
        assert session.query(BacktestExperiment).count() == 1
        completed = session.query(BacktestRunV2).first()
        assert completed is not None
        with pytest.raises(BacktestStateConflict, match="终态 attempt"):
            ResearchBacktestRepository(session).persist_trades(
                completed.backtest_id,
                [],
            )
    finally:
        session.close()
