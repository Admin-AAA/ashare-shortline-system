from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.session import Base
from app.engine.execution_policy import DEFAULT_EXECUTION_POLICY
from app.models.tables import (
    DailyKbar,
    Recommendation,
    RecommendationOutcome,
    RecommendationOutcomeVersion,
    RecommendationRun,
)
from app.services.evidence import identity_from_persisted_run
from app.services.outcome_identity import (
    bounded_outcome_bars,
    outcome_market_artifact_hash,
    outcome_market_artifact_payload,
)
from app.services.outcome_service import _apply_outcome_simulation
from app.services.outcome_simulation import _simulate_outcome
from app.services.entry_policy import NEXT_TRADING_DAY_OPEN


def _bar(trade_date: str, *, close: float = 10.0, high: float | None = None) -> DailyKbar:
    return DailyKbar(
        code="000001",
        trade_date=trade_date,
        open=close,
        high=high if high is not None else close + 0.2,
        low=close - 0.2,
        close=close,
        volume=1000,
        amount=10000,
        pct_chg=0.0,
        turnover_rate=1.0,
    )


def _run(run_id: str, *, complete: bool) -> RecommendationRun:
    values = {
        "run_id": run_id,
        "run_name": "outcome v2 writer test",
        "run_mode": "formal",
        "evaluation_scope": "live_forward",
        "trade_date": "2026-01-05",
        "status": "completed",
        "selected_skills_json": "[]",
        "params_json": "{}",
        "market_snapshot_json": "{}",
        "result_summary_json": "{}",
    }
    if complete:
        values.update(
            evidence_class="live_forward",
            actual_generated_at=datetime(2026, 1, 5, 8, 0),
            data_as_of="2026-01-05",
            source_snapshot_id="snapshot_outcome_v2",
            evidence_identity_hash="a" * 64,
            strategy_policy_fingerprint="b" * 64,
            execution_policy_fingerprint="c" * 64,
            code_revision="d" * 40,
            provenance_schema_version=1,
        )
    run = RecommendationRun(**values)
    if complete:
        run.evidence_identity_hash = identity_from_persisted_run(run).identity_hash
    return run


def _recommendation(rec_id: str, run_id: str) -> Recommendation:
    return Recommendation(
        recommendation_id=rec_id,
        run_id=run_id,
        trade_date="2026-01-05",
        recommended_at=datetime(2026, 1, 5, 8, 0),
        code="000001",
        name="writer test",
        recommend_price=10.0,
        final_score=82.0,
        consensus_score=80.0,
        feature_snapshot_json=json.dumps(
            {
                "ideal_buy": 10.0,
                "secondary_buy": 10.1,
                "stop_loss": 9.5,
                "take_profit": 10.8,
            }
        ),
        risk_tags_json="[]",
        info_tags_json="[]",
        market_context_json="{}",
        skill_breakdown_json="[]",
        agent_summary_json="{}",
        status="pending",
    )


def _database() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            RecommendationRun.__table__,
            Recommendation.__table__,
            DailyKbar.__table__,
            RecommendationOutcome.__table__,
            RecommendationOutcomeVersion.__table__,
        ],
    )
    return Session(engine)


def _settle(db: Session, rec: Recommendation, bars: list[DailyKbar]) -> tuple[bool, dict]:
    bounded = bounded_outcome_bars(rec, bars, 5)
    simulation = _simulate_outcome(
        rec,
        bounded,
        [str(bar.trade_date) for bar in bounded],
        horizon_days=5,
    )
    assert simulation is not None
    return (
        _apply_outcome_simulation(
            db,
            rec,
            simulation,
            5,
            market_bars=bounded,
        ),
        simulation,
    )


def test_complete_provenance_appends_one_valid_version_and_reuses_projection() -> None:
    db = _database()
    run = _run("run_valid", complete=True)
    rec = _recommendation("rec_valid", run.run_id)
    bars = [
        _bar("2026-01-05"),
        _bar("2026-01-06", close=10.0),
        _bar("2026-01-07", close=10.6, high=11.0),
        _bar("2026-01-08", close=10.7),
    ]
    db.add_all([run, rec, *bars])
    db.commit()

    assert _settle(db, rec, bars)[0] is True
    db.flush()
    outcome = db.get(RecommendationOutcome, rec.recommendation_id)
    assert outcome is not None
    assert outcome.current_calculation_fingerprint
    assert outcome.projection_revision == 1
    first_labeled_at = outcome.labeled_at
    assert db.scalar(select(func.count()).select_from(RecommendationOutcomeVersion)) == 1
    version = db.scalar(select(RecommendationOutcomeVersion))
    assert version is not None
    assert version.validity_status == "VALID"
    assert version.calculation_schema_version == 2

    assert _settle(db, rec, bars)[0] is True
    db.flush()
    db.refresh(outcome)
    assert outcome.projection_revision == 1
    assert outcome.labeled_at == first_labeled_at
    assert db.scalar(select(func.count()).select_from(RecommendationOutcomeVersion)) == 1


