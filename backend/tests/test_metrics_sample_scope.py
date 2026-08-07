from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.sql.selectable import Select

from app.api.metrics import router as metrics_router
from app.api.stats import router as stats_router
from app.db.session import get_db
from app.models.tables import (
    DailyKbar,
    Recommendation,
    RecommendationOutcome,
    RecommendationOutcomeVersion,
    RecommendationRun,
)
from app.engine.canonical import canonical_fingerprint, canonical_json
from app.services.evidence import (
    EvidenceClass,
    PROVENANCE_SCHEMA_VERSION,
    identity_from_persisted_run,
)
from app.services.evidence.reconstruction_batch import (
    build_reconstruction_batch_identity,
    create_or_resume_reconstruction_batch,
    publish_reconstruction_batch,
)
from app.services.official_sample_scope import (
    HISTORICAL_REPLAY_SCOPE,
    LIVE_FORWARD_SCOPE,
)
from app.services.outcome_service import (
    _horizon_metric_records_by_recommendation,
    alpha_decay_detection,
    filter_effectiveness,
    exit_ambiguity_metrics,
    invalidate_outcome_metrics_cache,
    overview_metrics,
    precision_at_k,
)


def _reconstruction_identity(trade_dates: tuple[str, ...]):
    return build_reconstruction_batch_identity(
        expected_trade_dates=trade_dates,
        request_template={
            "schema_version": 1,
            "run_mode": "formal",
            "run_name": "REPLAY-历史推荐重建",
            "trade_date_override": trade_dates[0],
            "score_line": 72.0,
            "output_count": 6,
            "skip_aux_refresh": True,
        },
        strategy_policy={"fingerprint": "a" * 64},
        execution_policy={"fingerprint": "b" * 64},
        code_revision="c" * 40,
    )


def _reconstruction_request(trade_date: str) -> str:
    return canonical_json(
        {
            "schema_version": 1,
            "run_mode": "formal",
            "run_name": "REPLAY-历史推荐重建",
            "trade_date_override": trade_date,
            "score_line": 72.0,
            "output_count": 6,
            "skip_aux_refresh": True,
        }
    )


def _reconstruction_execution_summary() -> str:
    return canonical_json(
        {
            "execution": {
                "strategy_fingerprints": ({"fingerprint": "a" * 64},),
                "execution_policy": {"fingerprint": "b" * 64},
            }
        }
    )


def _run(
    run_id: str,
    *,
    trade_date: str,
    evidence_class: EvidenceClass,
    evaluation_scope: str,
    complete: bool = True,
    reconstruction_batch_id: str | None = None,
) -> RecommendationRun:
    is_replay = evidence_class is EvidenceClass.HISTORICAL_RECONSTRUCTION
    actual_generated_at = (
        datetime(2099, 2, 20, 2, 0)
        if is_replay
        else datetime.fromisoformat(f"{trade_date}T02:00:00")
    )
    run = RecommendationRun(
        run_id=run_id,
        run_name="metrics scope fixture",
        run_mode="formal",
        evaluation_scope=evaluation_scope,
        evidence_class=evidence_class.value,
        actual_generated_at=actual_generated_at,
        evidence_identity_hash="d" * 64,
        strategy_policy_fingerprint="a" * 64,
        execution_policy_fingerprint="b" * 64,
        code_revision="c" * 40,
        reconstruction_batch_id=(
            reconstruction_batch_id or f"metrics-scope-{run_id}"
            if is_replay and complete
            else None
        ),
        provenance_schema_version=PROVENANCE_SCHEMA_VERSION,
        request_schema_version=1,
        request_json=_reconstruction_request(trade_date) if is_replay and complete else None,
        result_summary_json=(
            _reconstruction_execution_summary() if is_replay and complete else "{}"
        ),
        trade_date=trade_date,
        data_as_of=trade_date,
        source_snapshot_id=f"snapshot-{run_id}",
        started_at=datetime(2099, 2, 20, 2, 0),
        finished_at=datetime(2099, 2, 20, 2, 1),
        status="completed",
    )
    if complete and evidence_class in {
        EvidenceClass.LIVE_FORWARD,
        EvidenceClass.HISTORICAL_RECONSTRUCTION,
    }:
        run.evidence_identity_hash = identity_from_persisted_run(run).identity_hash
    return run


