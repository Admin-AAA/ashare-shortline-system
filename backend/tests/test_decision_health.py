from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.tables import ApiCallLog
from app.services.data_source_health import (
    _model_completion_alert,
    _readiness_outcome,
    _recommendation_is_stale,
)
from app.services.llm_review_cache_service import model_completion_capability


NOW = datetime(2026, 8, 16, 10, 0)


def _session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    ApiCallLog.__table__.create(engine)
    return Session(engine)


def _log(*, status: str, minutes_ago: int) -> ApiCallLog:
    return ApiCallLog(
        run_id="run-health",
        model="test-model",
        endpoint="chat.completions",
        status=status,
        created_at=NOW - timedelta(minutes=minutes_ago),
    )


def test_model_completion_capability_is_critical_when_recent_calls_all_fail(monkeypatch):
    monkeypatch.setattr(
        "app.services.model_router_service.has_active_model_routes",
        lambda: True,
    )
    with _session() as db:
        db.add_all(
            [
                _log(status="circuit_open", minutes_ago=1),
                _log(status="auth_error", minutes_ago=2),
                _log(status="insufficient_balance", minutes_ago=3),
            ]
        )
        db.commit()

        health = model_completion_capability(db, now=NOW)

    assert health["status"] == "critical"
    assert health["live_success_count"] == 0
    assert health["failure_count"] == 3
    assert health["reason"] == "recent_completions_all_failed"


def test_model_completion_capability_accepts_recent_live_success(monkeypatch):
    monkeypatch.setattr(
        "app.services.model_router_service.has_active_model_routes",
        lambda: True,
    )
    with _session() as db:
        db.add_all(
            [
                _log(status="success", minutes_ago=1),
                _log(status="circuit_open", minutes_ago=2),
                _log(status="cache_hit", minutes_ago=3),
            ]
        )
        db.commit()

        health = model_completion_capability(db, now=NOW)

    assert health["status"] == "ok"
    assert health["live_success_count"] == 1
    assert health["cache_hit_count"] == 1


def test_model_completion_capability_requires_configured_route(monkeypatch):
    monkeypatch.setattr(
        "app.services.model_router_service.has_active_model_routes",
        lambda: False,
    )
    with _session() as db:
        health = model_completion_capability(db, now=NOW)

    assert health["status"] == "critical"
    assert health["reason"] == "no_active_model_route"


def test_critical_model_completion_blocks_readiness():
    overall, can_run = _readiness_outcome(
        {
            "quote": {"status": "ok"},
            "daily_bar": {"status": "ok"},
            "model_completion": {"status": "critical"},
        }
    )

    assert overall == "not_ready"
    assert can_run is False


def test_critical_model_completion_produces_actionable_alert():
    alert = _model_completion_alert(
        {
            "status": "critical",
            "reason": "recent_completions_all_failed",
            "live_attempt_count": 100,
            "live_success_count": 0,
            "status_counts": {"circuit_open": 96, "insufficient_balance": 4},
        }
    )

    assert alert is not None
    assert alert["alert_level"] == "critical"
    assert "100" in alert["message"]
    assert "circuit_open=96" in alert["message"]


def test_recommendation_staleness_uses_reference_trade_date_on_weekend():
    assert not _recommendation_is_stale(
        reference_trade_date="2026-08-14",
        latest_trade_date="2026-08-14",
        hours_since_run=47,
    )
    assert _recommendation_is_stale(
        reference_trade_date="2026-08-14",
        latest_trade_date="2026-08-13",
        hours_since_run=12,
    )
