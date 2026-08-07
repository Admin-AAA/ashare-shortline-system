from datetime import datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.models.tables import RecommendationRun
from app.services.evidence import identity_from_persisted_run
from app.services.evidence.validation import canonical_live_forward_run_ids


def _canonical_live_run(index: int) -> RecommendationRun:
    trade_date = f"2099-04-{index + 1:02d}"
    run = RecommendationRun(
        run_id=f"validation_query_budget_{index}",
        run_name="validation query budget",
        run_mode="formal",
        trade_date=trade_date,
        data_as_of=trade_date,
        source_snapshot_id=f"validation-snapshot-{index}",
        evidence_class="live_forward",
        actual_generated_at=datetime.fromisoformat(f"{trade_date}T06:00:00"),
        strategy_policy_fingerprint="a" * 64,
        execution_policy_fingerprint="b" * 64,
        code_revision="c" * 40,
        provenance_schema_version=1,
        status="completed",
    )
    run.evidence_identity_hash = identity_from_persisted_run(run).identity_hash
    return run


def test_canonical_live_validation_loads_all_identity_fields_in_one_query() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    RecommendationRun.__table__.create(engine)
    with Session(engine) as db:
        db.add_all([_canonical_live_run(index) for index in range(3)])
        db.commit()

    selects: list[str] = []

    def count_selects(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)

    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        with Session(engine) as db:
            run_ids = canonical_live_forward_run_ids(db)
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)

    assert run_ids == tuple(f"validation_query_budget_{index}" for index in range(3))
    assert len(selects) == 1
    assert "evidence_identity_hash" in selects[0]


def test_canonical_live_validation_pushes_date_range_into_query() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    RecommendationRun.__table__.create(engine)
    with Session(engine) as db:
        db.add_all([_canonical_live_run(index) for index in range(3)])
        db.commit()

        assert canonical_live_forward_run_ids(
            db,
            date_from="2099-04-02",
            date_to="2099-04-02",
        ) == ("validation_query_budget_1",)
        assert canonical_live_forward_run_ids(
            db,
            date_from="2099-04-03",
        ) == ("validation_query_budget_2",)
        assert canonical_live_forward_run_ids(
            db,
            date_to="2099-04-01",
        ) == ("validation_query_budget_0",)

        with pytest.raises(ValueError, match="cannot be combined"):
            canonical_live_forward_run_ids(
                db,
                trade_date="2099-04-01",
                date_from="2099-04-01",
            )
        with pytest.raises(ValueError, match="must not be later"):
            canonical_live_forward_run_ids(
                db,
                date_from="2099-04-03",
                date_to="2099-04-01",
            )
