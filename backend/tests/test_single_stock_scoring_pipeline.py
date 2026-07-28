"""单股分析必须执行正式推荐管线的同一组评分阶段。"""
from __future__ import annotations

import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.runs.stages.adapters import (
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
from app.services.runs.stages.strategy_evaluation import StrategyEvaluationStage
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
