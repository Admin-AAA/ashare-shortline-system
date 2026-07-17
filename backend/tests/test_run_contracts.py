"""Domain tests for the new versioned run request and lifecycle contracts.

These deliberately use no database fixture: repository implementations are wired in a
later migration task, while the contract must remain deterministic and reusable by
API, scheduler and worker adapters.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.runs.contracts import (
    DataSourceId,
    RUN_REQUEST_SCHEMA_VERSION,
    RunActor,
    RunContractError,
    RunErrorCode,
    RunMode,
    RunPhase,
    RunRequest,
    RunStatus,
    RunTrigger,
    canonical_request_json,
    parse_run_request,
)
from app.services.runs.repository import RunCheckpoint, RunLease
from app.services.runs.state_machine import ResumeCheck, RunStateMachine, RunStateTransitionError


def test_request_canonical_json_orders_keys_skills_and_explicit_defaults():
    request = parse_run_request(
        {
            "run_name": "  契约测试  ",
            "run_mode": "experiment",
            "skills": [
                {"skill_id": "zeta", "weight": 0.5, "sort_order": 1},
                {"skill_id": "alpha", "weight": 1.0, "sort_order": 1},
            ],
        }
    )

    assert request.run_mode is RunMode.EXPERIMENT
    assert [item.skill_id for item in request.skills] == ["alpha", "zeta"]
    canonical = request.to_canonical_json()
    assert canonical == canonical_request_json(request)
    assert '"data_source":"local_pool_preferred"' in canonical
    assert canonical.index('"agent_review_concurrency"') < canonical.index('"run_mode"')
    with pytest.raises(Exception):
        request.run_name = "不可修改"  # type: ignore[misc]


def test_request_upgrades_legacy_payload_and_rejects_credential_fields():
    upgraded = parse_run_request(
        {
            "mode": "backtest",
            "name": "旧任务",
            "data_source_mode": "local_pool",
            "single_skill_id": "chip-single-peak-breakout",
            "single_skill_weight": 2.0,
        }
    )
    assert upgraded.schema_version == RunRequest.CURRENT_SCHEMA_VERSION
    assert upgraded.run_mode is RunMode.BACKTEST
    assert upgraded.data_source.value == "local_pool"
    assert upgraded.skills[0].model_dump() == {
        "skill_id": "chip-single-peak-breakout",
        "weight": 2.0,
        "sort_order": 0,
    }

    with pytest.raises(RunContractError) as exc_info:
        parse_run_request({"run_name": "不安全", "api_token": "do-not-store"})
    assert exc_info.value.code == RunErrorCode.CREDENTIAL_IN_REQUEST_FORBIDDEN.value

    with pytest.raises(RunContractError) as exc_info:
        parse_run_request({"schema_version": 99})
    assert exc_info.value.code == RunErrorCode.UNSUPPORTED_REQUEST_SCHEMA.value




def test_v1_defaults_are_explicit_and_parser_rejects_implicit_type_coercion():
    request = parse_run_request({})

    assert request.schema_version == RUN_REQUEST_SCHEMA_VERSION
    assert request.run_mode is RunMode.FORMAL
    assert request.data_source is DataSourceId.LOCAL_POOL_PREFERRED
    assert request.run_name == "临时运行"
    assert request.score_line == 72.0
    assert request.output_count == 6
    assert request.skills == ()
    assert '"trade_date_override":null' in request.to_canonical_json()

    invalid_payloads = (
        {"score_line": "72"},
        {"output_count": True},
        {"skip_aux_refresh": "false"},
        {"trade_date_override": "2026-02-30"},
        {"skills": [{"skill_id": "alpha", "weight": 0}]},
    )
    for payload in invalid_payloads:
        with pytest.raises(RunContractError) as exc_info:
            parse_run_request(payload)
        assert exc_info.value.code == RunErrorCode.INVALID_REQUEST_SCHEMA.value


def test_v0_upgrader_maps_legacy_run_fields_without_dropping_identity():
    request = parse_run_request(
        {
            "mode": "experiment",
            "name": "旧调度任务",
            "preset": {"preset_id": "preset-old", "name": "仅展示字段"},
            "risk": "aggressive",
            "review": True,
            "refresh": False,
            "trade_date": "2026-07-14",
            "selected_skill_ids": ["zeta", "alpha"],
        }
    )

    assert request.schema_version == RUN_REQUEST_SCHEMA_VERSION
    assert request.run_mode is RunMode.EXPERIMENT
    assert request.preset_id == "preset-old"
    assert request.risk_template == "aggressive"
    assert request.disable_agent_review is False
    assert request.skip_aux_refresh is True
    assert request.trade_date_override == "2026-07-14"
    assert [item.skill_id for item in request.skills] == ["alpha", "zeta"]

    backtest = parse_run_request({"backtest": True, "trade_date": "2026-07-14"})
    assert backtest.run_mode is RunMode.BACKTEST
    assert backtest.trade_date_override == "2026-07-14"


def test_parser_accepts_canonical_json_from_any_entry_point():
    payload = {"run_mode": "backtest", "data_source": "local_pool", "skills": ["not-valid"]}
    with pytest.raises(RunContractError):
        parse_run_request(payload)

    canonical = canonical_request_json({"run_mode": "backtest", "data_source": "local_pool"})
    assert parse_run_request(canonical.encode("utf-8")).run_mode is RunMode.BACKTEST
    assert canonical == canonical_request_json(canonical)


def test_legacy_credential_keys_are_rejected_before_upgrade_projection():
    with pytest.raises(RunContractError) as exc_info:
        parse_run_request(
            {
                "selected_skills": [{"skill_id": "alpha", "api-token": "do-not-store"}],
            }
        )
    assert exc_info.value.code == RunErrorCode.CREDENTIAL_IN_REQUEST_FORBIDDEN.value
    with pytest.raises(RunStateTransitionError) as exc_info:
        RunStateMachine.transition(
            current_status=RunStatus.COMPLETED,
            trigger=RunTrigger.RESUME,
            actor=RunActor.OPERATOR,
        )
    assert exc_info.value.code is RunErrorCode.INVALID_STATUS_TRANSITION

    with pytest.raises(RunStateTransitionError) as exc_info:
        RunStateMachine.transition(
            current_status=RunStatus.RUNNING,
            trigger=RunTrigger.COMPLETE,
            actor=RunActor.WORKER,
            last_committed_phase=RunPhase.AGGREGATION,
            side_effect_key="aggregate:run-1",
        )
    assert exc_info.value.code is RunErrorCode.COMPLETION_REQUIRES_PERSISTENCE_CHECKPOINT

    completed = RunStateMachine.transition(
        current_status=RunStatus.RUNNING,
        trigger=RunTrigger.COMPLETE,
        actor=RunActor.WORKER,
        last_committed_phase=RunPhase.PERSISTENCE,
        side_effect_key="persist:run-1",
    )
    assert completed.target_status is RunStatus.COMPLETED
    assert completed.target_phase is RunPhase.COMPLETED


def test_resume_and_checkpoint_sequence_require_exclusive_verified_state():
    active_resume = ResumeCheck(RunPhase.REVIEW, "review:run-1", has_active_lease=True)
    with pytest.raises(RunStateTransitionError) as exc_info:
        RunStateMachine.validate_resume(active_resume)
    assert exc_info.value.code is RunErrorCode.RESUME_REQUIRES_NO_ACTIVE_LEASE

    resume = ResumeCheck(RunPhase.REVIEW, "review:run-1", has_active_lease=False)
    assert RunStateMachine.validate_resume(resume) is RunPhase.AGGREGATION
    RunStateMachine.require_next_checkpoint(
        last_committed_phase=RunPhase.REVIEW,
        phase=RunPhase.AGGREGATION,
    )
    with pytest.raises(RunStateTransitionError):
        RunStateMachine.require_next_checkpoint(
            last_committed_phase=RunPhase.REVIEW,
            phase=RunPhase.PERSISTENCE,
        )


def test_checkpoint_and_lease_value_objects_enforce_nonempty_side_effects_and_valid_ttl():
    now = datetime.now(timezone.utc)
    checkpoint = RunCheckpoint(RunPhase.PERSISTENCE, "persist:run-1", now)
    assert checkpoint.side_effect_key == "persist:run-1"
    lease = RunLease("worker-1", "opaque-token", now, now + timedelta(seconds=60))
    assert lease.is_active_at(now)
    with pytest.raises(ValueError):
        RunCheckpoint(RunPhase.PERSISTENCE, "", now)
    with pytest.raises(ValueError):
        RunLease("worker-1", "opaque-token", now, now)
