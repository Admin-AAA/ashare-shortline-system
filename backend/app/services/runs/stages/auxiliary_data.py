"""Auxiliary data stage: run the S4 orchestrator over the prefiltered universe.

This is the simplest stage because S4 already provides a well-typed
``AuxiliaryDataOrchestrator`` that owns refresh → normalize → persist →
load → coverage audit.  The stage wraps its ``prepare()`` call and stores
the result and audit for downstream consumers.
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
class AuxiliaryApplicationPort(Protocol):
    """Run auxiliary data application for a set of instruments."""

    def run_application(
        self,
        *,
        instruments: Sequence[str],
        trade_date: str,
        refresh: bool,
    ) -> Any:
        """Return an ``AuxiliaryApplicationResult`` (opaque at this protocol boundary)."""


# ---------------------------------------------------------------------------
# Stage implementation
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class AuxiliaryDataStage:
    """Prepare auxiliary data through the S4 orchestrator."""

    phase: RunPhase = RunPhase.AUXILIARY_DATA
    auxiliary_application: AuxiliaryApplicationPort | None = None

    def execute(self, context: RunContext, data: StageData) -> StageOutput:
        params = data.params or {}
        snapshot = dict(data.snapshot or {})
        skip_aux_refresh = bool(params.get("skip_aux_refresh"))

        auxiliary_codes = [
            str(stock.get("code") or "")
            for stock in data.prefiltered_stocks
            if stock.get("code")
        ]

        if self.auxiliary_application is not None:
            auxiliary_result = self.auxiliary_application.run_application(
                instruments=auxiliary_codes,
                trade_date=str(snapshot.get("trade_date") or ""),
                refresh=not skip_aux_refresh,
            )
        else:
            raise RuntimeError("AuxiliaryDataStage requires an AuxiliaryApplicationPort")

        # Write audit into snapshot breadth.
        breadth = dict(snapshot.get("breadth") or {})
        breadth["auxiliary_data_audit"] = dict(auxiliary_result.audit_payload)
        breadth["auxiliary_orchestration"] = {
            "usable": auxiliary_result.orchestrated.is_usable,
            "inventory": tuple(item.dataset_key for item in auxiliary_result.orchestrated.results),
            "datasets": dict(auxiliary_result.audit_payload),
            "skip_aux_refresh": skip_aux_refresh,
        }
        snapshot["breadth"] = breadth

        result = RunStageResult(
            phase=RunPhase.AUXILIARY_DATA,
            side_effect_key=f"auxiliary_data:{context.run_id}",
        )
        updated = data.with_updates(
            snapshot=snapshot,
            auxiliary_result=auxiliary_result,
        )
        return StageOutput(result=result, data=updated)


__all__ = [
    "AuxiliaryApplicationPort",
    "AuxiliaryDataStage",
]