def _recommendation(
    recommendation_id: str,
    run_id: str,
    *,
    trade_date: str,
    code: str,
    executed_return: float,
) -> tuple[Recommendation, RecommendationOutcome]:
    recommendation = Recommendation(
        recommendation_id=recommendation_id,
        run_id=run_id,
        trade_date=trade_date,
        recommended_at=datetime(2099, 2, 20, 2, 0),
        code=code,
        name="metrics scope fixture",
        recommend_price=10.0,
        final_score=80.0,
        feature_snapshot_json="{}",
    )
    outcome = RecommendationOutcome(
        recommendation_id=recommendation_id,
        entry_triggered=True,
        entry_date=trade_date,
        exit_date=trade_date,
        executed_return=executed_return,
        outcome_label="success" if executed_return > 0 else "failed",
    )
    return recommendation, outcome


def _ambiguity_version(
    recommendation: Recommendation,
    outcome: RecommendationOutcome,
    *,
    ambiguous: bool,
) -> RecommendationOutcomeVersion:
    result = {
        "canonical_exit_evidence": True,
        "intraday_exit_evidence": {
            "applied_priority": "stop_loss" if ambiguous else None,
            "first_hit": "ambiguous" if ambiguous else "not_applicable",
            "same_bar_stop_take_profit_hit": ambiguous,
        },
        "outcome_label": outcome.outcome_label,
    }
    result_json = canonical_json(result)
    fingerprint = canonical_fingerprint(
        {"recommendation_id": recommendation.recommendation_id}
    )
    version = RecommendationOutcomeVersion(
        version_id=f"version_{recommendation.recommendation_id}",
        recommendation_id=recommendation.recommendation_id,
        calculation_version="outcome_simulation_v2",
        entry_policy_id="entry-v1",
        horizon_days=5,
        label_source="auto",
        result_json=result_json,
        calculation_fingerprint=fingerprint,
        result_hash=canonical_fingerprint(result),
        calculation_schema_version=2,
        validity_status="VALID",
    )
    outcome.calculation_version_id = version.version_id
    outcome.current_calculation_fingerprint = fingerprint
    return version