def test_valid_version_fingerprint_binds_the_effective_entry_policy() -> None:
    db = _database()
    run = _run("run_next_open", complete=True)
    rec = _recommendation("rec_next_open", run.run_id)
    snapshot = json.loads(rec.feature_snapshot_json)
    snapshot["entry_mode"] = "next_open"
    rec.feature_snapshot_json = json.dumps(snapshot)
    bars = [
        _bar("2026-01-05"),
        _bar("2026-01-06", close=10.2),
        _bar("2026-01-07", close=10.6),
    ]
    db.add_all([run, rec, *bars])
    db.commit()

    bounded = bounded_outcome_bars(rec, bars, 5)
    simulation = _simulate_outcome(
        rec,
        bounded,
        [str(bar.trade_date) for bar in bounded],
        horizon_days=5,
        entry_policy=NEXT_TRADING_DAY_OPEN,
    )
    assert simulation is not None
    assert _apply_outcome_simulation(db, rec, simulation, 5, market_bars=bounded) is True
    version = db.scalar(select(RecommendationOutcomeVersion))
    assert version is not None
    assert version.entry_policy_id == "next_trading_day_open_v1"
    assert version.execution_policy_fingerprint != DEFAULT_EXECUTION_POLICY.fingerprint()


def test_same_identity_with_different_result_fails_closed() -> None:
    db = _database()
    run = _run("run_conflict", complete=True)
    rec = _recommendation("rec_conflict", run.run_id)
    bars = [_bar("2026-01-05"), _bar("2026-01-06"), _bar("2026-01-07", high=11.0)]
    db.add_all([run, rec, *bars])
    db.commit()

    ok, simulation = _settle(db, rec, bars)
    assert ok is True
    altered = {**simulation, "executed_return": float(simulation["executed_return"]) + 1.0}
    assert _apply_outcome_simulation(db, rec, altered, 5, market_bars=bounded_outcome_bars(rec, bars, 5)) is False
    assert db.scalar(select(func.count()).select_from(RecommendationOutcomeVersion)) == 1
    assert db.get(RecommendationOutcome, rec.recommendation_id).projection_revision == 1


def test_incomplete_provenance_writes_non_valid_legacy_without_trusted_projection() -> None:
    db = _database()
    run = _run("run_legacy", complete=False)
    rec = _recommendation("rec_legacy", run.run_id)
    bars = [_bar("2026-01-05"), _bar("2026-01-06"), _bar("2026-01-07", high=11.0)]
    db.add_all([run, rec, *bars])
    db.commit()

    assert _settle(db, rec, bars)[0] is True
    db.flush()
    outcome = db.get(RecommendationOutcome, rec.recommendation_id)
    version = db.scalar(select(RecommendationOutcomeVersion))
    assert outcome is not None
    assert outcome.current_calculation_fingerprint is None
    assert outcome.projection_revision is None
    assert version is not None
    assert version.validity_status == "NON_VALID"
    assert version.legacy_verification_status == "INCOMPLETE_PROVENANCE"


def test_forged_provenance_writes_non_valid_version_without_trusted_projection() -> None:
    db = _database()
    run = _run("run_forged", complete=True)
    run.evidence_identity_hash = "0" * 64
    rec = _recommendation("rec_forged", run.run_id)
    bars = [
        _bar("2026-01-05"),
        _bar("2026-01-06"),
        _bar("2026-01-07", high=11.0),
    ]
    db.add_all([run, rec, *bars])
    db.commit()

    assert _settle(db, rec, bars)[0] is True
    db.flush()

    outcome = db.get(RecommendationOutcome, rec.recommendation_id)
    version = db.scalar(select(RecommendationOutcomeVersion))
    assert outcome is not None
    assert outcome.current_calculation_fingerprint is None
    assert outcome.projection_revision is None
    assert version is not None
    assert version.validity_status == "NON_VALID"
    assert version.legacy_verification_status == "INCOMPLETE_PROVENANCE"


def test_market_artifact_excludes_bars_before_signal_and_after_bounded_window() -> None:
    rec = _recommendation("rec_window", "run_window")
    relevant = [
        _bar("2026-01-05"),
        _bar("2026-01-06"),
        _bar("2026-01-07"),
        _bar("2026-01-08"),
        _bar("2026-01-09"),
        _bar("2026-01-12"),
        _bar("2026-01-13"),
    ]
    with_outside = [_bar("2026-01-02", close=7.0), *relevant, _bar("2026-01-14", close=20.0)]
    payload = outcome_market_artifact_payload(rec, relevant, 5)
    outside_payload = outcome_market_artifact_payload(rec, with_outside, 5)

    assert payload["bars"][0]["trade_date"] == "2026-01-05"
    assert payload["bars"][-1]["trade_date"] == "2026-01-13"
    assert outcome_market_artifact_hash(payload) == outcome_market_artifact_hash(outside_payload)
