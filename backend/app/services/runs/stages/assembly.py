"""Production assembly for the recommendation-pipeline RunCoordinator.

Each stage is constructed with its own explicit Ports/repositories; the coordinator
receives only their protocol.  This module is the single composition root so that
stage wiring stays in one auditable location while individual stage implementations
evolve independently.
"""
from __future__ import annotations

from .contracts import StageData
from ..coordinator import AnyStage, RunCoordinator, RunCoordinatorDependencies


def assemble_pipeline_stages() -> tuple[AnyStage, ...]:
    """Build the ordered tuple of recommendation-pipeline stages.

    Individual stage imports are intentionally lazy: a stage that has not yet been
    migrated simply does not appear here.  As each S5 subtask lands, its import
    and construction are added to this function.
    """
    stages: list[AnyStage] = []
    # Each S5 step adds one import + construction below:
    #
    # from .prepare import PrepareStage
    # stages.append(PrepareStage(...))
    #
    # from .market_data import MarketDataStage
    # stages.append(MarketDataStage(...))
    #
    # …and so on through persistence.
    return tuple(stages)


def assemble_run_pipeline_coordinator(
    dependencies: RunCoordinatorDependencies,
) -> RunCoordinator:
    """Build the production recommendation coordinator with all migrated stages."""
    return RunCoordinator.from_dependencies(dependencies)


__all__ = [
    "assemble_pipeline_stages",
    "assemble_run_pipeline_coordinator",
]
