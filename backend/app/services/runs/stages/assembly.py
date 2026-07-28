"""Production assembly for the recommendation-pipeline RunCoordinator.

Each stage is constructed with its own explicit Ports/repositories; the coordinator
receives only their protocol.  This module is the single composition root so that
stage wiring stays in one auditable location while individual stage implementations
evolve independently.
"""
from __future__ import annotations

from typing import Any

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


def assemble_scoring_pipeline_stages(
    *,
    skill_resolution: Any,
    execution_version: Any,
    market_universe: Any,
    market_scope: Any,
    auxiliary_application: Any,
    feature_build: Any,
    strategy_evaluation: Any,
    review_executor: Any,
    cost_recording: Any,
    recommendation_assembly: Any,
    run_start: Any | None = None,
    market_persistence: Any | None = None,
) -> tuple[AnyStage, ...]:
    """Build the canonical scoring stages shared by formal and single-stock runs."""
    return (
        PrepareStage(
            skill_resolution=skill_resolution,
            run_start=run_start,
            execution_version=execution_version,
        ),
        MarketDataStage(
            market_universe=market_universe,
            market_persistence=market_persistence,
            market_scope=market_scope,
        ),
        AuxiliaryDataStage(
            auxiliary_application=auxiliary_application,
        ),
        FeaturesStage(
            feature_build=feature_build,
        ),
        StrategyEvaluationStage(
            strategy_evaluation=strategy_evaluation,
        ),
        ReviewStage(
            review_executor=review_executor,
            cost_recording=cost_recording,
        ),
        AggregationStage(
            recommendation_assembly=recommendation_assembly,
        ),
    )


def assemble_pipeline_stages(db: Session, *, run_id: str) -> tuple[AnyStage, ...]:
    """Build the ordered tuple of recommendation-pipeline stages with production adapters."""
    scoring_stages = assemble_scoring_pipeline_stages(
        skill_resolution=ProductionSkillResolution(db),
        run_start=ProductionRunStart(db),
        execution_version=ProductionExecutionVersion(),
        market_universe=ProductionMarketUniverse(db),
        market_persistence=ProductionMarketPersistence(db, run_id),
        market_scope=ProductionMarketScope(db),
        auxiliary_application=ProductionAuxiliaryApplication(db),
        feature_build=ProductionFeatureBuild(db),
        strategy_evaluation=ProductionStrategyEvaluation(db),
        review_executor=ProductionReviewExecutor(),
        cost_recording=ProductionCostRecording(db, run_id),
        recommendation_assembly=ProductionRecommendationAssembly(db, run_id),
    )
    return (
        *scoring_stages,
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
    "assemble_scoring_pipeline_stages",
    "assemble_run_pipeline_coordinator",
]