def test_metrics_scopes_are_provenance_selected_and_cache_isolated(db):
    trade_date = "2099-02-10"
    live = _run(
        "run_metrics_scope_live",
        trade_date=trade_date,
        evidence_class=EvidenceClass.LIVE_FORWARD,
        evaluation_scope="historical_replay",
    )
    replay = _run(
        "run_metrics_scope_replay",
        trade_date=trade_date,
        evidence_class=EvidenceClass.HISTORICAL_RECONSTRUCTION,
        evaluation_scope="live_forward",
    )
    incomplete_replay = _run(
        "run_metrics_scope_incomplete",
        trade_date=trade_date,
        evidence_class=EvidenceClass.HISTORICAL_RECONSTRUCTION,
        evaluation_scope="historical_replay",
        complete=False,
    )
    unverified = _run(
        "run_metrics_scope_unverified",
        trade_date=trade_date,
        evidence_class=EvidenceClass.UNVERIFIED,
        evaluation_scope="historical_replay",
    )
    live_rec, live_outcome = _recommendation(
        "rec_metrics_scope_live",
        live.run_id,
        trade_date=trade_date,
        code="989101",
        executed_return=2.0,
    )
    replay_rec, replay_outcome = _recommendation(
        "rec_metrics_scope_replay",
        replay.run_id,
        trade_date=trade_date,
        code="989102",
        executed_return=-2.0,
    )
    incomplete_rec, incomplete_outcome = _recommendation(
        "rec_metrics_scope_incomplete",
        incomplete_replay.run_id,
        trade_date=trade_date,
        code="989103",
        executed_return=3.0,
    )
    unverified_rec, unverified_outcome = _recommendation(
        "rec_metrics_scope_unverified",
        unverified.run_id,
        trade_date=trade_date,
        code="989104",
        executed_return=4.0,
    )
    forged_replay = _run(
        "run_metrics_scope_forged_replay",
        trade_date=trade_date,
        evidence_class=EvidenceClass.HISTORICAL_RECONSTRUCTION,
        evaluation_scope="historical_replay",
    )
    forged_replay.evidence_identity_hash = "0" * 64
    forged_rec, forged_outcome = _recommendation(
        "rec_metrics_scope_forged_replay",
        forged_replay.run_id,
        trade_date=trade_date,
        code="989105",
        executed_return=5.0,
    )
    db.add_all(
        [
            live,
            replay,
            incomplete_replay,
            unverified,
            live_rec,
            live_outcome,
            replay_rec,
            replay_outcome,
            incomplete_rec,
            incomplete_outcome,
            unverified_rec,
            unverified_outcome,
            forged_replay,
            forged_rec,
            forged_outcome,
        ]
    )
    db.flush()
    invalidate_outcome_metrics_cache()

    live_metrics = overview_metrics(
        db,
        start_date=trade_date,
        end_date=trade_date,
        sample_scope=LIVE_FORWARD_SCOPE,
    )
    replay_metrics = overview_metrics(
        db,
        start_date=trade_date,
        end_date=trade_date,
        sample_scope=HISTORICAL_REPLAY_SCOPE,
    )

    assert live_metrics["total_recommendations"] == 1
    assert live_metrics["success_count"] == 1
    assert live_metrics["hit_rate"] == 1.0
    assert replay_metrics["total_recommendations"] == 0
    assert replay_metrics["failed_count"] == 0
    assert replay_metrics["hit_rate"] == 0.0


def test_published_reconstruction_cohort_drives_all_historical_metrics(db):
    trade_date = "2099-02-11"
    identity = _reconstruction_identity((trade_date,))
    create_or_resume_reconstruction_batch(db, identity)
    replay = _run(
        "run_metrics_published_replay",
        trade_date=trade_date,
        evidence_class=EvidenceClass.HISTORICAL_RECONSTRUCTION,
        evaluation_scope="historical_replay",
        reconstruction_batch_id=identity.batch_id,
    )
    replay_rec, replay_outcome = _recommendation(
        "rec_metrics_published_replay",
        replay.run_id,
        trade_date=trade_date,
        code="989106",
        executed_return=-2.0,
    )
    db.add_all([replay, replay_rec, replay_outcome])
    db.flush()

    invalidate_outcome_metrics_cache()
    before_publish = overview_metrics(
        db,
        start_date=trade_date,
        end_date=trade_date,
        sample_scope=HISTORICAL_REPLAY_SCOPE,
    )
    assert before_publish["total_recommendations"] == 0

    publish_reconstruction_batch(db, identity.batch_id)
    overview = overview_metrics(
        db,
        start_date=trade_date,
        end_date=trade_date,
        sample_scope=HISTORICAL_REPLAY_SCOPE,
    )
    precision = precision_at_k(
        db,
        ks=(1,),
        start_date=trade_date,
        end_date=trade_date,
        sample_scope=HISTORICAL_REPLAY_SCOPE,
    )

    assert overview["total_recommendations"] == 1
    assert overview["failed_count"] == 1
    assert overview["historical_reconstruction_cohort"] == {
        "batch_id": identity.batch_id,
        "window_start": trade_date,
        "window_end": trade_date,
        "expected_trade_date_count": 1,
        "completed_trade_date_count": 1,
        "completed_at": overview["historical_reconstruction_cohort"]["completed_at"],
        "cohort_identity_hash": overview["historical_reconstruction_cohort"][
            "cohort_identity_hash"
        ],
        "request_template_fingerprint": identity.request_template_fingerprint,
        "strategy_policy_fingerprint": identity.strategy_policy_fingerprint,
        "execution_policy_fingerprint": identity.execution_policy_fingerprint,
        "code_revision": identity.code_revision,
    }
    assert precision == [
        {
            "k": 1,
            "sample_runs": 1,
            "evaluated_count": 1,
            "success_count": 0,
            "precision": 0.0,
            "date_range": {
                "window_days": None,
                "start_date": trade_date,
                "end_date": trade_date,
                "settle_lag_days": 7,
                "settle_cutoff": None,
                "total_in_window": 1,
                "labeled_in_window": 1,
                "pending_excluded": 0,
            },
        }
    ]


