"""Data source trace sink + append-only MySQL repository + health aggregation.

Router 只依赖轻量 ``DataSourceTraceSink`` protocol；生产 sink 写 MySQL，
测试 sink 为内存 collector。health 从统一 trace 聚合，不维护第二套漂移计数。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import DataSourceAttempt, DataSourceRequestTrace as TraceRow
from app.services.data_source_trace import (
    DataSourceAttempt as AttemptContract,
    DataSourceRequestTrace,
    FinalOutcome,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TracePersistResult:
    ok: bool
    telemetry_status: str
    reused: bool = False
    error: str | None = None


class DataSourceTraceSink(Protocol):
    """Lightweight sink the router depends on (no ORM coupling)."""

    def persist(self, trace: DataSourceRequestTrace) -> TracePersistResult: ...


class InMemoryTraceSink:
    """Test collector sink."""

    def __init__(self) -> None:
        self.traces: list[DataSourceRequestTrace] = []

    def persist(self, trace: DataSourceRequestTrace) -> TracePersistResult:
        self.traces.append(trace)
        return TracePersistResult(ok=True, telemetry_status="ok")


def _attempt_to_row(trace_id: str, attempt: AttemptContract) -> DataSourceAttempt:
    validation = attempt.contract_validation
    return DataSourceAttempt(
        attempt_id=f"{trace_id}:{attempt.ordinal}",
        trace_id=trace_id,
        ordinal=attempt.ordinal,
        provider=attempt.provider,
        outcome=attempt.outcome.value,
        duration_ms=attempt.duration_ms,
        failure_category=attempt.failure_category,
        retryable=attempt.retryable,
        fallback_allowed=attempt.fallback_allowed,
        fallback_from=attempt.fallback_from,
        fallback_to=attempt.fallback_to,
        field_fingerprint=attempt.field_fingerprint,
        contract_validation_json=(
            json.dumps(validation.canonical_payload(), ensure_ascii=False, sort_keys=True)
            if validation is not None
            else None
        ),
    )


class MySQLTraceSink:
    """Append-only MySQL sink. 同 trace_id 重复 persist 为幂等 no-op。"""

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    def persist(self, trace: DataSourceRequestTrace) -> TracePersistResult:
        try:
            with self._session_factory() as db:
                existing = db.get(TraceRow, trace.trace_id)
                if existing is not None:
                    # 幂等：同 identity 重跑不追加，视为已持久化
                    return TracePersistResult(ok=True, telemetry_status="ok", reused=True)
                row = TraceRow(
                    trace_id=trace.trace_id,
                    correlation_id=trace.correlation_id,
                    operation_name=trace.operation_name,
                    request_identity_hash=trace.request_identity_hash,
                    contract_id=trace.contract_id,
                    contract_version=trace.contract_version,
                    selected_provider=trace.selected_provider,
                    final_outcome=trace.final_outcome.value,
                    attempt_count=trace.attempt_count,
                    fallback_count=trace.fallback_count,
                    duration_ms=sum(a.duration_ms for a in trace.attempts),
                    telemetry_status="ok",
                    started_at=datetime.now(timezone.utc).replace(tzinfo=None),
                    completed_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
                db.add(row)
                for attempt in trace.attempts:
                    db.add(_attempt_to_row(trace.trace_id, attempt))
                db.commit()
                return TracePersistResult(ok=True, telemetry_status="ok")
        except Exception as exc:  # 持久化失败不阻断已取到的数据，但显式标记  # noqa: BLE001
            logger.warning("data source trace persist failed trace=%s: %s", trace.trace_id, exc)
            return TracePersistResult(
                ok=False, telemetry_status="failed", error=str(exc)[:160]
            )


# ── 查询 / health 聚合 ────────────────────────────────────────────────

def _row_to_summary(row: TraceRow, attempts: Sequence[DataSourceAttempt]) -> dict[str, Any]:
    return {
        "trace_id": row.trace_id,
        "correlation_id": row.correlation_id,
        "operation_name": row.operation_name,
        "contract_id": row.contract_id,
        "contract_version": row.contract_version,
        "selected_provider": row.selected_provider,
        "final_outcome": row.final_outcome,
        "attempt_count": row.attempt_count,
        "fallback_count": row.fallback_count,
        "duration_ms": row.duration_ms,
        "telemetry_status": row.telemetry_status,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "attempts": [
            {
                "ordinal": a.ordinal,
                "provider": a.provider,
                "outcome": a.outcome,
                "duration_ms": a.duration_ms,
                "failure_category": a.failure_category,
                "retryable": a.retryable,
                "fallback_allowed": a.fallback_allowed,
                "fallback_from": a.fallback_from,
                "fallback_to": a.fallback_to,
                "contract_validation": (
                    json.loads(a.contract_validation_json)
                    if a.contract_validation_json
                    else None
                ),
            }
            for a in attempts
        ],
    }


def list_traces(
    db: Session,
    *,
    operation_name: str | None = None,
    provider: str | None = None,
    final_outcome: str | None = None,
    failure_category: str | None = None,
    correlation_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    stmt = select(TraceRow).order_by(TraceRow.started_at.desc()).limit(min(int(limit), 500))
    if operation_name:
        stmt = stmt.where(TraceRow.operation_name == operation_name)
    if final_outcome:
        stmt = stmt.where(TraceRow.final_outcome == final_outcome)
    if correlation_id:
        stmt = stmt.where(TraceRow.correlation_id == correlation_id)
    rows = list(db.scalars(stmt).all())
    if not rows:
        return []
    trace_ids = [row.trace_id for row in rows]
    attempts = list(
        db.scalars(
            select(DataSourceAttempt)
            .where(DataSourceAttempt.trace_id.in_(trace_ids))
            .order_by(DataSourceAttempt.trace_id, DataSourceAttempt.ordinal)
        ).all()
    )
    by_trace: dict[str, list[DataSourceAttempt]] = {}
    for a in attempts:
        by_trace.setdefault(a.trace_id, []).append(a)
    summaries = [_row_to_summary(row, by_trace.get(row.trace_id, [])) for row in rows]
    if provider:
        summaries = [s for s in summaries if any(a["provider"] == provider for a in s["attempts"])]
    if failure_category:
        summaries = [
            s for s in summaries
            if any(a["failure_category"] == failure_category for a in s["attempts"])
        ]
    return summaries


def recent_fallback_chains(db: Session, *, limit: int = 20) -> list[dict[str, Any]]:
    """最近发生过 fallback（>1 attempt 或 fallback_count>0）的 trace chain。"""
    stmt = (
        select(TraceRow)
        .where(TraceRow.fallback_count > 0)
        .order_by(TraceRow.started_at.desc())
        .limit(min(int(limit), 200))
    )
    rows = list(db.scalars(stmt).all())
    if not rows:
        return []
    trace_ids = [row.trace_id for row in rows]
    attempts = list(
        db.scalars(
            select(DataSourceAttempt)
            .where(DataSourceAttempt.trace_id.in_(trace_ids))
            .order_by(DataSourceAttempt.trace_id, DataSourceAttempt.ordinal)
        ).all()
    )
    by_trace: dict[str, list[DataSourceAttempt]] = {}
    for a in attempts:
        by_trace.setdefault(a.trace_id, []).append(a)
    chains: list[dict[str, Any]] = []
    for row in rows:
        atts = by_trace.get(row.trace_id, [])
        chains.append({
            "trace_id": row.trace_id,
            "operation_name": row.operation_name,
            "final_outcome": row.final_outcome,
            "selected_provider": row.selected_provider,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "chain": [
                {
                    "provider": a.provider,
                    "outcome": a.outcome,
                    "failure_category": a.failure_category,
                }
                for a in atts
            ],
        })
    return chains


def health_aggregation(db: Session, *, hours: int = 24) -> dict[str, Any]:
    """从统一 trace 聚合 success/fallback/valid-empty/breaker-skip/p95。"""
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=int(hours))
    rows = list(
        db.scalars(select(TraceRow).where(TraceRow.started_at >= cutoff)).all()
    )
    if not rows:
        return {
            "window_hours": hours,
            "trace_count": 0,
            "success_rate": None,
            "fallback_rate": None,
            "valid_empty_rate": None,
            "breaker_skip_count": 0,
            "p95_duration_ms": None,
            "by_operation": [],
        }
    total = len(rows)
    success = sum(1 for r in rows if r.final_outcome == FinalOutcome.SUCCESS.value)
    valid_empty = sum(1 for r in rows if r.final_outcome == FinalOutcome.VALID_EMPTY.value)
    fallback = sum(1 for r in rows if (r.fallback_count or 0) > 0)
    durations = sorted(int(r.duration_ms or 0) for r in rows)
    p95 = durations[min(len(durations) - 1, int(len(durations) * 0.95))] if durations else None
    trace_ids = [r.trace_id for r in rows]
    breaker_skips = int(
        db.scalar(
            select(func.count())
            .select_from(DataSourceAttempt)
            .where(
                DataSourceAttempt.trace_id.in_(trace_ids),
                DataSourceAttempt.outcome == "skipped_breaker",
            )
        )
        or 0
    )
    by_op: dict[str, dict[str, Any]] = {}
    for r in rows:
        bucket = by_op.setdefault(
            r.operation_name, {"operation_name": r.operation_name, "count": 0, "success": 0, "fallback": 0}
        )
        bucket["count"] += 1
        bucket["success"] += 1 if r.final_outcome == FinalOutcome.SUCCESS.value else 0
        bucket["fallback"] += 1 if (r.fallback_count or 0) > 0 else 0
    return {
        "window_hours": hours,
        "trace_count": total,
        "success_rate": round(success / total, 3) if total else None,
        "fallback_rate": round(fallback / total, 3) if total else None,
        "valid_empty_rate": round(valid_empty / total, 3) if total else None,
        "breaker_skip_count": breaker_skips,
        "p95_duration_ms": p95,
        "by_operation": sorted(by_op.values(), key=lambda x: -x["count"]),
    }


__all__ = [
    "DataSourceTraceSink",
    "InMemoryTraceSink",
    "MySQLTraceSink",
    "TracePersistResult",
    "health_aggregation",
    "list_traces",
    "recent_fallback_chains",
]
