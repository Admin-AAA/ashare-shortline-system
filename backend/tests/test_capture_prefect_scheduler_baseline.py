from __future__ import annotations

from datetime import datetime, timedelta, timezone
import random
import string

from scripts.capture_prefect_scheduler_baseline import (
    build_business_identity_contracts,
    calculate_flow_run_metrics,
    calculate_scheduler_run_metrics,
    parse_prefect_process_table,
    project_work_pool_row,
    project_worker_row,
)
from scripts.capture_production_evidence_baseline import assert_safe_output


def test_process_projection_recognizes_exact_prefect_commands_only() -> None:
    raw = "\n".join(
        [
            "68013 1 Sun Aug  3 11:00:00 2026 /opt/python -m prefect server start --port 4200",
            "73032 1 Sun Aug  3 11:01:00 2026 /opt/prefect worker start --pool ashare-local",
            (
                "99999 1 Sun Aug  3 11:02:00 2026 /opt/python -c "
                "'text mentioning prefect server start is not a server process'"
            ),
        ]
    )

    projected = parse_prefect_process_table(raw)

    assert [(item["pid"], item["role"]) for item in projected] == [
        (68013, "server"),
        (73032, "worker"),
    ]
    assert projected[0]["declared_port"] == 4200
    assert projected[1]["work_pool"] == "ashare-local"
    assert all("command" not in item for item in projected)


def test_flow_runtime_metrics_capture_delay_failure_duplicates_and_overlap() -> None:
    captured_at = datetime(2026, 8, 3, 12, tzinfo=timezone.utc)
    window_start = captured_at - timedelta(days=7)
    expected = captured_at - timedelta(hours=4)
    rows = [
        {
            "flow_name": "scheduled-task",
            "deployment_name": "post_sync_formal_recommendation",
            "state_type": "COMPLETED",
            "expected_start_time": expected,
            "start_time": expected + timedelta(seconds=10),
            "end_time": expected + timedelta(minutes=5),
            "auto_scheduled": True,
            "parameters": '{"task_id":"post_sync_formal_recommendation"}',
        },
        {
            "flow_name": "scheduled-task",
            "deployment_name": "post_sync_formal_recommendation",
            "state_type": "FAILED",
            "expected_start_time": expected,
            "start_time": expected + timedelta(seconds=20),
            "end_time": expected + timedelta(minutes=4),
            "auto_scheduled": True,
            "parameters": '{"task_id":"post_sync_formal_recommendation"}',
        },
        {
            "flow_name": "stale-run-recovery",
            "deployment_name": "default",
            "state_type": "CRASHED",
            "expected_start_time": expected + timedelta(hours=1),
            "start_time": expected + timedelta(hours=1, seconds=60),
            "end_time": expected + timedelta(hours=1, minutes=2),
            "auto_scheduled": True,
            "parameters": "{}",
        },
        {
            "flow_name": "intraday-lock-poll",
            "deployment_name": "default",
            "state_type": "SCHEDULED",
            "expected_start_time": captured_at - timedelta(minutes=30),
            "start_time": None,
            "end_time": None,
            "auto_scheduled": True,
            "parameters": "{}",
        },
        {
            "flow_name": "recommendation-run",
            "deployment_name": "default",
            "state_type": "COMPLETED",
            "expected_start_time": expected + timedelta(hours=2),
            "start_time": expected + timedelta(hours=2, seconds=5),
            "end_time": expected + timedelta(hours=2, minutes=3),
            "auto_scheduled": False,
            "parameters": '{"run_id":"run_same"}',
        },
        {
            "flow_name": "recommendation-run",
            "deployment_name": "default",
            "state_type": "COMPLETED",
            "expected_start_time": expected + timedelta(hours=2, seconds=1),
            "start_time": expected + timedelta(hours=2, seconds=6),
            "end_time": expected + timedelta(hours=2, minutes=2),
            "auto_scheduled": False,
            "parameters": '{"run_id":"run_same"}',
        },
    ]

    metrics = calculate_flow_run_metrics(
        rows,
        captured_at=captured_at,
        window_start=window_start,
    )

    assert metrics["flow_run_count"] == 6
    assert metrics["failed_count"] == 1
    assert metrics["crashed_count"] == 1
    assert metrics["maximum_start_latency_seconds"] == 60.0
    assert metrics["queue_delay_seconds"]["maximum"] == 60.0
    assert metrics["overdue_unstarted"] == {
        "count": 1,
        "maximum_age_seconds": 1800.0,
    }
    assert metrics["duplicates"]["scheduled_occurrence_group_count"] == 1
    assert metrics["duplicates"]["scheduled_occurrence_excess_run_count"] == 1
    assert metrics["duplicates"]["durable_identity_group_count"] == 1
    assert metrics["duplicates"]["durable_identity_excess_run_count"] == 1
    assert metrics["concurrency_and_overlap"]["same_task_overlap_pairs"] == 2
    assert metrics["concurrency_and_overlap"]["global"]["maximum_concurrency"] == 2