def test_exit_ambiguity_uses_exact_live_or_single_published_cohort(db):
    trade_date = "2099-02-12"
    identity = _reconstruction_identity((trade_date,))
    create_or_resume_reconstruction_batch(db, identity)
    live = _run(
        "run_ambiguity_live",
        trade_date=trade_date,
        evidence_class=EvidenceClass.LIVE_FORWARD,
        evaluation_scope="historical_replay",
    )
    replay = _run(
        "run_ambiguity_replay",
        trade_date=trade_date,
        evidence_class=EvidenceClass.HISTORICAL_RECONSTRUCTION,
        evaluation_scope="historical_replay",
        reconstruction_batch_id=identity.batch_id,
    )
    forged = _run(
        "run_ambiguity_forged",
        trade_date=trade_date,
        evidence_class=EvidenceClass.LIVE_FORWARD,
        evaluation_scope="live_forward",
    )
    forged.evidence_identity_hash = "0" * 64
    live_rec, live_outcome = _recommendation(
        "rec_ambiguity_live",
        live.run_id,
        trade_date=trade_date,
        code="989111",
        executed_return=2.0,
    )
    replay_rec, replay_outcome = _recommendation(
        "rec_ambiguity_replay",
        replay.run_id,
        trade_date=trade_date,
        code="989112",
        executed_return=-2.0,
    )
    forged_rec, forged_outcome = _recommendation(
        "rec_ambiguity_forged",
        forged.run_id,
        trade_date=trade_date,
        code="989113",
        executed_return=3.0,
    )
    versions = [
        _ambiguity_version(live_rec, live_outcome, ambiguous=False),
        _ambiguity_version(replay_rec, replay_outcome, ambiguous=True),
        _ambiguity_version(forged_rec, forged_outcome, ambiguous=True),
    ]
    db.add_all([
        live,
        replay,
        forged,
        live_rec,
        live_outcome,
        replay_rec,
        replay_outcome,
        forged_rec,
        forged_outcome,
        *versions,
    ])
    db.flush()

    live_report = exit_ambiguity_metrics(
        db,
        start_date=trade_date,
        end_date=trade_date,
        sample_scope=LIVE_FORWARD_SCOPE,
    )
    before_publish = exit_ambiguity_metrics(
        db,
        start_date=trade_date,
        end_date=trade_date,
        sample_scope=HISTORICAL_REPLAY_SCOPE,
    )
    publish_reconstruction_batch(db, identity.batch_id)
    replay_report = exit_ambiguity_metrics(
        db,
        start_date=trade_date,
        end_date=trade_date,
        sample_scope=HISTORICAL_REPLAY_SCOPE,
    )

    assert live_report["overall"] == {
        "eligible_count": 1,
        "ambiguous_count": 0,
        "ambiguity_rate": 0.0,
    }
    assert live_report["by_evaluation_scope"] == {
        LIVE_FORWARD_SCOPE: live_report["overall"]
    }
    assert before_publish["overall"]["eligible_count"] == 0
    assert replay_report["overall"] == {
        "eligible_count": 1,
        "ambiguous_count": 1,
        "ambiguity_rate": 1.0,
    }
    assert replay_report["by_evaluation_scope"] == {
        HISTORICAL_REPLAY_SCOPE: replay_report["overall"]
    }


def test_metrics_scope_rejects_unknown_value_before_query(db):
    invalidate_outcome_metrics_cache()
    with pytest.raises(ValueError, match="unsupported metrics sample_scope"):
        overview_metrics(db, sample_scope="evaluation_scope_only")
    with pytest.raises(ValueError, match="unsupported metrics sample_scope"):
        exit_ambiguity_metrics(db, sample_scope="evaluation_scope_only")


