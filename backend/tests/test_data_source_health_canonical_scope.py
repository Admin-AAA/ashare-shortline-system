from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.tables import RecommendationRun
from app.services.data_source_health import active_alerts
from app.services.evidence import identity_from_persisted_run


def _system_alerts(db: Session, monkeypatch) -> list[dict]:
    monkeypatch.setattr(
        "app.services.data_sources.list_data_sources",
        lambda _db: [],
    )
    return [item for item in active_alerts(db) if item["source"] == "system"]


def _canonical_run(
    *,
    run_id: str,
    evidence_class: str,
    trade_date: str,
    actual_generated_at: datetime,
    finished_at: datetime,
) -> RecommendationRun:
    run = RecommendationRun(
        run_id=run_id,
        run_name="canonical health run",
        run_mode="formal",
        trade_date=trade_date,
        data_as_of=trade_date,
        source_snapshot_id=f"snapshot-{run_id}",
        evidence_class=evidence_class,
        actual_generated_at=actual_generated_at,
        strategy_policy_fingerprint="a" * 64,
        execution_policy_fingerprint="b" * 64,
        code_revision="c" * 40,
        reconstruction_batch_id=(
            f"batch-{run_id}"
            if evidence_class == "historical_reconstruction"
            else None
        ),
        provenance_schema_version=1,
        status="completed",
        finished_at=finished_at,
    )
    run.evidence_identity_hash = identity_from_persisted_run(run).identity_hash
    return run


def test_active_alerts_ignores_unverified_completed_run(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    RecommendationRun.__table__.create(engine)
    with Session(engine) as db:
        db.add(
            RecommendationRun(
                run_id="health_unverified_completed",
                run_name="unverified health run",
                run_mode="formal",
                trade_date="2099-04-01",
                status="completed",
                finished_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )
        db.commit()

        system_alerts = _system_alerts(db, monkeypatch)

    assert len(system_alerts) == 1
    assert system_alerts[0]["status"] == "never_run"


def test_active_alerts_ignores_forged_live_identity(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    RecommendationRun.__table__.create(engine)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with Session(engine) as db:
        forged = _canonical_run(
            run_id="health_forged_live",
            evidence_class="live_forward",
            trade_date="2099-04-01",
            actual_generated_at=datetime(2099, 4, 1, 6, 0),
            finished_at=now,
        )
        forged.evidence_identity_hash = "0" * 64
        db.add(forged)
        db.commit()

        system_alerts = _system_alerts(db, monkeypatch)

    assert len(system_alerts) == 1
    assert system_alerts[0]["status"] == "never_run"


def test_active_alerts_uses_live_heartbeat_not_fresh_reconstruction(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    RecommendationRun.__table__.create(engine)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with Session(engine) as db:
        db.add_all(
            [
                _canonical_run(
                    run_id="health_stale_live",
                    evidence_class="live_forward",
                    trade_date="2099-04-01",
                    actual_generated_at=datetime(2099, 4, 1, 6, 0),
                    finished_at=now - timedelta(hours=48),
                ),
                _canonical_run(
                    run_id="health_fresh_reconstruction",
                    evidence_class="historical_reconstruction",
                    trade_date="2099-03-31",
                    actual_generated_at=datetime(2099, 4, 2, 6, 0),
                    finished_at=now,
                ),
            ]
        )
        db.commit()

        system_alerts = _system_alerts(db, monkeypatch)

    assert len(system_alerts) == 1
    assert system_alerts[0]["status"] == "stale"
