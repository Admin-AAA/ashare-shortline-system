"""Pure S8 dashboard hardening projection contracts.

This module performs no I/O and owns no cache.  It projects already-authoritative
run rows into evidence-labelled dashboard facts without changing any production
recommendation selector.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
from typing import Any

from app.services.evidence.identity import EvidenceClass
from app.services.evidence.selectors import is_live_forward_run


class DashboardEvidenceSection(StrEnum):
    LIVE_FORWARD = "LIVE_FORWARD"
    HISTORICAL_RECONSTRUCTION = "HISTORICAL_RECONSTRUCTION"
    RESEARCH = "RESEARCH"


@dataclass(frozen=True, slots=True)
class DashboardRunEvidence:
    run_id: str
    evidence_section: DashboardEvidenceSection
    evidence_class: str
    actual_generated_at: datetime | None
    business_date: str
    signal_date: str
    trade_date: str
    reconstruction_label: str | None
    data_quality_decision: str
    data_quality_reasons: tuple[str, ...]
    risk_gate: str
    risk_reasons: tuple[str, ...]
    last_updated_at: datetime | None
    status: str

    @property
    def current_eligible(self) -> bool:
        return (
            self.actual_generated_at is not None
            and self.evidence_section
            in {
                DashboardEvidenceSection.LIVE_FORWARD,
                DashboardEvidenceSection.HISTORICAL_RECONSTRUCTION,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "evidence_section": self.evidence_section.value,
            "evidence_class": self.evidence_class,
            "actual_generated_at": _iso(self.actual_generated_at),
            "business_date": self.business_date,
            "signal_date": self.signal_date,
            "trade_date": self.trade_date,
            "reconstruction_label": self.reconstruction_label,
            "data_quality_decision": self.data_quality_decision,
            "data_quality_reasons": list(self.data_quality_reasons),
            "risk_gate": self.risk_gate,
            "risk_reasons": list(self.risk_reasons),
            "last_updated_at": _iso(self.last_updated_at),
            "status": self.status,
        }


def project_run_evidence(row: Any) -> DashboardRunEvidence:
    """Project one persisted run without inferring LIVE from mutable labels."""
    raw_class = str(_value(row, "evidence_class") or EvidenceClass.UNVERIFIED.value)
    if is_live_forward_run(row):
        section = DashboardEvidenceSection.LIVE_FORWARD
    elif _is_historical_reconstruction(row):
        section = DashboardEvidenceSection.HISTORICAL_RECONSTRUCTION
    else:
        section = DashboardEvidenceSection.RESEARCH

    actual_generated_at = _datetime(_value(row, "actual_generated_at"))
    trade_date = str(_value(row, "trade_date") or "")
    data_as_of = str(_value(row, "data_as_of") or trade_date)
    summary = _json_mapping(_value(row, "result_summary_json"))
    quality_status, quality_reasons = _decision(summary, "data_quality_decision")
    risk_status, risk_reasons = _decision(summary, "risk_gate")
    batch_id = str(_value(row, "reconstruction_batch_id") or "").strip()
    reconstruction_label = None
    if section is DashboardEvidenceSection.HISTORICAL_RECONSTRUCTION:
        reconstruction_label = f"历史重建 · {batch_id}" if batch_id else "历史重建"

    updated_candidates = (
        _datetime(_value(row, "heartbeat_at")),
        _datetime(_value(row, "finished_at")),
        actual_generated_at,
        _datetime(_value(row, "started_at")),
    )
    last_updated_at = max((item for item in updated_candidates if item is not None), default=None)
    return DashboardRunEvidence(
        run_id=str(_value(row, "run_id") or ""),
        evidence_section=section,
        evidence_class=raw_class,
        actual_generated_at=actual_generated_at,
        business_date=trade_date,
        signal_date=trade_date,
        trade_date=trade_date,
        reconstruction_label=reconstruction_label,
        data_quality_decision=quality_status,
        data_quality_reasons=quality_reasons,
        risk_gate=risk_status,
        risk_reasons=risk_reasons,
        last_updated_at=last_updated_at,
        status=str(_value(row, "status") or "unknown"),
    )


def select_current_run(
    runs: Iterable[DashboardRunEvidence],
) -> DashboardRunEvidence | None:
    """Select current evidence truthfully.

    Provenance-complete LIVE evidence has authority over reconstruction.  Within
    a class, business date is compared before the real generation timestamp.
    Research/unverified evidence can never become current.
    """
    population = tuple(runs)
    for section in (
        DashboardEvidenceSection.LIVE_FORWARD,
        DashboardEvidenceSection.HISTORICAL_RECONSTRUCTION,
    ):
        eligible = [
            item
            for item in population
            if item.evidence_section is section and item.current_eligible
        ]
        if eligible:
            return max(
                eligible,
                key=lambda item: (
                    _date_key(item.business_date),
                    _utc(item.actual_generated_at),
                    item.run_id,
                ),
            )
    return None


def projection_is_stale(
    last_updated_at: datetime,
    *,
    now: datetime,
    stale_threshold: timedelta,
) -> bool:
    """Return true iff projection age strictly exceeds the configured threshold."""
    if stale_threshold.total_seconds() < 0:
        raise ValueError("stale_threshold must not be negative")
    return _utc(now) - _utc(last_updated_at) > stale_threshold


def project_dashboard_hardening(
    rows: Iterable[Any],
    *,
    now: datetime,
    current_business_date: str,
    stale_threshold: timedelta,
    fallback_data_quality: str = "unknown",
    fallback_risk_gate: str = "unknown",
) -> dict[str, Any]:
    """Build the additive S8 projection from existing authoritative rows."""
    facts = tuple(project_run_evidence(row) for row in rows)
    current = select_current_run(facts)
    buckets = {
        section.value: [
            item.to_dict() for item in facts if item.evidence_section is section
        ]
        for section in DashboardEvidenceSection
    }
    last_updated_at = current.last_updated_at if current else None
    stale = (
        projection_is_stale(last_updated_at, now=now, stale_threshold=stale_threshold)
        if last_updated_at is not None
        else False
    )
    current_quality = current.data_quality_decision if current else fallback_data_quality
    current_risk = current.risk_gate if current else fallback_risk_gate
    if current_quality == "unknown":
        current_quality = fallback_data_quality
    if current_risk == "unknown":
        current_risk = fallback_risk_gate
    is_today_live = bool(
        current
        and current.evidence_section is DashboardEvidenceSection.LIVE_FORWARD
        and current.business_date == current_business_date
        and current.status == "completed"
    )
    if is_today_live:
        current_label = "今日 LIVE 已运行"
    elif current and current.evidence_section is DashboardEvidenceSection.HISTORICAL_RECONSTRUCTION:
        current_label = current.reconstruction_label or "历史重建"
    elif current:
        current_label = "LIVE_FORWARD"
    else:
        current_label = "无可验证 LIVE/重建证据"
    return {
        "generated_at": _iso(now),
        "last_updated_at": _iso(last_updated_at),
        "stale_threshold_seconds": int(stale_threshold.total_seconds()),
        "stale": stale,
        "current_run": current.to_dict() if current else None,
        "current_label": current_label,
        "today_live_completed": is_today_live,
        "data_quality_decision": current_quality,
        "risk_gate": current_risk,
        "evidence": buckets,
    }


def _is_historical_reconstruction(row: Any) -> bool:
    required = (
        "actual_generated_at",
        "reconstruction_batch_id",
        "evidence_identity_hash",
        "strategy_policy_fingerprint",
        "execution_policy_fingerprint",
        "code_revision",
        "provenance_schema_version",
        "data_as_of",
        "source_snapshot_id",
    )
    return (
        _value(row, "evidence_class")
        == EvidenceClass.HISTORICAL_RECONSTRUCTION.value
        and all(_value(row, field) is not None for field in required)
    )


def _decision(summary: Mapping[str, Any], key: str) -> tuple[str, tuple[str, ...]]:
    raw = summary.get(key)
    if isinstance(raw, Mapping):
        status = str(raw.get("status") or raw.get("decision") or "unknown")
        reasons = raw.get("reason_codes") or raw.get("reasons") or ()
        if not isinstance(reasons, (list, tuple)):
            reasons = (str(reasons),)
        return status, tuple(str(item) for item in reasons if str(item))
    if isinstance(raw, str) and raw.strip():
        return raw.strip(), ()
    return "unknown", ()


def _json_mapping(raw: Any) -> Mapping[str, Any]:
    if isinstance(raw, Mapping):
        return raw
    try:
        value = json.loads(str(raw or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, Mapping) else {}


def _value(row: Any, name: str) -> Any:
    return row.get(name) if isinstance(row, Mapping) else getattr(row, name, None)


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _utc(value)
    if not value:
        return None
    try:
        return _utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return None


def _utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return _utc(value).isoformat().replace("+00:00", "Z") if value is not None else None


def _date_key(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return date.min


__all__ = [
    "DashboardEvidenceSection",
    "DashboardRunEvidence",
    "project_dashboard_hardening",
    "project_run_evidence",
    "projection_is_stale",
    "select_current_run",
]