def test_scheduler_runtime_marks_overlap_and_duplicate_measurement_as_proxy() -> None:
    captured_at = datetime(2026, 8, 3, 12, tzinfo=timezone.utc)
    window_start = captured_at - timedelta(days=7)
    started = captured_at - timedelta(hours=3)
    rows = [
        {
            "task_id": "formal_run_safety_net",
            "task_type": "formal_safety_net",
            "status": "failed",
            "started_at": started,
            "finished_at": started + timedelta(hours=1),
        },
        {
            "task_id": "formal_run_safety_net",
            "task_type": "formal_safety_net",
            "status": "completed",
            "started_at": started + timedelta(minutes=30),
            "finished_at": started + timedelta(hours=2),
        },
        {
            "task_id": "strategy_exposure_run",
            "task_type": "strategy_exposure_run",
            "status": "interrupted",
            "started_at": started + timedelta(minutes=10),
            "finished_at": started + timedelta(minutes=20),
        },
    ]

    metrics = calculate_scheduler_run_metrics(
        rows,
        captured_at=captured_at,
        window_start=window_start,
    )

    assert metrics["run_count"] == 3
    assert metrics["failed_count"] == 1
    assert metrics["interrupted_count"] == 1
    assert metrics["concurrency_and_overlap"]["same_task_overlap_pairs"] == 1
    assert metrics["duplicate_proxy"]["group_count"] == 1
    assert metrics["duplicate_proxy"]["excess_run_count"] == 1
    assert metrics["queue_delay"]["status"] == "unavailable"


def test_every_current_scheduler_task_type_has_an_explicit_business_identity() -> None:
    current_types = {
        "recommendation_run",
        "formal_safety_net",
        "strategy_exposure_run",
        "intraday_lock_shadow",
        "data_sync",
        "data_backfill",
        "pool_refresh",
        "outcome_label",
        "preset_matrix_run",
    }

    result = build_business_identity_contracts(current_types)
    contracts = {
        item["task_type"]: item for item in result["scheduler_task_types"]
    }

    assert set(contracts) == current_types
    assert all(
        item["current_materialization"] == "defined_but_not_first_class"
        for item in contracts.values()
    )
    assert all(item["business_date"] and item["scope"] for item in contracts.values())
    assert result["current_schema"] == {
        "scheduled_task_runs_has_business_date": False,
        "scheduled_task_runs_has_scope": False,
        "scheduled_task_runs_has_scheduled_for": False,
        "exact_business_occurrence_duplicate_measurement": False,
    }


def test_allowlist_property_drops_arbitrary_control_plane_columns() -> None:
    """Generative property: unknown metadata columns never survive projections."""
    rng = random.Random(20260803)
    alphabet = string.ascii_lowercase
    captured_at = datetime(2026, 8, 3, 12, tzinfo=timezone.utc)
    for _ in range(200):
        unknown_key = "unknown_" + "".join(
            rng.choice(alphabet) for _ in range(16)
        )
        pool = project_work_pool_row(
            {
                "name": "ashare-local",
                "type": "process",
                "status": "READY",
                "is_paused": False,
                "concurrency_limit": None,
                unknown_key: "arbitrary payload",
            }
        )
        worker = project_worker_row(
            {
                "name": "ProcessWorker fixture",
                "pool_name": "ashare-local",
                "status": "ONLINE",
                "last_heartbeat_time": captured_at,
                "heartbeat_interval_seconds": 30,
                unknown_key: "arbitrary payload",
            },
            captured_at=captured_at,
        )
        assert unknown_key not in pool
        assert unknown_key not in worker
        assert set(pool) == {
            "name",
            "type",
            "status",
            "paused",
            "concurrency_limit",
        }
        assert "fixture" not in str(worker)
        assert_safe_output({"pool": pool, "worker": worker})
