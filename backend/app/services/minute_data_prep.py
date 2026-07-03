from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import MinuteKbar

logger = logging.getLogger(__name__)


def compare_minute_gap_remediation(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_gate = before.get("minute_gate") if isinstance(before.get("minute_gate"), dict) else {}
    after_gate = after.get("minute_gate") if isinstance(after.get("minute_gate"), dict) else {}
    before_coverage = before.get("data_coverage") if isinstance(before.get("data_coverage"), dict) else {}
    after_coverage = after.get("data_coverage") if isinstance(after.get("data_coverage"), dict) else {}

    before_missing_pairs = minute_gate_missing_pair_count(before_gate)
    after_missing_pairs = minute_gate_missing_pair_count(after_gate)
    resolved_missing_pairs = max(0, before_missing_pairs - after_missing_pairs)

    before_pair_sample = minute_gate_missing_pair_keys(before_gate)
    after_pair_sample = minute_gate_missing_pair_keys(after_gate)
    sample_unresolved_pairs = before_pair_sample & after_pair_sample
    sample_resolved_pairs = before_pair_sample - after_pair_sample

    before_benchmark_missing = minute_gate_benchmark_missing_dates(before_gate)
    after_benchmark_missing = minute_gate_benchmark_missing_dates(after_gate)
    benchmark_resolved = before_benchmark_missing - after_benchmark_missing
    benchmark_unresolved = before_benchmark_missing & after_benchmark_missing

    target_units = before_missing_pairs + len(before_benchmark_missing)
    resolved_units = resolved_missing_pairs + len(benchmark_resolved)
    unresolved_units = after_missing_pairs + len(benchmark_unresolved)

    return {
        "candidate_coverage_rate_delta": round(
            float(after_coverage.get("candidate_minute_coverage_rate", 0) or 0)
            - float(before_coverage.get("candidate_minute_coverage_rate", 0) or 0),
            3,
        ),
        "benchmark_coverage_rate_delta": round(
            float(after_coverage.get("benchmark_minute_coverage_rate", 0) or 0)
            - float(before_coverage.get("benchmark_minute_coverage_rate", 0) or 0),
            3,
        ),
        "gate_passed_before": bool(before_gate.get("passed")),
        "gate_passed_after": bool(after_gate.get("passed")),
        "target_missing_pair_count": before_missing_pairs,
        "sampled_missing_pair_count": len(before_pair_sample),
        "resolved_missing_pair_count": resolved_missing_pairs,
        "unresolved_missing_pair_count": after_missing_pairs,
        "sample_resolved_missing_pair_count": len(sample_resolved_pairs),
        "sample_unresolved_missing_pair_count": len(sample_unresolved_pairs),
        "benchmark_target_missing_date_count": len(before_benchmark_missing),
        "benchmark_resolved_missing_date_count": len(benchmark_resolved),
        "benchmark_unresolved_missing_date_count": len(benchmark_unresolved),
        "target_missing_unit_count": target_units,
        "resolved_missing_unit_count": resolved_units,
        "unresolved_missing_unit_count": unresolved_units,
        "remediation_coverage_rate": round(resolved_units / target_units, 3) if target_units else 1.0,
    }


def minute_gate_missing_pair_count(gate: dict[str, Any]) -> int:
    if gate.get("missing_pair_count") is not None:
        return max(0, int(gate.get("missing_pair_count") or 0))
    candidate_count = gate.get("candidate_count")
    ready_count = gate.get("minute_ready_candidate_count")
    if candidate_count is not None and ready_count is not None:
        return max(0, int(candidate_count or 0) - int(ready_count or 0))
    return len(gate.get("missing_pairs") or [])


def minute_gate_missing_pair_keys(gate: dict[str, Any]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for item in gate.get("missing_pairs") or []:
        if not isinstance(item, dict):
            continue
        trade_date = str(item.get("trade_date") or "").strip()
        code = str(item.get("code") or "").strip()
        if trade_date and code:
            keys.add((trade_date, code))
    return keys


def minute_gate_benchmark_missing_dates(gate: dict[str, Any]) -> set[str]:
    return {str(item) for item in gate.get("benchmark_missing_dates") or [] if str(item or "").strip()}


def local_minute_inventory(db: Session) -> dict[str, Any]:
    try:
        raw_start_date, raw_end_date, trade_date_count = db.execute(
            select(
                func.min(MinuteKbar.trade_date),
                func.max(MinuteKbar.trade_date),
                func.count(func.distinct(MinuteKbar.trade_date)),
            )
        ).one()
        start_date = normalize_trade_date(raw_start_date)
        end_date = normalize_trade_date(raw_end_date)
        if not start_date or not end_date:
            return empty_minute_inventory()

        latest_raw_trade_dates = sorted({end_date, end_date.replace("-", "")})
        latest_code_bucket = (
            select(MinuteKbar.code)
            .where(MinuteKbar.trade_date.in_(latest_raw_trade_dates))
            .group_by(MinuteKbar.code)
            .subquery()
        )
        code_count = db.scalar(select(func.count()).select_from(latest_code_bucket)) or 0

        return {
            "trade_date_start": start_date,
            "trade_date_end": end_date,
            "trade_date_count": int(trade_date_count or 0),
            "code_count": int(code_count or 0),
            "code_count_scope": "latest_trade_date",
            "trade_date_count_scope": "db_raw_trade_dates_indexed",
            "inventory_status": "ok",
        }
    except Exception as exc:
        logger.warning("local minute inventory unavailable: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        return {
            **empty_minute_inventory(),
            "inventory_status": "unavailable",
            "inventory_error": type(exc).__name__,
        }


def empty_minute_inventory() -> dict[str, Any]:
    return {
        "trade_date_start": "",
        "trade_date_end": "",
        "trade_date_count": 0,
        "code_count": 0,
        "code_count_scope": "latest_trade_date",
        "trade_date_count_scope": "db_raw_trade_dates_indexed",
        "inventory_status": "empty",
    }


def build_data_coverage(
    signal_dates: list[str],
    date_summaries: list[dict[str, Any]],
    minute_inventory: dict[str, Any],
    minute_source_trace: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    total_candidates = sum(int(item.get("candidate_count") or 0) for item in date_summaries)
    minute_ready_candidates = sum(int(item.get("minute_ready_candidate_count") or 0) for item in date_summaries)
    benchmark_ready_dates = sum(1 for item in date_summaries if item.get("benchmark_minute_covered"))
    missing_examples: list[str] = []
    for item in date_summaries:
        trade_date = str(item.get("trade_date") or "")
        for code in item.get("missing_minute_codes") or []:
            if len(missing_examples) >= 8:
                break
            missing_examples.append(f"{trade_date} {code}")
    return {
        "signal_date_count": len(signal_dates),
        "candidate_count": total_candidates,
        "minute_ready_candidate_count": minute_ready_candidates,
        "minute_missing_candidate_count": max(0, total_candidates - minute_ready_candidates),
        "candidate_minute_coverage_rate": round(minute_ready_candidates / total_candidates, 3)
        if total_candidates
        else 0.0,
        "benchmark_minute_covered_dates": benchmark_ready_dates,
        "benchmark_minute_coverage_rate": round(benchmark_ready_dates / len(signal_dates), 3) if signal_dates else 0.0,
        "missing_minute_examples": missing_examples,
        "local_minute_inventory": minute_inventory,
        "minute_source_diagnostics": summarize_minute_source_trace(minute_source_trace or []),
    }


def record_minute_source_trace(
    trace: list[dict[str, Any]] | None,
    *,
    source: str,
    code: str,
    status: str,
    row_count: int,
    scope: str,
    reason: str | None = None,
) -> None:
    if trace is None:
        return
    trace.append(
        {
            "source": source,
            "code": code,
            "status": status,
            "row_count": int(row_count or 0),
            "scope": scope,
            "reason": (reason or "")[:160],
        }
    )


def summarize_minute_source_trace(trace: list[dict[str, Any]]) -> dict[str, Any]:
    source_map: dict[tuple[str, str], dict[str, Any]] = {}
    success_sources: set[str] = set()
    fallback_success_sources: set[str] = set()
    tushare_statuses: set[str] = set()
    samples: list[dict[str, Any]] = []
    for item in trace:
        source = str(item.get("source") or "unknown")
        status = str(item.get("status") or "unknown")
        scope = str(item.get("scope") or "external")
        key = (source, status)
        bucket = source_map.setdefault(
            key,
            {
                "source": source,
                "status": status,
                "scope": scope,
                "attempt_count": 0,
                "row_count": 0,
                "example_reason": "",
            },
        )
        bucket["attempt_count"] += 1
        bucket["row_count"] += int(item.get("row_count") or 0)
        if item.get("reason") and not bucket["example_reason"]:
            bucket["example_reason"] = str(item.get("reason") or "")
        if status == "success" and int(item.get("row_count") or 0) > 0:
            success_sources.add(source)
            if source not in {"tushare_stk_mins", "local_memory_cache", "local_minute_db", "local_minute_db_after_fetch"}:
                fallback_success_sources.add(source)
        if source == "tushare_stk_mins":
            tushare_statuses.add(status)
        if len(samples) < 8 and (status != "success" or source == "tushare_stk_mins"):
            samples.append(
                {
                    "source": source,
                    "status": status,
                    "code": item.get("code"),
                    "reason": item.get("reason") or "",
                }
            )
    by_source = sorted(
        source_map.values(),
        key=lambda row: (str(row["source"]), str(row["status"])),
    )
    tushare_permission_denied = bool({"permission_denied", "skipped"} & tushare_statuses)
    return {
        "attempt_count": len(trace),
        "by_source": by_source,
        "successful_sources": sorted(success_sources),
        "fallback_used": bool(fallback_success_sources),
        "tushare_stk_mins_status": ",".join(sorted(tushare_statuses)) if tushare_statuses else "not_attempted",
        "tushare_permission_denied": tushare_permission_denied,
        "samples": samples,
    }


def normalize_trade_date(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
        return raw[:10]
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return raw[:10]
