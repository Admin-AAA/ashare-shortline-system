from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.tables import RecommendationRun
from app.services.data_source_health import active_alerts


def test_active_alerts_ignores_unverified_completed_run(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    RecommendationRun.__table__.create(engine)
    monkeypatch.setattr(
        "app.services.data_sources.list_data_sources",
        lambda _db: [],
    )
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

        alerts = active_alerts(db)

    system_alerts = [item for item in alerts if item["source"] == "system"]
    assert len(system_alerts) == 1
    assert system_alerts[0]["status"] == "never_run"
