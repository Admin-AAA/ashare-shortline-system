"""Market data stage: load universe, persist snapshot, scope, and prefilter.

This stage depends on ``MarketSourcePort`` for quote/kline retrieval and on
auxiliary state for prefilter injection.  It produces the scoped, prefiltered
stock list and the market snapshot that downstream stages consume.
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
# Ports
# ---------------------------------------------------------------------------

@runtime_checkable
class MarketUniversePort(Protocol):
    """Load the full market universe and produce a snapshot."""

    def load_universe(
        self,
        *,
        data_source: str,
        as_of_date: str | None,
    ) -> tuple[tuple[StageMapping, ...], StageMapping, StageMapping]:
        """Return (stocks, snapshot, market_source_audit)."""


@runtime_checkable
class MarketPersistencePort(Protocol):
    """Persist market snapshot, stocks, and daily bars."""

    def persist_snapshot(self, snapshot: StageMapping) -> None: ...

    def persist_stocks_and_bars(
        self,
        stocks: Sequence[StageMapping],
        *,
        persist_bars: bool,
    ) -> None: ...


@runtime_checkable
class MarketScopePort(Protocol):
    """Apply market scope and prefilter to the stock universe."""

    def apply_scope(
        self,
        stocks: Sequence[StageMapping],
        params: StageMapping,
    ) -> tuple[StageMapping, ...]: ...

    def apply_prefilter(
        self,
        scoped_stocks: Sequence[StageMapping],
        selected_skills: Sequence[StageMapping],
        params: StageMapping,
        snapshot: StageMapping,
    ) -> tuple[tuple[StageMapping, ...], StageMapping]:
        """Return (prefiltered_stocks, prefilter_audit)."""


# ---------------------------------------------------------------------------
# Stage implementation
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class MarketDataStage:
    """Load market universe, persist snapshot, apply scope and prefilter."""

    phase: RunPhase = RunPhase.MARKET_DATA
    market_universe: MarketUniversePort | None = None
    market_persistence: MarketPersistencePort | None = None
    market_scope: MarketScopePort | None = None

    def execute(self, context: RunContext, data: StageData) -> StageOutput:
        params = data.params or {}
        data_source = str(params.get("data_source") or "local_pool")
        trade_date_override = params.get("trade_date_override") or None

        # 1. Load universe.
        if self.market_universe is not None:
            stocks, snapshot, market_audit = self.market_universe.load_universe(
                data_source=data_source,
                as_of_date=trade_date_override,
            )
        else:
            raise RuntimeError("MarketDataStage requires a MarketUniversePort")

        # Apply data_source mode to snapshot.
        snapshot_dict = dict(snapshot)
        breadth = dict(snapshot_dict.get("breadth") or {})
        breadth["run_data_source_mode"] = data_source
        breadth["market_source_audit"] = market_audit
        snapshot_dict["breadth"] = breadth

        # 2. Persist market snapshot.
        if self.market_persistence is not None:
            self.market_persistence.persist_snapshot(snapshot_dict)
            should_persist_bars = data_source in ("realtime", "local_pool_realtime")
            if should_persist_bars:
                self.market_persistence.persist_stocks_and_bars(stocks, persist_bars=True)

        # 3. Apply market scope.
        if self.market_scope is not None:
            scoped_stocks = self.market_scope.apply_scope(stocks, params)
            if not scoped_stocks:
                raise ValueError("当前市场范围内没有可运行的股票，请调整预设范围后重试")

            prefiltered_stocks, prefilter_audit = self.market_scope.apply_prefilter(
                scoped_stocks, data.selected_skills, params, snapshot_dict,
            )
            if not prefiltered_stocks:
                raise ValueError("前置预过滤后没有可运行的股票，请放宽候选条件后重试")
        else:
            scoped_stocks = stocks
            prefiltered_stocks = stocks
            prefilter_audit = {}

        breadth["scoped_candidate_count"] = len(scoped_stocks)
        breadth["prefilter_candidate_count"] = len(prefiltered_stocks)
        snapshot_dict["breadth"] = breadth

        result = RunStageResult(
            phase=RunPhase.MARKET_DATA,
            side_effect_key=f"market_data:{context.run_id}",
        )
        updated = data.with_updates(
            stocks=stocks,
            snapshot=snapshot_dict,
            market_source_audit=market_audit,
            scoped_stocks=scoped_stocks,
            prefiltered_stocks=prefiltered_stocks,
            prefilter_audit=prefilter_audit,
        )
        return StageOutput(result=result, data=updated)


__all__ = [
    "MarketDataStage",
    "MarketPersistencePort",
    "MarketScopePort",
    "MarketUniversePort",
]
