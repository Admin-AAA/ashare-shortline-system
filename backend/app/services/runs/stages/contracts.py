"""Typed values shared by constructor-injected recommendation run stages.

``StageData`` is an immutable, in-process hand-off between adjacent stages.  It
contains only explicitly named values; ``RunContext`` remains the durable lifecycle
projection and never becomes an untyped pipeline bag.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping

from ..contracts import RunStageResult


StageMapping = Mapping[str, object]


@dataclass(frozen=True, slots=True)
class StageData:
    """Immutable output accumulated while one coordinator advances a run.

    Concrete stages populate only the fields they own.  Dictionary-shaped legacy
    business values remain shallowly immutable at this boundary while their source
    services are migrated to stronger DTOs in their respective stage tasks.
    """

    # prepare
    selected_skills: tuple[StageMapping, ...] = ()
    execution_version: str | None = None
    execution_audit: StageMapping | None = None
    params: StageMapping | None = None

    # market_data / auxiliary_data
    stocks: tuple[StageMapping, ...] = ()
    snapshot: StageMapping | None = None
    market_source_audit: StageMapping | None = None
    scoped_stocks: tuple[StageMapping, ...] = ()
    prefiltered_stocks: tuple[StageMapping, ...] = ()
    prefilter_audit: StageMapping | None = None
    auxiliary_result: object | None = None

    # features / strategy_evaluation / review
    features: tuple[StageMapping, ...] = ()
    research_contexts: tuple[object, ...] = ()
    prelim_entries: tuple[StageMapping, ...] = ()
    skill_candidates: tuple[StageMapping, ...] = ()
    skill_gate_audit: StageMapping | None = None
    pipeline_audit: StageMapping | None = None
    filter_audit: StageMapping | None = None
    candidate_results: tuple[StageMapping, ...] = ()
    api_costs: tuple[StageMapping, ...] = ()
    skill_review_failure_audit: StageMapping | None = None

    # aggregation / persistence
    cards: tuple[StageMapping, ...] = ()
    scored_cards: tuple[StageMapping, ...] = ()
    degraded_watchlist: tuple[StageMapping, ...] = ()
    climax_watchlist: tuple[StageMapping, ...] = ()
    saved_recommendations: tuple[object, ...] = ()

    # orchestration audit
    stage_timings_ms: StageMapping | None = None
    stage_audit: StageMapping | None = None

    def with_updates(self, **changes: object) -> "StageData":
        """Return a new stage hand-off without mutating prior-stage output."""
        return replace(self, **changes)


@dataclass(frozen=True, slots=True)
class StageOutput:
    """One stage's side-effect checkpoint and explicit hand-off to its successor."""

    result: RunStageResult
    data: StageData


__all__ = ["StageData", "StageMapping", "StageOutput"]
