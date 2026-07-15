"""Project existing workbench auxiliary refresh evidence into typed contracts.

The legacy workbench remains responsible for the established provider calls and
cache rules.  This bridge is deliberately read-only: it turns the resulting audit
into the shared immutable result model without causing a second refresh or any new
network traffic.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Mapping, Sequence

from .contracts import (
    AuxiliaryAuditEvent,
    AuxiliaryAuditOutcome,
    AuxiliaryDatasetResult,
    AuxiliaryError,
    AuxiliaryErrorCode,
    AuxiliaryInstrument,
    AuxiliaryOrchestratorResult,
    AuxiliaryStage,
    CoverageResult,
    RefreshDecision,
    RefreshReason,
)

_SUCCESS_STATUSES = frozenset({"success", "cached", "force_synced_on_skip"})
_SKIPPED_STATUSES = frozenset({"skipped", "skipped_fast_run"})


def project_legacy_auxiliary_audit(
    audit: Mapping[str, Mapping[str, Any]],
    *,
    instruments: Sequence[AuxiliaryInstrument],
    trade_date: date,
    refresh_enabled: bool,
) -> AuxiliaryOrchestratorResult:
    """Return a typed, persistence-safe projection of workbench auxiliary results.

    Coverage intentionally reflects only the legacy audit's documented count; this
    bridge never invents code-level coverage for market-wide datasets.  A failed
    legacy refresh is explicit in both ``errors`` and audit, while skipped fast runs
    remain usable only when the established pipeline chooses to continue with its
    cached inputs.
    """
    requested_codes = tuple(item.code for item in instruments)
    results: list[AuxiliaryDatasetResult[dict[str, Any]]] = []
    now = datetime.now(timezone.utc)
    for dataset_key, raw in audit.items():
        row = dict(raw or {})
        status = str(row.get("status") or "skipped")
        count = max(0, int(row.get("count") or 0))
        failed = status == "failed"
        skipped = status in _SKIPPED_STATUSES
        outcome = AuxiliaryAuditOutcome.FAILED if failed else (
            AuxiliaryAuditOutcome.SKIPPED if skipped else AuxiliaryAuditOutcome.SUCCEEDED
        )
        reason = (
            RefreshReason.REFRESH_DISABLED
            if skipped or not refresh_enabled
            else RefreshReason.POLICY_ALWAYS
        )
        coverage = CoverageResult(
            record_count=count,
            requested_instruments=requested_codes,
            covered_instruments=(),
            missing_instruments=requested_codes,
            instrument_coverage_ratio=1.0 if not requested_codes else 0.0,
            # Legacy auxiliary datasets include market-wide and code-specific data;
            # count>0 is the only truthful universal coverage fact available here.
            sufficient=not failed,
            source="legacy_workbench_audit",
        )
        details = {
            "status": status,
            "count": count,
            "elapsed_ms": row.get("elapsed_ms", 0),
            "trade_date": trade_date.isoformat(),
        }
        if row.get("requested_count") is not None:
            details["requested_count"] = row["requested_count"]
        errors = ()
        if failed:
            errors = (
                AuxiliaryError(
                    AuxiliaryErrorCode.FETCH_FAILED,
                    AuxiliaryStage.REFRESH,
                    str(row.get("error") or f"legacy dataset {dataset_key} refresh failed"),
                    retriable=True,
                ),
            )
        results.append(
            AuxiliaryDatasetResult(
                dataset_key=str(dataset_key),
                view_key=f"legacy.{dataset_key}",
                view={"legacy_status": status, "record_count": count},
                coverage=coverage,
                refresh=RefreshDecision(not skipped, reason, trade_date),
                audit=(AuxiliaryAuditEvent.create(AuxiliaryStage.REFRESH, outcome, now, details),),
                errors=errors,
            )
        )
    return AuxiliaryOrchestratorResult(results=tuple(results))


def auxiliary_projection_payload(result: AuxiliaryOrchestratorResult) -> dict[str, Any]:
    """Serialize only stable audit facts for ``RecommendationRun`` JSON columns."""
    return {
        "usable": result.is_usable,
        "datasets": [
            {
                "dataset_key": item.dataset_key,
                "view_key": item.view_key,
                "record_count": item.coverage.record_count,
                "sufficient": item.coverage.sufficient,
                "refresh_required": item.refresh.required,
                "refresh_reason": item.refresh.reason.value,
                "errors": [error.code.value for error in item.errors],
                "audit": [
                    {
                        "stage": event.stage.value,
                        "outcome": event.outcome.value,
                        "details": dict(event.details),
                    }
                    for event in item.audit
                ],
            }
            for item in result.results
        ],
    }


__all__ = ["auxiliary_projection_payload", "project_legacy_auxiliary_audit"]
