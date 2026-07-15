"""Production assembly for the recommendation-pipeline RunCoordinator.

Each stage is constructed with its own explicit Ports/repositories; the coordinator
receives only their protocol.  This module is the single composition root so that
stage wiring stays in one auditable location while individual stage implementations
evolve independently.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from .contracts import StageData
from .adapters import (
    ProductionAuxiliaryApplication,
    ProductionCostRecording,
    ProductionExecutionVersion,
    ProductionFeatureBuild,
    ProductionMarketPersistence,
    ProductionMarketScope,
    ProductionMarketUniverse,
    ProductionRecommendationAssembly,
    ProductionRecommendationPersistence,
    ProductionReviewExecutor,
    ProductionRunStart,
    ProductionSkillResolution,
    ProductionStrategyEvaluation,
)
from .prepare import PrepareStage
from .market_data import MarketDataStage
from .auxiliary_data import AuxiliaryDataStage
from .features import FeaturesStage
from .strategy_evaluation import StrategyEvaluationStage
from .review import ReviewStage
from .aggregation import AggregationStage
from .persistence import PersistenceStage
from ..coordinator import AnyStage, RunCoordinator, RunCoordinatorDependencies


def assemble_pipeline_stages(db: Session, *, run_id: str) -> tuple[AnyStage, ...]:
    """Build the ordered tuple of recommendation-pipeline stages with production adapters."""
    return (
        PrepareStage(
            skill_resolution=ProductionSkillResolution(db),
            run_start=ProductionRunStart(db),
            execution_version=ProductionExecutionVersion(),
        ),
        MarketDataStage(
            market_universe=ProductionMarketUniverse(db),
            market_persistence=ProductionMarketPersistence(db),
            market_scope=ProductionMarketScope(db),
        ),
        AuxiliaryDataStage(
            auxiliary_application=ProductionAuxiliaryApplication(db),
        ),
        FeaturesStage(
            feature_build=ProductionFeatureBuild(db),
        ),
        StrategyEvaluationStage(
            strategy_evaluation=ProductionStrategyEvaluation(db),
        ),
        ReviewStage(
            review_executor=ProductionReviewExecutor(),
            cost_recording=ProductionCostRecording(db, run_id),
        ),
        AggregationStage(
            recommendation_assembly=ProductionRecommendationAssembly(db),
        ),
        PersistenceStage(
            recommendation_persistence=ProductionRecommendationPersistence(db),
        ),
    )


def assemble_run_pipeline_coordinator(
    dependencies: RunCoordinatorDependencies,
) -> RunCoordinator:
    """Build the production recommendation coordinator with all migrated stages."""
    return RunCoordinator.from_dependencies(dependencies)


__all__ = [
    "assemble_pipeline_stages",
    "assemble_run_pipeline_coordinator",
]