def test_metrics_api_rejects_unknown_sample_scope():
    app = FastAPI()
    app.include_router(metrics_router)
    app.dependency_overrides[get_db] = lambda: SimpleNamespace()

    with TestClient(app) as client:
        response = client.get("/api/metrics/overview?sample_scope=unknown")

    assert response.status_code == 422


def test_filter_effectiveness_empty_result_keeps_frontend_contract(monkeypatch):
    monkeypatch.setattr(
        "app.services.outcome_service._historical_cohort_for_metrics",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.outcome_service._scoped_recommendation_rows",
        lambda *_args, **_kwargs: [],
    )
    invalidate_outcome_metrics_cache()

    result = filter_effectiveness(
        SimpleNamespace(),
        sample_scope=HISTORICAL_REPLAY_SCOPE,
    )

    assert result["count"] == 0
    assert result["filtered_count"] == 0
    assert result["filter_rate_pct"] == 0.0
    assert result["kept"] == {
        "count": 0,
        "net_win_rate": 0.0,
        "net_avg_return": 0.0,
        "net_profit_factor": 0.0,
    }
    assert result["filtered_by_rule"] == {
        "pos20_low": {"count": 0, "win_rate": 0.0, "avg_return": 0.0},
        "avg_cost_high": {"count": 0, "win_rate": 0.0, "avg_return": 0.0},
        "regime_ma5": {"count": 0, "win_rate": 0.0, "avg_return": 0.0},
    }


def test_horizon_cache_distinguishes_equal_sized_row_sets(monkeypatch):
    calls: list[tuple[str, ...]] = []

    def compute(_db, rows):
        recommendation_ids = tuple(row.recommendation_id for row in rows)
        calls.append(recommendation_ids)
        return {recommendation_id: {} for recommendation_id in recommendation_ids}

    monkeypatch.setattr(
        "app.services.outcome_service._compute_horizon_metric_records",
        compute,
    )
    invalidate_outcome_metrics_cache()
    first_rows = [
        SimpleNamespace(recommendation_id="rec_horizon_a", trade_date="2099-02-10"),
    ]
    second_rows = [
        SimpleNamespace(recommendation_id="rec_horizon_b", trade_date="2099-02-10"),
    ]

    first = _horizon_metric_records_by_recommendation(
        SimpleNamespace(),
        first_rows,
        cache_scope=LIVE_FORWARD_SCOPE,
    )
    second = _horizon_metric_records_by_recommendation(
        SimpleNamespace(),
        second_rows,
        cache_scope=LIVE_FORWARD_SCOPE,
    )
    first_again = _horizon_metric_records_by_recommendation(
        SimpleNamespace(),
        list(first_rows),
        cache_scope=LIVE_FORWARD_SCOPE,
    )

    assert first == {"rec_horizon_a": {}}
    assert second == {"rec_horizon_b": {}}
    assert first_again == first
    assert calls == [("rec_horizon_a",), ("rec_horizon_b",)]


