from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.dashboard_hardening import project_dashboard_hardening
from app.services.evidence.identity import EvidenceClass, PROVENANCE_SCHEMA_VERSION
from app.services.observability import (
    AlertKind,
    AlertRegistry,
    ApplicationLifespan,
    CorrelationChain,
    allowlisted_log_fields,
    correlation_scope,
    current_correlation_id,
)

UTC = timezone.utc
NOW = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
HASH = "a" * 64


def _row(run_id: str, evidence_class: str, generated: datetime, trade_date: str, **changes):
    values = {
        "run_id": run_id,
        "evidence_class": evidence_class,
        "actual_generated_at": generated,
        "trade_date": trade_date,
        "data_as_of": trade_date,
        "source_snapshot_id": f"snapshot-{run_id}",
        "evidence_identity_hash": HASH,
        "strategy_policy_fingerprint": HASH,
        "execution_policy_fingerprint": HASH,
        "code_revision": "1" * 40,
        "provenance_schema_version": PROVENANCE_SCHEMA_VERSION,
        "reconstruction_batch_id": None,
        "status": "completed",
        "started_at": generated,
        "finished_at": generated,
        "heartbeat_at": generated,
        "result_summary_json": {
            "data_quality_decision": {"status": "ready", "reason_codes": ["COMPLETE"]},
            "risk_gate": {"status": "allow", "reason_codes": ["WITHIN_CAPS"]},
        },
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_projection_separates_evidence_and_live_remains_current_over_newer_reconstruction():
    live = _row("live", EvidenceClass.LIVE_FORWARD.value, NOW - timedelta(hours=1), "2026-08-04")
    reconstruction = _row(
        "reconstruction",
        EvidenceClass.HISTORICAL_RECONSTRUCTION.value,
        NOW,
        "2026-08-05",
        reconstruction_batch_id="batch-8",
    )
    research = _row("research", EvidenceClass.RESEARCH_BACKTEST.value, NOW, "2026-08-06")

    result = project_dashboard_hardening(
        [research, reconstruction, live],
        now=NOW,
        current_business_date="2026-08-04",
        stale_threshold=timedelta(minutes=30),
    )

    assert result["current_run"]["run_id"] == "live"
    assert result["current_label"] == "今日 LIVE 已运行"
    assert result["today_live_completed"] is True
    assert [item["run_id"] for item in result["evidence"]["LIVE_FORWARD"]] == ["live"]
    assert result["evidence"]["HISTORICAL_RECONSTRUCTION"][0]["reconstruction_label"] == "历史重建 · batch-8"
    assert [item["run_id"] for item in result["evidence"]["RESEARCH"]] == ["research"]
    assert result["data_quality_decision"] == "ready"
    assert result["risk_gate"] == "allow"
    assert result["stale"] is True


def test_reconstruction_fallback_never_claims_today_live_completed():
    reconstruction = _row(
        "reconstruction",
        EvidenceClass.HISTORICAL_RECONSTRUCTION.value,
        NOW,
        "2026-08-04",
        reconstruction_batch_id="batch-only",
    )
    result = project_dashboard_hardening(
        [reconstruction],
        now=NOW,
        current_business_date="2026-08-04",
        stale_threshold=timedelta(minutes=5),
    )
    assert result["current_run"]["evidence_section"] == "HISTORICAL_RECONSTRUCTION"
    assert result["current_label"] == "历史重建 · batch-only"
    assert result["today_live_completed"] is False


def test_all_seven_configurable_alert_conditions_are_registered_and_evaluated():
    registry = AlertRegistry.defaults()
    assert {item.kind for item in registry.conditions} == set(AlertKind)
    alerts = registry.evaluate(
        {
            "latency_ms": 3001,
            "heartbeat_age_seconds": 1201,
            "queue_delay_seconds": 301,
            "flow_failed": True,
            "quality_blocked": True,
            "identity_mismatch": True,
            "risk_halted": True,
        }
    )
    assert {item["kind"] for item in alerts} == {item.value for item in AlertKind}


def test_correlation_chain_and_logs_keep_only_allowlisted_identity_hash_reason():
    chain = (
        CorrelationChain.start("req-safe", api_identity="GET:/api/dashboard/overview")
        .with_identity("scheduler", "task-1")
        .with_identity("prefect", "flow-1")
        .with_identity("run", "run-1")
        .with_identity("provider", "daily")
        .with_identity("database", "recommendation_runs")
    )
    assert chain.correlation_id == "req-safe"
    assert chain.database_identity == "recommendation_runs"
    fields = allowlisted_log_fields(
        {
            **chain.audit_fields(stage="run", reason_code="RUN_DISPATCHED"),
            "credential": "must-not-log",
            "payload": {"secret": "must-not-log"},
        }
    )
    assert fields.keys() <= {"correlation_id", "stage", "identity", "identity_hash", "reason_code"}
    assert "must-not-log" not in str(fields)
    with correlation_scope("req-safe"):
        assert current_correlation_id() == "req-safe"


def test_application_lifespan_cleans_partial_startup_in_reverse_and_shutdown_is_idempotent():
    events: list[str] = []
    lifespan = ApplicationLifespan()
    lifespan.add_cleanup("database", lambda: events.append("database"))
    lifespan.add_cleanup("provider", lambda: events.append("provider"))

    def fail_startup() -> None:
        events.append("startup")
        raise RuntimeError("partial startup")

    with pytest.raises(RuntimeError, match="partial startup"):
        lifespan.startup(fail_startup)
    assert events == ["startup", "provider", "database"]
    assert lifespan.is_shutdown
    assert lifespan.shutdown() == ()
    assert events == ["startup", "provider", "database"]
