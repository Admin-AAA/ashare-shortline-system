"""Feature stage: build features and research contexts for the prefiltered universe.

This stage wraps ``build_features`` and ``build_research_contexts`` behind a
typed Port so the stage does not directly import the feature builder or
access the database session.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol, Sequence, runtime_checkable

from ..contracts import RunPhase, RunStageResult
from .contracts import StageData, StageMapping, StageOutput
from ..coordinator import RunContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Port
# ---------------------------------------------------------------------------

@runtime_checkable
class FeatureBuildPort(Protocol):
    """Build features and research contexts for a set of stocks."""

    def build(
        self,
        stocks: Sequence[StageMapping],
        snapshot: StageMapping,
    ) -> tuple[tuple[StageMapping, ...], tuple[Any, ...]]:
        """Return (features, research_contexts)."""


# ---------------------------------------------------------------------------
# Stage implementation
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class FeaturesStage:
    """Build features and research contexts from the prefiltered stock list."""

    phase: RunPhase = RunPhase.FEATURES
    feature_build: FeatureBuildPort | None = None

    def execute(self, context: RunContext, data: StageData) -> StageOutput:
        if self.feature_build is not None:
            features, research_contexts = self.feature_build.build(
                stocks=data.prefiltered_stocks,
                snapshot=data.snapshot or {},
            )
        else:
            raise RuntimeError("FeaturesStage requires a FeatureBuildPort")

        result = RunStageResult(
            phase=RunPhase.FEATURES,
            side_effect_key=f"features:{context.run_id}",
        )
        updated = data.with_updates(
            features=features,
            research_contexts=research_contexts,
        )
        return StageOutput(result=result, data=updated)


__all__ = [
    "FeatureBuildPort",
    "FeaturesStage",
]
