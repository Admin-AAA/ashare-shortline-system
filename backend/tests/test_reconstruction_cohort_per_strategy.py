"""每个策略取最新有效回放批次再合并（2026-10-19，owner 批准）。

策略身份 = request 模板里的 preset_id / skills[].skill_id（不看 run_name / attempt-tag）+ 交易日；
同策略多个批次按 completed_at 取最新，较旧批次只保留新批次没覆盖的交易日。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.tables import (
    Recommendation,
    RecommendationOutcome,
    RecommendationReconstructionBatch,
    RecommendationRun,
)
from app.services.evidence import identity_from_persisted_run
from app.services.evidence.reconstruction_batch import (
    MergedReconstructionCohort,
    build_reconstruction_batch_identity,
    create_or_resume_reconstruction_batch,
    latest_validated_reconstruction_cohorts,
    publish_reconstruction_batch,
    reconstruction_strategy_identity,
)
from app.services.evidence.validation import (
    canonical_historical_reconstruction_cohort,
    canonical_historical_reconstruction_run_ids,
)
from app.services.official_sample_scope import HISTORICAL_REPLAY_SCOPE
from app.services.outcome_service import (
    invalidate_outcome_metrics_cache,
    metrics_by_score_line,
    overview_metrics,
    precision_at_k,
)

DATES = ("2099-03-02", "2099-03-03")
EXECUTION_POLICY = {"policy_id": "recommendation-run", "policy_version": "v1", "fingerprint": "b" * 64}


def _strategies(skill_id: str, impl: str = "1") -> tuple[dict, ...]:
    return ({"strategy_id": skill_id, "strategy_version": "v1", "fingerprint": impl * 64},)


def _request(skill_id: str, run_name: str, trade_date: str) -> dict:
    return {
        "schema_version": 1,
        "run_mode": "formal",
        "run_name": run_name,
        "trade_date_override": trade_date,
        "score_line": 72.0,
        "output_count": 3,
        "skip_aux_refresh": True,
        "preset_id": None,
        "skills": [{"skill_id": skill_id, "sort_order": 0, "weight": 1.0}],
    }


def _canon(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _publish_batch(
    db: Session,
    *,
    skill_id: str,
    run_name: str,
    dates: tuple[str, ...] = DATES,
    impl: str = "1",
    revision: str = "c" * 40,
    prefix: str,
):
    strategies = _strategies(skill_id, impl)
    identity = build_reconstruction_batch_identity(
        expected_trade_dates=dates,
        request_template=_request(skill_id, run_name, dates[0]),
        strategy_policy=strategies,
        execution_policy=EXECUTION_POLICY,
        code_revision=revision,
    )
    create_or_resume_reconstruction_batch(db, identity)
    runs = []
    for trade_date in dates:
        run = RecommendationRun(
            run_id=f"{prefix}_{trade_date}",
            run_name=run_name,
            run_mode="formal",
            evaluation_scope="historical_replay",
            trade_date=trade_date,
            status="completed",
            evidence_class="historical_reconstruction",
            actual_generated_at=datetime(2099, 3, 10, 2, 0),
            data_as_of=trade_date,
            source_snapshot_id=f"snapshot-{prefix}-{trade_date}",
            strategy_policy_fingerprint=identity.strategy_policy_fingerprint,
            execution_policy_fingerprint=identity.execution_policy_fingerprint,
            code_revision=identity.code_revision,
            reconstruction_batch_id=identity.batch_id,
            provenance_schema_version=1,
            request_schema_version=1,
            request_json=_canon(_request(skill_id, run_name, trade_date)),
            result_summary_json=_canon(
                {"execution": {"strategy_fingerprints": list(strategies), "execution_policy": EXECUTION_POLICY}}
            ),
            started_at=datetime(2099, 3, 10, 2, 0),
            finished_at=datetime(2099, 3, 10, 2, 1),
        )
        run.evidence_identity_hash = identity_from_persisted_run(run).identity_hash
        runs.append(run)
    db.add_all(runs)
    db.flush()
    publish_reconstruction_batch(db, identity.batch_id)
    return identity, runs


def _set_completed(db: Session, batch_id: str, when: datetime) -> None:
    db.get(RecommendationReconstructionBatch, batch_id).completed_at = when
    db.flush()


def _sqlite_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    RecommendationReconstructionBatch.__table__.create(engine)
    RecommendationRun.__table__.create(engine)
    return Session(engine)


def test_strategy_identity_ignores_run_name_and_uses_preset_or_skills() -> None:
    a = reconstruction_strategy_identity(_canon(_request("chen-xiaoqun-skill", "REPLAY/x", DATES[0])))
    b = reconstruction_strategy_identity(_canon(_request("chen-xiaoqun-skill", "REPLAY/x-r2", DATES[1])))
    assert a == b == ("skills:chen-xiaoqun-skill", "chen-xiaoqun-skill")
    assert reconstruction_strategy_identity({"preset_id": "p1", "skills": []}) == ("preset:p1", "p1")
    assert reconstruction_strategy_identity({"run_name": "x"}, strategy_policy_fingerprint="F" * 64)[0] == "policy:" + "f" * 64


def test_same_strategy_two_tags_counts_only_the_newer_batch() -> None:
    with _sqlite_session() as db:
        old, _ = _publish_batch(db, skill_id="chen-xiaoqun-skill", run_name="REPLAY/rebuild-chen", prefix="old")
        # 换 attempt-tag 且实现指纹/代码版本也变了：仍是同一策略
        new, new_runs = _publish_batch(
            db, skill_id="chen-xiaoqun-skill", run_name="REPLAY/rebuild-chen-r2",
            impl="2", revision="d" * 40, prefix="new",
        )
        assert old.request_template_fingerprint != new.request_template_fingerprint
        now = datetime(2099, 3, 11)
        _set_completed(db, old.batch_id, now)
        _set_completed(db, new.batch_id, now + timedelta(hours=3))

        cohorts = latest_validated_reconstruction_cohorts(db)
        assert [c.batch_id for c in cohorts] == [new.batch_id]
        assert canonical_historical_reconstruction_run_ids(db) == tuple(r.run_id for r in new_runs)
        merged = canonical_historical_reconstruction_cohort(db)
        assert merged is not None and merged.batch_id == new.batch_id
        assert merged.as_metadata()["batch_count"] == 1

        # 选择规则是 completed_at 最新，不是创建顺序
        _set_completed(db, old.batch_id, now + timedelta(hours=5))
        assert [c.batch_id for c in latest_validated_reconstruction_cohorts(db)] == [old.batch_id]


def test_overlapping_windows_of_same_strategy_never_double_count_a_date() -> None:
    with _sqlite_session() as db:
        old, old_runs = _publish_batch(
            db, skill_id="oversold-capital-reversal", run_name="REPLAY/a",
            dates=("2099-03-02", "2099-03-03", "2099-03-04"), prefix="wide",
        )
        new, new_runs = _publish_batch(
            db, skill_id="oversold-capital-reversal", run_name="REPLAY/a-r2",
            dates=("2099-03-03", "2099-03-04"), prefix="narrow",
        )
        now = datetime(2099, 3, 11)
        _set_completed(db, old.batch_id, now)
        _set_completed(db, new.batch_id, now + timedelta(hours=1))
        ids = canonical_historical_reconstruction_run_ids(db)
        assert sorted(ids) == sorted([old_runs[0].run_id, *(r.run_id for r in new_runs)])
        cohorts = latest_validated_reconstruction_cohorts(db)
        assert cohorts[1].superseded_trade_date_count == 2
        assert cohorts[1].expected_trade_dates == ("2099-03-02",)


def test_two_strategies_are_merged() -> None:
    with _sqlite_session() as db:
        sector, sector_runs = _publish_batch(
            db, skill_id="sector-relative-strength-leader", run_name="REPLAY/rebuild-sector", prefix="sector"
        )
        chao, chao_runs = _publish_batch(
            db, skill_id="chao-gu-yang-jia-skill", run_name="REPLAY/rebuild-chao", impl="3", prefix="chao"
        )
        now = datetime(2099, 3, 11)
        _set_completed(db, chao.batch_id, now)
        _set_completed(db, sector.batch_id, now + timedelta(minutes=4))

        merged = canonical_historical_reconstruction_cohort(db)
        assert isinstance(merged, MergedReconstructionCohort)
        assert set(merged.run_ids) == {r.run_id for r in [*sector_runs, *chao_runs]}
        meta = merged.as_metadata()
        assert meta["batch_count"] == 2 and meta["run_count"] == 4
        assert meta["expected_trade_date_count"] == meta["completed_trade_date_count"] == 2
        assert {b["strategy_label"] for b in meta["batches"]} == {
            "sector-relative-strength-leader", "chao-gu-yang-jia-skill",
        }
        assert set(canonical_historical_reconstruction_run_ids(db, trade_date=DATES[0])) == {
            sector_runs[0].run_id, chao_runs[0].run_id,
        }
        # 合并封印随成员变化（缓存 key 用它区分人口）
        assert merged.cohort_identity_hash not in {sector.batch_id, chao.batch_id}


def _rec(run: RecommendationRun, code: str, ret: float, score: float):
    rec = Recommendation(
        recommendation_id=f"rec_{run.run_id}_{code}",
        run_id=run.run_id,
        trade_date=run.trade_date,
        recommended_at=datetime(2099, 3, 10, 2, 0),
        code=code,
        name="per-strategy fixture",
        recommend_price=10.0,
        final_score=score,
        feature_snapshot_json="{}",
    )
    outcome = RecommendationOutcome(
        recommendation_id=rec.recommendation_id,
        entry_triggered=True,
        entry_date=run.trade_date,
        exit_date=run.trade_date,
        executed_return=ret,
        outcome_label="success" if ret > 0 else "failed",
    )
    return [rec, outcome]


def test_diagnostics_endpoints_aggregate_across_strategy_batches(db):
    """ashare_test（事务回滚）：overview / precision@k / by_score_line 覆盖两个策略批次，旧 tag 批次不计。"""
    d = ("2099-03-16",)
    old_chen, old_runs = _publish_batch(db, skill_id="chen-xiaoqun-skill", run_name="REPLAY/chen", dates=d, prefix="pc_oldchen")
    chen, chen_runs = _publish_batch(db, skill_id="chen-xiaoqun-skill", run_name="REPLAY/chen-r2", dates=d, prefix="pc_chen")
    sector, sector_runs = _publish_batch(
        db, skill_id="sector-relative-strength-leader", run_name="REPLAY/sector", dates=d, impl="4", prefix="pc_sector"
    )
    now = datetime(2099, 3, 17)
    _set_completed(db, old_chen.batch_id, now)
    _set_completed(db, chen.batch_id, now + timedelta(hours=1))
    _set_completed(db, sector.batch_id, now + timedelta(hours=2))
    rows = [
        *_rec(old_runs[0], "989201", 9.0, 90.0),   # 旧 tag：不应计入
        *_rec(chen_runs[0], "989202", 2.0, 80.0),
        *_rec(sector_runs[0], "989203", -1.0, 76.0),
    ]
    db.add_all(rows)
    db.flush()
    invalidate_outcome_metrics_cache()

    overview = overview_metrics(db, start_date=d[0], end_date=d[0], sample_scope=HISTORICAL_REPLAY_SCOPE)
    assert overview["total_recommendations"] == 2
    assert overview["failed_count"] == 1
    meta = overview["historical_reconstruction_cohort"]
    assert meta["batch_count"] == 2
    assert {b["batch_id"] for b in meta["batches"]} == {chen.batch_id, sector.batch_id}

    precision = precision_at_k(db, ks=(1,), start_date=d[0], end_date=d[0], sample_scope=HISTORICAL_REPLAY_SCOPE)
    assert precision[0]["sample_runs"] == 2
    assert precision[0]["success_count"] == 1

    lines = metrics_by_score_line(db, score_lines=(70,), start_date=d[0], end_date=d[0], sample_scope=HISTORICAL_REPLAY_SCOPE)
    assert lines[0]["sample_count"] == 2, lines[0]
