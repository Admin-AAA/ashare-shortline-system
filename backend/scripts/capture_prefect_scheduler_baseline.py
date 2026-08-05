#!/usr/bin/env python3
"""Capture the read-only Prefect and scheduler baseline for hardening task 7.1.

Only explicitly allowlisted control-plane fields and aggregate runtime metrics are
written. Process command lines, environment variables, API locations, database
paths, task parameters, payload/result JSON, and error text are never emitted.
This module is evidence capture only; it does not configure concurrency, create
leases, claim occurrences, or change scheduler behavior.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import heapq
import json
import math
import os
from pathlib import Path
import re
import shlex
import sqlite3
import subprocess
import sys
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

# Support both ``python scripts/...py`` and import-based test execution.
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import prefect
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.tables import ScheduledTask, ScheduledTaskRun
from app.orchestration.deployments import WORK_POOL_NAME
from scripts.capture_production_evidence_baseline import (
    _forbidden_literals_from_settings,
    assert_safe_output,
    repository_identity,
)


BASELINE_SCHEMA_VERSION = 1
DEFAULT_WINDOW_DAYS = 7
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = (
    REPO_ROOT
    / ".kiro/specs/production-evidence-risk-hardening/baseline-task-7.1.json"
)
DEFAULT_PREFECT_DB = Path.home() / ".prefect" / "prefect.db"
BEIJING = ZoneInfo("Asia/Shanghai")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:/-]{1,160}$")
_SQLITE_SUFFIX = re.compile(r"-(?:wal|shm)$")


_SCHEDULER_IDENTITY_CONTRACTS: dict[str, dict[str, str]] = {
    "recommendation_run": {
        "business_date": (
            "related RecommendationRun.trade_date; a post-close pipeline resolves "
            "the latest allowed market date when no date was requested"
        ),
        "scope": (
            "RecommendationRun.run_mode + evaluation_scope + preset_id; the "
            "ScheduledTask row alone does not materialize that scope"
        ),
        "cardinality": "one related recommendation run per successful task attempt",
    },
    "formal_safety_net": {
        "business_date": (
            "the missing formal RecommendationRun.trade_date selected by the gap check"
        ),
        "scope": "formal/live_forward + preset identity",
        "cardinality": "one missing trading date selected per remediation attempt",
    },
    "strategy_exposure_run": {
        "business_date": "trade_date of each experiment RecommendationRun emitted by the batch",
        "scope": "strategy_id + run_mode=experiment + exposure window",
        "cardinality": "one scheduler attempt may emit multiple strategy/date identities",
    },
    "intraday_lock_shadow": {
        "business_date": "Asia/Shanghai market-session date evaluated by the action",
        "scope": "action=create|settle + monitored recommendation cohort",
        "cardinality": "one action-wide cohort per scheduled attempt",
    },
    "data_sync": {
        "business_date": (
            "target market trade date resolved by each sync step; latest allowed date "
            "is used where the request omits one"
        ),
        "scope": "sync_scope + dataset/table family",
        "cardinality": "a batch may contain multiple dataset/date identities",
    },
    "data_backfill": {
        "business_date": "each target missing market-data date selected for remediation",
        "scope": "target table set + code universe + remediation scope",
        "cardinality": "a batch may contain multiple table/date identities",
    },
    "pool_refresh": {
        "business_date": "Asia/Shanghai quote as-of/trading date refreshed by the task",
        "scope": "stock quote pool + requested universe",
        "cardinality": "one quote-pool cohort per attempt",
    },
    "outcome_label": {
        "business_date": "Recommendation.trade_date of each outcome cohort being labeled",
        "scope": "evaluation scope + horizon/label policy",
        "cardinality": "a settlement batch may label multiple recommendation dates",
    },
    "preset_matrix_run": {
        "business_date": "trade_date of the RecommendationRuns emitted by the matrix batch",
        "scope": "matrix batch identity + run_mode + frozen active preset set",
        "cardinality": "one manual batch may emit multiple preset/date identities",
    },
}

_PREFECT_FLOW_IDENTITY_CONTRACTS: tuple[dict[str, str], ...] = (
    {
        "flow_name": "scheduled-task",
        "business_date": "delegated to the persisted scheduler task_type contract",
        "scope": "delegated task scope; task_id identifies only the definition",
        "durable_identity": "task_id is present; business_date/scope are not first-class",
        "scheduled_for": "Prefect flow_run.expected_start_time",
    },
    {
        "flow_name": "recommendation-run",
        "business_date": "RecommendationRun.trade_date addressed by run_id",
        "scope": "RecommendationRun.run_mode + evaluation_scope + preset identity",
        "durable_identity": "run_id",
        "scheduled_for": "dispatch/expected start time",
    },
    {
        "flow_name": "manual-preset-matrix",
        "business_date": "matrix output RecommendationRun.trade_date",
        "scope": "matrix batch + run_mode + preset set",
        "durable_identity": "ScheduledTaskRun.task_run_id",
        "scheduled_for": "manual dispatch time",
    },
    {
        "flow_name": "manual-data-health-remediation",
        "business_date": "each remediated market-data date",
        "scope": "target tables + code universe",
        "durable_identity": "ScheduledTaskRun.task_run_id",
        "scheduled_for": "manual dispatch time",
    },
    {
        "flow_name": "manual-strategy-exposure",
        "business_date": "each emitted experiment RecommendationRun.trade_date",
        "scope": "strategy set + exposure window",
        "durable_identity": "ScheduledTaskRun.task_run_id",
        "scheduled_for": "manual dispatch time",
    },
    {
        "flow_name": "intraday-lock-poll",
        "business_date": "Asia/Shanghai market-session date",
        "scope": "five-minute scheduled bucket + all open monitored positions",
        "durable_identity": "deployment + expected_start_time",
        "scheduled_for": "Prefect flow_run.expected_start_time",
    },
    {
        "flow_name": "stale-run-recovery",
        "business_date": "Asia/Shanghai recovery execution date",
        "scope": "all recommendation runs older than the recovery cutoff",
        "durable_identity": "deployment + expected_start_time",
        "scheduled_for": "Prefect flow_run.expected_start_time",
    },
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=lambda item: item.isoformat() if isinstance(item, datetime) else str(item),
    )


def _digest(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _database_id(path: Path) -> str:
    identity = sha256(str(path.expanduser().resolve()).encode("utf-8")).hexdigest()
    return f"prefect-sqlite-{identity[:16]}"


def _safe_name(value: Any, *, fallback_prefix: str) -> str:
    rendered = str(value or "")
    if _SAFE_IDENTIFIER.fullmatch(rendered):
        return rendered
    return f"{fallback_prefix}-{sha256(rendered.encode('utf-8')).hexdigest()[:16]}"


def _as_utc(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text_value = str(value).strip()
        if text_value.endswith("Z"):
            text_value = f"{text_value[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text_value)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _round_seconds(value: float | int | None) -> float | None:
    return None if value is None else round(float(value), 3)


def _nearest_rank(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    if not raw:
        return {}
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _command_role(tokens: Sequence[str]) -> tuple[str | None, int | None]:
    normalized = [Path(token).name for token in tokens]
    for index, token in enumerate(normalized):
        if token != "prefect" or index + 2 >= len(tokens):
            continue
        candidate = normalized[index + 1 : index + 3]
        if candidate == ["server", "start"]:
            return "server", index
        if candidate == ["worker", "start"]:
            return "worker", index
    return None, None


def _option_value(tokens: Sequence[str], *flags: str) -> str | None:
    for flag in flags:
        try:
            index = tokens.index(flag)
        except ValueError:
            continue
        if index + 1 < len(tokens):
            return str(tokens[index + 1])
    return None


def parse_prefect_process_table(raw: str) -> list[dict[str, Any]]:
    """Project exact Prefect CLI processes without retaining their command lines."""
    processes: list[dict[str, Any]] = []
    for line in raw.splitlines():
        # ps columns: pid, ppid, five lstart tokens, then the full command.
        parts = line.strip().split(None, 7)
        if len(parts) < 8:
            continue
        pid_text, ppid_text = parts[0], parts[1]
        command = parts[7]
        try:
            tokens = shlex.split(command)
            pid = int(pid_text)
            ppid = int(ppid_text)
        except (ValueError, TypeError):
            continue
        role, _index = _command_role(tokens)
        if role is None:
            continue
        local_started = None
        try:
            local_started = datetime.strptime(
                " ".join(parts[2:7]), "%a %b %d %H:%M:%S %Y"
            ).astimezone().astimezone(timezone.utc)
        except ValueError:
            pass
        declared_port = _option_value(tokens, "--port") if role == "server" else None
        pool = _option_value(tokens, "--pool", "-p") if role == "worker" else None
        processes.append(
            {
                "pid": pid,
                "ppid": ppid,
                "role": role,
                "started_at": local_started.isoformat() if local_started else None,
                "declared_port": int(declared_port) if declared_port and declared_port.isdigit() else None,
                "work_pool": _safe_name(pool, fallback_prefix="pool") if pool else None,
            }
        )
    return sorted(processes, key=lambda item: (item["role"], item["pid"]))


def _run_lsof(pid: int, *arguments: str) -> str:
    completed = subprocess.run(
        ["lsof", "-nP", "-a", "-p", str(pid), *arguments, "-Fn"],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def _listening_ports(pid: int) -> list[int]:
    raw = _run_lsof(pid, "-iTCP", "-sTCP:LISTEN")
    ports: set[int] = set()
    for line in raw.splitlines():
        if not line.startswith("n"):
            continue
        match = re.search(r":(\d+)$", line)
        if match:
            ports.add(int(match.group(1)))
    return sorted(ports)


def _open_sqlite_paths(pid: int) -> list[Path]:
    paths: set[Path] = set()
    for line in _run_lsof(pid).splitlines():
        if not line.startswith("n/"):
            continue
        raw_path = _SQLITE_SUFFIX.sub("", line[1:])
        if not raw_path.endswith((".db", ".sqlite", ".sqlite3")):
            continue
        paths.add(Path(raw_path).expanduser().resolve())
    return sorted(paths, key=str)


def discover_prefect_processes() -> tuple[list[dict[str, Any]], dict[int, list[Path]]]:
    raw = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,lstart=,command="],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    projected = parse_prefect_process_table(raw)
    database_paths: dict[int, list[Path]] = {}
    for process in projected:
        pid = int(process["pid"])
        process["listening_ports"] = _listening_ports(pid)
        if process["role"] == "server":
            paths = _open_sqlite_paths(pid)
            database_paths[pid] = paths
            process["metadata_database_ids"] = [_database_id(path) for path in paths]
        else:
            process["metadata_database_ids"] = []
    return projected, database_paths


def project_work_pool_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": _safe_name(row.get("name"), fallback_prefix="pool"),
        "type": _safe_name(row.get("type"), fallback_prefix="type"),
        "status": str(row.get("status") or "UNKNOWN"),
        "paused": bool(row.get("is_paused")),
        "concurrency_limit": (
            int(row["concurrency_limit"])
            if row.get("concurrency_limit") is not None
            else None
        ),
    }


def project_work_queue_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": _safe_name(row.get("name"), fallback_prefix="queue"),
        "work_pool": _safe_name(row.get("pool_name"), fallback_prefix="pool"),
        "status": str(row.get("status") or "UNKNOWN"),
        "paused": bool(row.get("is_paused")),
        "priority": int(row.get("priority") or 0),
        "concurrency_limit": (
            int(row["concurrency_limit"])
            if row.get("concurrency_limit") is not None
            else None
        ),
        "last_polled_at": _as_utc(row.get("last_polled")).isoformat()
        if _as_utc(row.get("last_polled"))
        else None,
    }


def project_worker_row(
    row: Mapping[str, Any], *, captured_at: datetime
) -> dict[str, Any]:
    heartbeat = _as_utc(row.get("last_heartbeat_time"))
    interval = int(row.get("heartbeat_interval_seconds") or 0)
    age = max(0.0, (captured_at - heartbeat).total_seconds()) if heartbeat else None
    stale_threshold = max(90, interval * 3) if interval else 90
    raw_name = str(row.get("name") or "")
    return {
        "worker_identity_sha256": sha256(raw_name.encode("utf-8")).hexdigest(),
        "worker_kind": raw_name.split(maxsplit=1)[0] if raw_name else "unknown",
        "work_pool": _safe_name(row.get("pool_name"), fallback_prefix="pool"),
        "status": str(row.get("status") or "UNKNOWN"),
        "last_heartbeat_at": heartbeat.isoformat() if heartbeat else None,
        "heartbeat_interval_seconds": interval or None,
        "heartbeat_age_seconds": _round_seconds(age),
        "heartbeat_stale": age is None or age > stale_threshold,
    }


def _project_schedule(raw: Any) -> dict[str, Any]:
    schedule = _json_object(raw)
    projected: dict[str, Any] = {}
    for key in ("cron", "timezone", "interval", "anchor_date", "day_or"):
        if key in schedule and isinstance(schedule[key], (str, int, float, bool)):
            projected[key] = schedule[key]
    if "cron" in projected:
        projected["kind"] = "cron"
    elif "interval" in projected:
        projected["kind"] = "interval"
    else:
        projected["kind"] = "unknown"
    return projected


def _safe_entrypoint(value: Any) -> str | None:
    if not value:
        return None
    rendered = str(value).replace("\\", "/")
    if ":" not in rendered:
        return None
    path, symbol = rendered.rsplit(":", 1)
    safe_path = Path(path).name if Path(path).is_absolute() else path
    if not _SAFE_IDENTIFIER.fullmatch(safe_path) or not _SAFE_IDENTIFIER.fullmatch(symbol):
        return None
    return f"{safe_path}:{symbol}"


def project_deployment_row(
    row: Mapping[str, Any], *, schedules: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    return {
        "flow_name": _safe_name(row.get("flow_name"), fallback_prefix="flow"),
        "deployment_name": _safe_name(
            row.get("deployment_name"), fallback_prefix="deployment"
        ),
        "work_pool": _safe_name(row.get("pool_name"), fallback_prefix="pool"),
        "work_queue": _safe_name(row.get("queue_name"), fallback_prefix="queue"),
        "status": str(row.get("status") or "UNKNOWN"),
        "paused": bool(row.get("paused")),
        "concurrency_limit": (
            int(row["concurrency_limit"])
            if row.get("concurrency_limit") is not None
            else None
        ),
        "entrypoint": _safe_entrypoint(row.get("entrypoint")),
        "schedules": [dict(item) for item in schedules],
    }


def _logical_task_key(row: Mapping[str, Any]) -> str:
    flow_name = _safe_name(row.get("flow_name"), fallback_prefix="flow")
    deployment_name = _safe_name(
        row.get("deployment_name"), fallback_prefix="deployment"
    )
    parameters = _json_object(row.get("parameters"))
    if flow_name == "scheduled-task":
        return _safe_name(
            parameters.get("task_id") or deployment_name,
            fallback_prefix="scheduled-task",
        )
    return f"{flow_name}/{deployment_name}"


def _durable_identity(row: Mapping[str, Any]) -> tuple[str, str] | None:
    parameters = _json_object(row.get("parameters"))
    flow_name = str(row.get("flow_name") or "")
    field = None
    if flow_name == "recommendation-run":
        field = "run_id"
    elif flow_name.startswith("manual-"):
        field = "task_run_id"
    if field is None or not parameters.get(field):
        return None
    value = str(parameters[field])
    return flow_name, sha256(value.encode("utf-8")).hexdigest()


def _overlap_metrics(
    intervals: Iterable[tuple[datetime, datetime]],
) -> dict[str, int]:
    active_ends: list[datetime] = []
    overlap_pairs = 0
    maximum_concurrency = 0
    for started_at, finished_at in sorted(intervals, key=lambda item: item[0]):
        while active_ends and active_ends[0] <= started_at:
            heapq.heappop(active_ends)
        overlap_pairs += len(active_ends)
        heapq.heappush(active_ends, max(started_at, finished_at))
        maximum_concurrency = max(maximum_concurrency, len(active_ends))
    return {
        "overlap_pairs": overlap_pairs,
        "maximum_concurrency": maximum_concurrency,
    }


def calculate_flow_run_metrics(
    rows: Iterable[Mapping[str, Any]],
    *,
    captured_at: datetime,
    window_start: datetime,
) -> dict[str, Any]:
    values = [dict(row) for row in rows]
    status_counts: Counter[str] = Counter()
    queue_delays: list[float] = []
    raw_start_latencies: list[float] = []
    negative_latency_count = 0
    overdue_ages: list[float] = []
    intervals: list[tuple[datetime, datetime]] = []
    per_task_rows: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    scheduled_occurrences: Counter[tuple[str, str]] = Counter()
    durable_identities: Counter[tuple[str, str]] = Counter()

    for row in values:
        status = str(row.get("state_type") or row.get("state_name") or "unknown").lower()
        status_counts[status] += 1
        expected = _as_utc(row.get("expected_start_time"))
        started = _as_utc(row.get("start_time"))
        finished = _as_utc(row.get("end_time"))
        task_key = _logical_task_key(row)
        per_task_rows[task_key].append({**row, "_status": status})
        if expected and started:
            raw_latency = (started - expected).total_seconds()
            raw_start_latencies.append(raw_latency)
            if raw_latency < 0:
                negative_latency_count += 1
            queue_delays.append(max(0.0, raw_latency))
        if expected and not started and status == "scheduled" and expected < captured_at:
            overdue_ages.append((captured_at - expected).total_seconds())
        if started:
            end = finished or captured_at
            intervals.append((started, max(started, end)))
        if bool(row.get("auto_scheduled")) and expected:
            scheduled_occurrences[(task_key, expected.isoformat())] += 1
        durable = _durable_identity(row)
        if durable is not None:
            durable_identities[durable] += 1

    by_task: list[dict[str, Any]] = []
    total_same_task_overlap_pairs = 0
    for task_key, task_rows in sorted(per_task_rows.items()):
        task_intervals: list[tuple[datetime, datetime]] = []
        task_delays: list[float] = []
        task_statuses: Counter[str] = Counter()
        for row in task_rows:
            task_statuses[str(row["_status"])] += 1
            expected = _as_utc(row.get("expected_start_time"))
            started = _as_utc(row.get("start_time"))
            finished = _as_utc(row.get("end_time"))
            if expected and started:
                task_delays.append(max(0.0, (started - expected).total_seconds()))
            if started:
                task_intervals.append((started, max(started, finished or captured_at)))
        overlap = _overlap_metrics(task_intervals)
        total_same_task_overlap_pairs += overlap["overlap_pairs"]
        by_task.append(
            {
                "task_key": task_key,
                "run_count": len(task_rows),
                "status_counts": dict(sorted(task_statuses.items())),
                "maximum_queue_delay_seconds": _round_seconds(max(task_delays))
                if task_delays
                else None,
                **overlap,
            }
        )

    duplicate_occurrences = {
        key: count for key, count in scheduled_occurrences.items() if count > 1
    }
    duplicate_by_task: Counter[str] = Counter()
    for (task_key, _scheduled_for), _count in duplicate_occurrences.items():
        duplicate_by_task[task_key] += 1
    duplicate_durable = {
        key: count for key, count in durable_identities.items() if count > 1
    }
    duplicate_durable_by_flow: Counter[str] = Counter()
    for (flow_name, _identity), _count in duplicate_durable.items():
        duplicate_durable_by_flow[flow_name] += 1

    return {
        "window": {
            "started_at": window_start.isoformat(),
            "ended_at": captured_at.isoformat(),
            "duration_days": round(
                (captured_at - window_start).total_seconds() / 86400, 6
            ),
        },
        "flow_run_count": len(values),
        "status_counts": dict(sorted(status_counts.items())),
        "failed_count": int(status_counts.get("failed", 0)),
        "crashed_count": int(status_counts.get("crashed", 0)),
        "queue_delay_seconds": {
            "started_run_count": len(queue_delays),
            "mean": _round_seconds(sum(queue_delays) / len(queue_delays))
            if queue_delays
            else None,
            "p50": _round_seconds(_nearest_rank(queue_delays, 0.50)),
            "p95": _round_seconds(_nearest_rank(queue_delays, 0.95)),
            "p99": _round_seconds(_nearest_rank(queue_delays, 0.99)),
            "maximum": _round_seconds(max(queue_delays)) if queue_delays else None,
        },
        "maximum_start_latency_seconds": _round_seconds(max(raw_start_latencies))
        if raw_start_latencies
        else None,
        "negative_start_latency_count": negative_latency_count,
        "overdue_unstarted": {
            "count": len(overdue_ages),
            "maximum_age_seconds": _round_seconds(max(overdue_ages))
            if overdue_ages
            else None,
        },
        "concurrency_and_overlap": {
            "global": _overlap_metrics(intervals),
            "same_task_overlap_pairs": total_same_task_overlap_pairs,
            "by_task": by_task,
        },
        "duplicates": {
            "scheduled_occurrence_group_count": len(duplicate_occurrences),
            "scheduled_occurrence_excess_run_count": sum(
                count - 1 for count in duplicate_occurrences.values()
            ),
            "scheduled_occurrence_groups_by_task": dict(
                sorted(duplicate_by_task.items())
            ),
            "durable_identity_group_count": len(duplicate_durable),
            "durable_identity_excess_run_count": sum(
                count - 1 for count in duplicate_durable.values()
            ),
            "durable_identity_groups_by_flow": dict(
                sorted(duplicate_durable_by_flow.items())
            ),
        },
    }


def calculate_scheduler_run_metrics(
    rows: Iterable[Mapping[str, Any]],
    *,
    captured_at: datetime,
    window_start: datetime,
) -> dict[str, Any]:
    values = [dict(row) for row in rows]
    statuses: Counter[str] = Counter()
    per_task: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    date_groups: Counter[tuple[str, str]] = Counter()
    intervals: list[tuple[datetime, datetime]] = []

    for row in values:
        status = str(row.get("status") or "unknown").lower()
        statuses[status] += 1
        task_id = _safe_name(row.get("task_id"), fallback_prefix="task")
        per_task[task_id].append({**row, "_status": status})
        started = _as_utc(row.get("started_at"))
        finished = _as_utc(row.get("finished_at"))
        if started:
            business_date_proxy = started.astimezone(BEIJING).date().isoformat()
            date_groups[(task_id, business_date_proxy)] += 1
            intervals.append((started, max(started, finished or captured_at)))

    by_task: list[dict[str, Any]] = []
    same_task_overlap_pairs = 0
    for task_id, task_rows in sorted(per_task.items()):
        task_intervals: list[tuple[datetime, datetime]] = []
        task_statuses: Counter[str] = Counter()
        for row in task_rows:
            task_statuses[str(row["_status"])] += 1
            started = _as_utc(row.get("started_at"))
            finished = _as_utc(row.get("finished_at"))
            if started:
                task_intervals.append((started, max(started, finished or captured_at)))
        overlap = _overlap_metrics(task_intervals)
        same_task_overlap_pairs += overlap["overlap_pairs"]
        by_task.append(
            {
                "task_id": task_id,
                "run_count": len(task_rows),
                "status_counts": dict(sorted(task_statuses.items())),
                **overlap,
            }
        )

    multi_run_groups = {
        key: count for key, count in date_groups.items() if count > 1
    }
    multi_by_task: Counter[str] = Counter()
    for (task_id, _business_date), _count in multi_run_groups.items():
        multi_by_task[task_id] += 1

    return {
        "window": {
            "started_at": window_start.isoformat(),
            "ended_at": captured_at.isoformat(),
        },
        "run_count": len(values),
        "status_counts": dict(sorted(statuses.items())),
        "failed_count": int(statuses.get("failed", 0)),
        "partial_failed_count": int(statuses.get("partial_failed", 0)),
        "interrupted_count": int(statuses.get("interrupted", 0)),
        "concurrency_and_overlap": {
            "global": _overlap_metrics(intervals),
            "same_task_overlap_pairs": same_task_overlap_pairs,
            "by_task": by_task,
        },
        "duplicate_proxy": {
            "definition": (
                "same task_id and Asia/Shanghai start date; this is a multi-run proxy, "
                "not an authoritative scheduled-occurrence duplicate"
            ),
            "group_count": len(multi_run_groups),
            "excess_run_count": sum(count - 1 for count in multi_run_groups.values()),
            "groups_by_task": dict(sorted(multi_by_task.items())),
        },
        "queue_delay": {
            "status": "unavailable",
            "reason": "scheduled_task_runs has no scheduled_for column",
        },
    }


def build_business_identity_contracts(
    task_types: Iterable[str],
) -> dict[str, Any]:
    contracts: list[dict[str, Any]] = []
    for task_type in sorted(set(task_types)):
        definition = _SCHEDULER_IDENTITY_CONTRACTS.get(task_type)
        if definition is None:
            definition = {
                "business_date": "undefined in the current scheduler",
                "scope": "task_id only; business scope is undefined",
                "cardinality": "unknown",
            }
            status = "undefined"
        else:
            status = "defined_but_not_first_class"
        contracts.append(
            {
                "task_type": task_type,
                **definition,
                "current_materialization": status,
                "canonical_dimensions": [
                    "task_type",
                    "business_date",
                    "scope",
                    "scheduled_for",
                ],
            }
        )
    return {
        "canonical_dimensions": [
            "task_type",
            "business_date",
            "scope",
            "scheduled_for",
        ],
        "current_schema": {
            "scheduled_task_runs_has_business_date": False,
            "scheduled_task_runs_has_scope": False,
            "scheduled_task_runs_has_scheduled_for": False,
            "exact_business_occurrence_duplicate_measurement": False,
        },
        "scheduler_task_types": contracts,
        "prefect_flow_types": [dict(item) for item in _PREFECT_FLOW_IDENTITY_CONTRACTS],
    }


def _sqlite_rows(connection: sqlite3.Connection, sql: str, parameters: Sequence[Any] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(sql, parameters).fetchall()]


def collect_prefect_metadata(
    path: Path,
    *,
    captured_at: datetime,
    window_start: datetime,
    owner_server_pids: Sequence[int],
) -> dict[str, Any]:
    uri = f"{path.expanduser().resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        required = {"flow_run", "work_pool", "work_queue", "worker", "deployment"}
        missing = sorted(required - tables)
        if missing:
            raise ValueError(f"not a Prefect metadata database; missing tables: {missing}")

        revisions = (
            [str(row[0]) for row in connection.execute("SELECT version_num FROM alembic_version")]
            if "alembic_version" in tables
            else []
        )
        pools = [
            project_work_pool_row(row)
            for row in _sqlite_rows(
                connection,
                """
                SELECT name, type, is_paused, concurrency_limit, status
                FROM work_pool
                ORDER BY name
                """,
            )
        ]
        queues = [
            project_work_queue_row(row)
            for row in _sqlite_rows(
                connection,
                """
                SELECT q.name, p.name AS pool_name, q.is_paused,
                       q.concurrency_limit, q.priority, q.status, q.last_polled
                FROM work_queue q
                LEFT JOIN work_pool p ON p.id = q.work_pool_id
                ORDER BY p.name, q.priority, q.name
                """,
            )
        ]
        workers = [
            project_worker_row(row, captured_at=captured_at)
            for row in _sqlite_rows(
                connection,
                """
                SELECT w.name, p.name AS pool_name, w.status,
                       w.last_heartbeat_time, w.heartbeat_interval_seconds
                FROM worker w
                LEFT JOIN work_pool p ON p.id = w.work_pool_id
                ORDER BY p.name, w.name
                """,
            )
        ]

        schedule_by_deployment: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        if "deployment_schedule" in tables:
            schedule_rows = _sqlite_rows(
                connection,
                """
                SELECT deployment_id, schedule, active, max_scheduled_runs
                FROM deployment_schedule
                ORDER BY deployment_id, created
                """,
            )
            for row in schedule_rows:
                schedule_by_deployment[str(row["deployment_id"])].append(
                    {
                        "active": bool(row.get("active")),
                        "max_scheduled_runs": row.get("max_scheduled_runs"),
                        "schedule": _project_schedule(row.get("schedule")),
                    }
                )
        deployment_rows = _sqlite_rows(
            connection,
            """
            SELECT d.id, f.name AS flow_name, d.name AS deployment_name,
                   p.name AS pool_name, q.name AS queue_name, d.paused,
                   d.status, d.concurrency_limit, d.entrypoint
            FROM deployment d
            LEFT JOIN flow f ON f.id = d.flow_id
            LEFT JOIN work_queue q ON q.id = d.work_queue_id
            LEFT JOIN work_pool p ON p.id = q.work_pool_id
            ORDER BY f.name, d.name
            """,
        )
        deployments = [
            project_deployment_row(
                row,
                schedules=schedule_by_deployment.get(str(row["id"]), []),
            )
            for row in deployment_rows
        ]

        flow_rows = _sqlite_rows(
            connection,
            """
            SELECT fr.id, f.name AS flow_name, d.name AS deployment_name,
                   fr.state_type, fr.state_name, fr.expected_start_time,
                   fr.start_time, fr.end_time, fr.auto_scheduled, fr.parameters
            FROM flow_run fr
            LEFT JOIN flow f ON f.id = fr.flow_id
            LEFT JOIN deployment d ON d.id = fr.deployment_id
            WHERE fr.expected_start_time IS NOT NULL
              AND datetime(fr.expected_start_time) >= datetime(?)
            ORDER BY fr.expected_start_time, fr.id
            """,
            (window_start.isoformat(),),
        )
        runtime = calculate_flow_run_metrics(
            flow_rows,
            captured_at=captured_at,
            window_start=window_start,
        )
        return {
            "metadata_database_id": _database_id(path),
            "dialect": "sqlite",
            "sqlite_version": sqlite3.sqlite_version,
            "schema_revisions": sorted(revisions),
            "owner_server_pids": sorted(int(pid) for pid in owner_server_pids),
            "work_pools": pools,
            "work_queues": queues,
            "workers": workers,
            "deployments": deployments,
            "concurrency": {
                "work_pool_with_limit_count": sum(
                    item["concurrency_limit"] is not None for item in pools
                ),
                "work_pool_without_limit_count": sum(
                    item["concurrency_limit"] is None for item in pools
                ),
                "work_queue_with_limit_count": sum(
                    item["concurrency_limit"] is not None for item in queues
                ),
                "work_queue_without_limit_count": sum(
                    item["concurrency_limit"] is None for item in queues
                ),
                "deployment_with_limit_count": sum(
                    item["concurrency_limit"] is not None for item in deployments
                ),
                "deployment_without_limit_count": sum(
                    item["concurrency_limit"] is None for item in deployments
                ),
                "observed_flow_run_maximum": runtime["concurrency_and_overlap"][
                    "global"
                ]["maximum_concurrency"],
            },
            "runtime": runtime,
        }
    finally:
        connection.close()


def collect_scheduler_inventory(
    db: Session,
    *,
    captured_at: datetime,
    window_start: datetime,
) -> dict[str, Any]:
    task_rows = db.execute(
        select(
            ScheduledTask.task_id,
            ScheduledTask.task_type,
            ScheduledTask.cron_expression,
            ScheduledTask.enabled,
            ScheduledTask.is_running,
            ScheduledTask.last_run_at,
            ScheduledTask.last_status,
            ScheduledTask.created_at,
            ScheduledTask.updated_at,
        ).order_by(ScheduledTask.task_id)
    ).all()
    tasks = [
        {
            "task_id": _safe_name(row.task_id, fallback_prefix="task"),
            "task_type": _safe_name(row.task_type, fallback_prefix="task-type"),
            "cron_expression": str(row.cron_expression),
            "enabled": bool(row.enabled),
            "is_running": bool(row.is_running),
            "last_run_at": _as_utc(row.last_run_at).isoformat()
            if _as_utc(row.last_run_at)
            else None,
            "last_status": row.last_status,
            "created_at": _as_utc(row.created_at).isoformat()
            if _as_utc(row.created_at)
            else None,
            "updated_at": _as_utc(row.updated_at).isoformat()
            if _as_utc(row.updated_at)
            else None,
        }
        for row in task_rows
    ]

    all_status_rows = db.execute(
        select(ScheduledTaskRun.status, func.count())
        .group_by(ScheduledTaskRun.status)
        .order_by(ScheduledTaskRun.status)
    ).all()
    run_rows = db.execute(
        select(
            ScheduledTaskRun.task_id,
            ScheduledTaskRun.task_type,
            ScheduledTaskRun.trigger_source,
            ScheduledTaskRun.started_at,
            ScheduledTaskRun.finished_at,
            ScheduledTaskRun.status,
        )
        .where(ScheduledTaskRun.started_at >= window_start.replace(tzinfo=None))
        .order_by(ScheduledTaskRun.started_at, ScheduledTaskRun.task_run_id)
    ).all()
    runtime_rows = [
        {
            "task_id": row.task_id,
            "task_type": row.task_type,
            "trigger_source": row.trigger_source,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "status": row.status,
        }
        for row in run_rows
    ]
    task_types = {str(row.task_type) for row in task_rows}
    task_types.add("preset_matrix_run")
    return {
        "tasks": tasks,
        "task_count": len(tasks),
        "enabled_task_count": sum(item["enabled"] for item in tasks),
        "disabled_task_count": sum(not item["enabled"] for item in tasks),
        "all_time_status_counts": {
            str(status): int(count) for status, count in all_status_rows
        },
        "runtime": calculate_scheduler_run_metrics(
            runtime_rows,
            captured_at=captured_at,
            window_start=window_start,
        ),
        "business_identity": build_business_identity_contracts(task_types),
        "omitted_fields": [
            "ScheduledTask.payload_json",
            "ScheduledTask.last_error",
            "ScheduledTaskRun.payload_json",
            "ScheduledTaskRun.result_json",
            "ScheduledTaskRun.summary",
            "ScheduledTaskRun.error_message",
        ],
    }


def _start_read_only_transaction(db: Session) -> str:
    dialect = db.get_bind().dialect.name
    if dialect == "mysql":
        db.execute(text("SET TRANSACTION READ ONLY"))
    return str(dialect)


def _candidate_prefect_databases(
    explicit_paths: Iterable[Path],
    process_database_paths: Mapping[int, Sequence[Path]],
) -> list[Path]:
    candidates = {DEFAULT_PREFECT_DB.expanduser().resolve()}
    candidates.update(path.expanduser().resolve() for path in explicit_paths)
    for paths in process_database_paths.values():
        candidates.update(path.expanduser().resolve() for path in paths)
    return sorted((path for path in candidates if path.exists()), key=str)


def _control_plane_summary(
    processes: Sequence[Mapping[str, Any]],
    databases: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    servers = [item for item in processes if item.get("role") == "server"]
    worker_processes = [item for item in processes if item.get("role") == "worker"]
    owner_conflicts = [
        item["metadata_database_id"]
        for item in databases
        if len(item.get("owner_server_pids") or []) > 1
    ]
    maximum_latency = [
        item["runtime"].get("maximum_start_latency_seconds")
        for item in databases
        if item["runtime"].get("maximum_start_latency_seconds") is not None
    ]
    return {
        "server_process_count": len(servers),
        "worker_process_count": len(worker_processes),
        "metadata_database_count": len(databases),
        "work_pool_count": sum(len(item["work_pools"]) for item in databases),
        "work_queue_count": sum(len(item["work_queues"]) for item in databases),
        "worker_registration_count": sum(len(item["workers"]) for item in databases),
        "online_worker_registration_count": sum(
            str(worker.get("status")).upper() == "ONLINE"
            for item in databases
            for worker in item["workers"]
        ),
        "deployment_count": sum(len(item["deployments"]) for item in databases),
        "metadata_database_owner_conflict_count": len(owner_conflicts),
        "metadata_database_owner_conflict_ids": sorted(owner_conflicts),
        "maximum_start_latency_seconds": _round_seconds(max(maximum_latency))
        if maximum_latency
        else None,
    }


def collect_baseline(
    db: Session,
    *,
    captured_at: datetime,
    window_days: int,
    explicit_prefect_databases: Iterable[Path] = (),
) -> dict[str, Any]:
    window_start = captured_at - timedelta(days=window_days)
    processes, process_database_paths = discover_prefect_processes()
    database_paths = _candidate_prefect_databases(
        explicit_prefect_databases, process_database_paths
    )
    if not database_paths:
        raise RuntimeError("no local Prefect metadata database was discovered")

    owner_pids_by_database: defaultdict[str, list[int]] = defaultdict(list)
    for process in processes:
        if process["role"] != "server":
            continue
        for database_id in process.get("metadata_database_ids") or []:
            owner_pids_by_database[str(database_id)].append(int(process["pid"]))

    metadata_databases = [
        collect_prefect_metadata(
            path,
            captured_at=captured_at,
            window_start=window_start,
            owner_server_pids=owner_pids_by_database.get(_database_id(path), []),
        )
        for path in database_paths
    ]
    scheduler = collect_scheduler_inventory(
        db,
        captured_at=captured_at,
        window_start=window_start,
    )
    payload: dict[str, Any] = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "task": "7.1 固化当前控制面和 occurrence 基线",
        "spec": "production-evidence-risk-hardening",
        "requirements": ["7.1", "7.2", "7.9"],
        "captured_at": captured_at.isoformat(),
        "capture_semantics": {
            "observation_window_days": window_days,
            "business_database_access": "single read-only transaction rolled back on exit",
            "prefect_database_access": "SQLite URI mode=ro with PRAGMA query_only",
            "process_access": "allowlisted projection from ps/lsof; commands and environments omitted",
            "payload_policy": "allowlisted projections and aggregate metrics only",
            "full_json_columns_emitted": False,
            "safety_check": "fail closed before artifact write",
        },
        "repository": repository_identity(),
        "repository_control_contract": {
            "installed_prefect_version": str(prefect.__version__),
            "declared_work_pool": WORK_POOL_NAME,
            "server_launcher": "backend/scripts/run_prefect_server.py",
            "worker_launcher": "backend/scripts/run_prefect_worker.py",
            "worker_concurrency_limit_declared": False,
            "scheduler_duplicate_guard": "process-local set plus non-atomic ScheduledTask.is_running boolean",
            "database_occurrence_claim_present": False,
            "control_plane_owner_lease_present": False,
            "baseline_only": True,
        },
        "processes": {
            "servers": [item for item in processes if item["role"] == "server"],
            "workers": [item for item in processes if item["role"] == "worker"],
        },
        "metadata_databases": metadata_databases,
        "scheduler": scheduler,
    }
    payload["control_plane_summary"] = _control_plane_summary(
        processes, metadata_databases
    )
    payload["section_digests"] = {
        key: _digest(payload[key])
        for key in (
            "repository",
            "repository_control_contract",
            "processes",
            "metadata_databases",
            "scheduler",
            "control_plane_summary",
        )
    }
    return payload


def write_baseline(
    output: Path,
    *,
    prefect_databases: Iterable[Path] = (),
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> dict[str, Any]:
    if window_days < 1 or window_days > 90:
        raise ValueError("window_days must be between 1 and 90")
    captured_at = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        dialect = _start_read_only_transaction(db)
        payload = collect_baseline(
            db,
            captured_at=captured_at,
            window_days=window_days,
            explicit_prefect_databases=prefect_databases,
        )
        payload["capture_semantics"]["business_database_dialect"] = dialect
        assert_safe_output(
            payload,
            forbidden_literals=_forbidden_literals_from_settings(),
        )
        db.rollback()
    finally:
        db.close()

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(f"{output.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, output)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture a read-only, allowlisted Prefect/scheduler baseline."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--prefect-db",
        action="append",
        type=Path,
        default=[],
        help="Additional local Prefect SQLite metadata database (repeatable).",
    )
    parser.add_argument(
        "--window-days", type=int, default=DEFAULT_WINDOW_DAYS
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = write_baseline(
        args.output.resolve(),
        prefect_databases=args.prefect_db,
        window_days=args.window_days,
    )
    summary = payload["control_plane_summary"]
    print(
        json.dumps(
            {
                "output": str(args.output),
                "schema_version": payload["schema_version"],
                "servers": summary["server_process_count"],
                "metadata_databases": summary["metadata_database_count"],
                "work_pools": summary["work_pool_count"],
                "work_queues": summary["work_queue_count"],
                "workers": summary["worker_registration_count"],
                "scheduler_tasks": payload["scheduler"]["task_count"],
                "safety_check": "passed",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
