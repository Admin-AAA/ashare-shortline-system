from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.engine.backtest import BacktestRequest, DateRange, ExecutionPolicy
from app.models.tables import (
    BacktestCandidate,
    BacktestDataSnapshot,
    BacktestExperiment,
    BacktestFailureAttribution,
    BacktestHoldoutClaim,
    BacktestInputArtifact,
    BacktestRequestClaim,
    BacktestRunV2,
    BacktestTrade,
    DailyKbar,
    Stock,
)
from app.services.backtest.canonical_experiment import (
    CanonicalBacktestExperiment,
    CanonicalExperimentSpec,
)
from app.services.backtest.research_repository import ResearchBacktestRepository
from app.services.backtest.research_runner import ResearchBacktestRunner


def test_real_runner_persists_one_shared_snapshot_and_one_evidence_artifact() -> None:
    engine = create_engine("sqlite:///:memory:")
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
            BacktestHoldoutClaim.__table__,
            BacktestExperiment.__table__,
        ],
    )
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        session.add(
            Stock(
                code="000001",
                name="Canonical 实验样本",
                industry="测试行业",
                area="测试地区",
                market="主板",
                list_date="2010-01-01",
                is_st=False,
            )
        )
        start = date(2026, 1, 1)
        session.add_all(
            DailyKbar(
                code="000001",
                trade_date=(start + timedelta(days=index)).isoformat(),
                open=100,
                high=102,
                low=99,
                close=101,
                volume=1_000_000,
                amount=500_000_000,
                pct_chg=1.0,
                turnover_rate=5.0,
                macd_dif=1.0,
                macd_dea=0.0,
                rsi_12=55.0,
            )
            for index in range(180)
        )
        session.commit()

        repository = ResearchBacktestRepository(session)
        policy = ExecutionPolicy(
            entry_lookahead_days=1,
            horizon_days=3,
            commission_rate=0,
            stamp_tax_rate=0,
            entry_slippage_bps=0,
            exit_slippage_bps=0,
        )
        common = {
            "strategy_id": "chao-gu-yang-jia-skill",
            "date_selector": DateRange("2026-03-01", "2026-04-30"),
            "execution_policy": policy,
            "target_codes": ("000001",),
        }
        artifact = CanonicalBacktestExperiment(
            repository,
            ResearchBacktestRunner(repository),
        ).run(
            CanonicalExperimentSpec(
                experiment_id="btexp_real_vertical_slice",
                hypothesis="真实 runner 双臂必须共享不可变快照",
                champion=BacktestRequest(
                    **common,
                    parameter_set={"minimum_signal_score": 0.0},
                ),
                challenger=BacktestRequest(
                    **common,
                    parameter_set={"minimum_signal_score": 10.0},
                ),
            )
        )

        runs = session.query(BacktestRunV2).all()
        experiment = session.get(BacktestExperiment, "btexp_real_vertical_slice")
        assert len(runs) == 2
        assert len({run.snapshot_id for run in runs}) == 1
        assert session.query(BacktestDataSnapshot).count() == 1
        assert artifact["shared_snapshot_id"] == runs[0].snapshot_id
        assert artifact["truth_validation"]["passed"] is True
        assert artifact["stock_metadata_temporality"] == "current_static"
        assert artifact["decision"]["gates"]["point_in_time_stock_metadata"] is False
        assert artifact["decision"]["historically_qualified"] is False
        assert experiment is not None
        assert artifact["evidence_fingerprint"] in str(experiment.conclusion_json)
    finally:
        session.close()
        engine.dispose()
