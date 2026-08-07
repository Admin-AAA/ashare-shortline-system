from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.services.capital_flow_factor_evidence import (
    fetch_dedup_live_formal_capital_flow,
    summarize_discovery_oos,
)


def _evidence_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE recommendation_runs (run_id TEXT PRIMARY KEY, run_mode TEXT)"))
        conn.execute(text("""
            CREATE TABLE recommendations (
                recommendation_id TEXT PRIMARY KEY, run_id TEXT, code TEXT,
                trade_date TEXT, recommended_at TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE recommendation_outcomes (
                recommendation_id TEXT PRIMARY KEY, outcome_label TEXT, executed_return REAL
            )
        """))
        conn.execute(text("""
            CREATE TABLE capital_flow (
                code TEXT, trade_date TEXT, main_net_inflow_yi REAL,
                super_large_net_inflow_yi REAL, large_net_inflow_yi REAL,
                cumulative_inflow_3d_yi REAL, cumulative_inflow_5d_yi REAL
            )
        """))
    return Session(engine)


def test_query_deduplicates_repeated_formal_runs_and_excludes_non_live_rows():
    db = _evidence_session()
    try:
        db.execute(text("INSERT INTO recommendation_runs VALUES ('formal_1','formal'),('formal_2','formal'),('experiment_1','experiment')"))
        db.execute(text("""
            INSERT INTO recommendations VALUES
              ('rec_early','formal_1','000001','2026-07-25','2026-07-25 09:00:00'),
              ('rec_late','formal_2','000001','2026-07-25','2026-07-25 10:00:00'),
              ('rec_experiment','experiment_1','000002','2026-07-25','2026-07-25 09:00:00'),
              ('backtest_not_live','formal_1','000003','2026-07-25','2026-07-25 09:00:00'),
              ('rec_unique','formal_1','000004','2026-07-28','2026-07-28 09:00:00')
        """))
        db.execute(text("""
            INSERT INTO recommendation_outcomes VALUES
              ('rec_early','success',2.0), ('rec_late','failed',-4.0),
              ('rec_experiment','success',3.0), ('backtest_not_live','success',5.0),
              ('rec_unique','failed',-1.0)
        """))
        db.execute(text("""
            INSERT INTO capital_flow VALUES
              ('000001','2026-07-25',1,1,1,1,1),
              ('000002','2026-07-25',1,1,1,1,1),
              ('000003','2026-07-25',1,1,1,1,1),
              ('000004','2026-07-28',-1,-1,-1,-1,-1)
        """))
        db.commit()

        rows = fetch_dedup_live_formal_capital_flow(
            db,
            ("formal_1", "formal_2"),
        )

        assert [(row["code"], row["recommendation_id"]) for row in rows] == [
            ("000001", "rec_early"),
            ("000004", "rec_unique"),
        ]
        assert rows[0]["outcome_label"] == "success"
        assert rows[0]["executed_return"] == 2.0
    finally:
        db.close()


def test_query_fails_closed_without_canonical_live_runs():
    db = _evidence_session()
    try:
        assert fetch_dedup_live_formal_capital_flow(db, ()) == []
    finally:
        db.close()


def test_discovery_oos_is_exclusive_and_blocks_small_samples():
    rows = [
        {
            "trade_date": "2026-07-24",
            "outcome_label": "success",
            "executed_return": 9.0,
            "cumulative_inflow_3d_yi": 1.0,
        },
        *[
            {
                "trade_date": f"2026-07-{25 + index // 3:02d}",
                "outcome_label": "success" if index % 2 == 0 else "failed",
                "executed_return": 1.0 if index % 2 == 0 else -1.0,
                "cumulative_inflow_3d_yi": 1.0,
            }
            for index in range(12)
        ],
    ]

    oos = summarize_discovery_oos(rows)

    assert oos["positive"]["n"] == 12
    assert oos["positive"]["trade_days"] == 4
    assert oos["positive"]["win_rate"] == 50.0
    assert oos["evidence_sufficient"] is False
    assert "证据不足" in oos["verdict"]
    assert "不得晋级" in oos["verdict"]


def test_discovery_oos_requires_sample_and_day_coverage_together():
    start = date(2026, 7, 25)
    rows = [
        {
            "trade_date": (start + timedelta(days=index % 10)).isoformat(),
            "outcome_label": "success",
            "executed_return": 1.0,
            "cumulative_inflow_3d_yi": 1.0,
        }
        for index in range(30)
    ]

    oos = summarize_discovery_oos(rows)

    assert oos["positive"]["n"] == 30
    assert oos["positive"]["trade_days"] == 10
    assert oos["evidence_sufficient"] is True
    assert "不自动晋级" in oos["verdict"]