def test_alpha_decay_cache_is_partitioned_by_sample_scope_and_window(monkeypatch):
    monkeypatch.setattr(
        "app.services.outcome_service._historical_cohort_for_metrics",
        lambda *_args, **_kwargs: None,
    )
    calls: list[tuple[str | None, str | None, str | None]] = []

    def scoped_rows(_db, *args, start_date=None, end_date=None, sample_scope=None, **kwargs):
        calls.append((sample_scope, start_date, end_date))
        return [
            SimpleNamespace(
                entry_triggered=True,
                outcome_label="success",
                feature_snapshot_json="{}",
                executed_return=1.0,
                trade_date="2099-02-10",
            )
        ] if sample_scope == LIVE_FORWARD_SCOPE else []

    monkeypatch.setattr(
        "app.services.outcome_service._scoped_recommendation_rows",
        scoped_rows,
    )
    invalidate_outcome_metrics_cache()

    live_result = alpha_decay_detection(
        SimpleNamespace(),
        sample_scope=LIVE_FORWARD_SCOPE,
        start_date="2099-02-01",
        end_date="2099-02-10",
    )
    replay_result = alpha_decay_detection(
        SimpleNamespace(),
        sample_scope=HISTORICAL_REPLAY_SCOPE,
        start_date="2099-02-01",
        end_date="2099-02-10",
    )
    narrower_result = alpha_decay_detection(
        SimpleNamespace(),
        sample_scope=LIVE_FORWARD_SCOPE,
        start_date="2099-02-05",
        end_date="2099-02-10",
    )

    assert live_result["total"] == 1
    assert live_result["date_range"]["start_date"] == "2099-02-01"
    assert live_result["date_range"]["end_date"] == "2099-02-10"
    assert replay_result["total"] == 0
    assert narrower_result["total"] == 1
    assert narrower_result["date_range"]["start_date"] == "2099-02-05"
    assert calls == [
        (LIVE_FORWARD_SCOPE, "2099-02-01", "2099-02-10"),
        (HISTORICAL_REPLAY_SCOPE, "2099-02-01", "2099-02-10"),
        (LIVE_FORWARD_SCOPE, "2099-02-05", "2099-02-10"),
    ]


def test_alpha_decay_api_forwards_window_and_scope(monkeypatch):
    captured: dict[str, object] = {}

    def fake_alpha_decay(_db, **kwargs):
        captured.update(kwargs)
        return {"total": 0, "factors": [], "date_range": {}}

    monkeypatch.setattr("app.api.stats.alpha_decay_detection", fake_alpha_decay)
    app = FastAPI()
    app.include_router(stats_router)
    app.dependency_overrides[get_db] = lambda: SimpleNamespace()

    with TestClient(app) as client:
        response = client.get(
            "/api/stats/alpha-decay"
            "?window_days=30&sample_scope=historical_replay"
        )

    assert response.status_code == 200
    assert captured == {
        "sample_scope": HISTORICAL_REPLAY_SCOPE,
        "window_days": 30,
        "start_date": None,
        "end_date": None,
    }


def test_horizon_metric_bars_include_liquidity_columns(db, monkeypatch):
    """horizon 指标的可成交性判定必须看到 volume/amount（回归）。

    因果：_compute_horizon_metric_records 为省性能只 select 部分列，T4 优化砍列时
    漏掉 volume/amount——而 entry/exit 可成交性判定（no_liquidity_reason_from_bar）
    要求 volume>0 且 amount>0，缺失时全部入场被误判「无成交量买不进」→ 3 个 horizon
    的 triggered_count 恒 0、hit_rate 恒 0，与主口径（_load_outcome_bars 全列实体）
    的 39 触发/15 胜完全矛盾（recon-v1-61b5c84b 回放 cohort 实证）。
    """
    from app.services.outcome_service import _compute_horizon_metric_records

    captured: list[tuple] = []

    class _Result:
        def all(self):
            return []

    class _FakeDb:
        def execute(self, stmt, *args, **kwargs):
            if isinstance(stmt, Select):
                column_names = tuple(
                    col.name
                    for col in stmt.selected_columns
                    if getattr(col, "name", None)
                )
                if "trade_date" in column_names and "close" in column_names:
                    captured.append(column_names)
            return _Result()

        def scalars(self, stmt, *args, **kwargs):
            return _Result()

    rows = [
        SimpleNamespace(
            recommendation_id="rec_horizon_liquidity",
            code="989101",
            trade_date="2099-02-10",
            recommend_price=10.0,
            feature_snapshot_json="{}",
            outcome_label="failed",
        )
    ]
    assert _compute_horizon_metric_records(_FakeDb(), rows) == {}
    assert captured, "horizon 指标未发出日K列查询"
    bar_columns = captured[0]
    assert "volume" in bar_columns and "amount" in bar_columns, (
        f"horizon 指标日K查询缺 volume/amount（实际列: {bar_columns}），"
        "可成交性判定会把全部入场误判为无成交量 → horizon 指标恒 0"
    )
