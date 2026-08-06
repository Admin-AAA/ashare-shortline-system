"""单股分析必须执行正式推荐管线的同一组评分阶段。"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.runs.stages.adapters import (
    ProductionAuxiliaryApplication,
    ProductionFeatureBuild,
    ProductionMarketScope,
    ProductionRecommendationAssembly,
    ProductionReviewExecutor,
    ProductionStrategyEvaluation,
)
from app.services.runs.stages.aggregation import AggregationStage
from app.services.runs.stages.assembly import assemble_pipeline_stages
from app.services.runs.stages.auxiliary_data import AuxiliaryDataStage
from app.services.runs.stages.features import FeaturesStage
from app.services.runs.stages.market_data import MarketDataStage
from app.services.runs.stages.prepare import PrepareStage
from app.services.runs.stages.review import ReviewStage
from app.services.runs.coordinator import RunContext, WorkerLease
from app.services.runs.contracts import parse_run_request
from app.services.runs.stages.contracts import StageData
from app.services.runs.stages.strategy_evaluation import StrategyEvaluationStage
from app.models.tables import HolderNumber, PledgeStat
from app.services.single_stock_analysis import _single_stock_scoring_stages


def test_single_stock_uses_official_recommendation_scoring_stages():
    """除持久化外，单股分析与正式推荐必须由相同 Stage/Adapter 实现。"""
    stages = _single_stock_scoring_stages(object(), code="000001", run_id="single-test")
    formal_stages = assemble_pipeline_stages(object(), run_id="formal-test")

    assert [type(stage) for stage in stages] == [type(stage) for stage in formal_stages[:-1]]
    assert [type(stage) for stage in stages] == [
        PrepareStage,
        MarketDataStage,
        AuxiliaryDataStage,
        FeaturesStage,
        StrategyEvaluationStage,
        ReviewStage,
        AggregationStage,
    ]
    assert isinstance(stages[1].market_scope, ProductionMarketScope)
    assert isinstance(stages[3].feature_build, ProductionFeatureBuild)
    assert isinstance(stages[4].strategy_evaluation, ProductionStrategyEvaluation)
    assert isinstance(stages[5].review_executor, ProductionReviewExecutor)
    assert isinstance(stages[6].recommendation_assembly, ProductionRecommendationAssembly)
    assert isinstance(stages[2].auxiliary_application, ProductionAuxiliaryApplication)


def _context() -> RunContext:
    return RunContext(
        run_id="replay-auxiliary-test",
        request=parse_run_request({"run_mode": "formal"}),
        worker_lease=WorkerLease("replay-auxiliary-test", "lease", 1),
    )


def test_replay_stages_reuse_as_of_bounded_auxiliary_views():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine("sqlite:///:memory:")
    PledgeStat.__table__.create(engine)
    HolderNumber.__table__.create(engine)
    db = Session(engine)
    db.add_all(
        [
            PledgeStat(
                code="000001",
                end_date="2026-03-31",
                pledge_count=3,
                unrest_pledge=100.0,
                rest_pledge=20.0,
                pledge_ratio=8.0,
            ),
            PledgeStat(
                code="000001",
                end_date="2026-06-30",
                pledge_count=9,
                unrest_pledge=300.0,
                rest_pledge=40.0,
                pledge_ratio=30.0,
            ),
            HolderNumber(code="000001", end_date="2025-12-31", holder_num=1200),
            HolderNumber(code="000001", end_date="2026-03-31", holder_num=1000),
            HolderNumber(code="000001", end_date="2026-06-30", holder_num=600),
        ]
    )
    db.flush()
    calls = {}
    original_run_application = ProductionAuxiliaryApplication(db).run_application

    def run_application(**kwargs):
        calls["auxiliary"] = kwargs
        return original_run_application(**kwargs)

    class FeatureBuild:
        def build(self, stocks, snapshot, *, auxiliary_views=None):
            calls["feature_views"] = auxiliary_views
            return tuple(stocks), ()

    data = StageData(
        params={"trade_date_override": "2026-03-31", "skip_aux_refresh": True},
        snapshot={"trade_date": "2026-03-31"},
        prefiltered_stocks=({"code": "000001"},),
    )

    try:
        auxiliary_output = AuxiliaryDataStage(
            auxiliary_application=SimpleNamespace(run_application=run_application),
        ).execute(_context(), data)
        FeaturesStage(feature_build=FeatureBuild()).execute(_context(), auxiliary_output.data)

        assert calls["auxiliary"] == {
            "instruments": ["000001"],
            "trade_date": "2026-03-31",
            "refresh": False,
            "as_of_date": "2026-03-31",
        }
        feature_views = auxiliary_output.data.auxiliary_result.feature_views
        assert calls["feature_views"] is feature_views
        assert feature_views["pledges"]["000001"]["pledge_ratio"] == 8.0
        assert feature_views["holder_numbers"]["000001"]["holder_num"] == 1000
        assert feature_views["holder_numbers"]["000001"]["holder_num_prev"] == 1200
    finally:
        db.close()
