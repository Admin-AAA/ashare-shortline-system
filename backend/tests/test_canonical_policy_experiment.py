from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.engine.backtest import BarInput, ExecutionPolicy
from app.engine.production_policy import DEFAULT_PRODUCTION_SELECTION_POLICY
from app.models.tables import (
    BacktestCandidate,
    BacktestDataSnapshot,
    BacktestHoldoutClaim,
    BacktestExperiment,
    BacktestFailureAttribution,
    BacktestInputArtifact,
    BacktestRequestClaim,
    BacktestRunV2,
    BacktestTrade,
)
from app.services.backtest.policy_experiment import (
    CanonicalPolicyExperimentSpec,
    ProductionPolicyBacktestRunner,
    ScoredCandidateDay,
    ScoredCandidateSnapshot,
)
from app.services.backtest.research_repository import ResearchBacktestRepository
from app.services.backtest.research_repository import ImmutableRecordConflict


def _snapshot() -> ScoredCandidateSnapshot:
    start = date(2026, 1, 1)
    days = []
    for index in range(24):
        signal = start + timedelta(days=index * 2)
        entry = signal + timedelta(days=1)
        exit_day = signal + timedelta(days=2)
        code = "300001"
        days.append(
            ScoredCandidateDay(
                signal_date=signal.isoformat(),
                market_snapshot={
                    "sample_count": 100,
                    "market_regime": "结构性强势",
                    "limit_down_count": 0,
                },
                cards=({
                    "code": code,
                    "bucket": "chen-xiaoqun-skill",
                    "final_score": 90.0,
                    "recommend_price": 10.0,
                    "evidence_quality_score": 95.0,
                    "data_completeness_score": 100.0,
                    "data_authenticity": 1.0,
                    "data_quality": {"missing_fields": []},
                },),
                outcome_bars={code: (
                    BarInput(code, entry.isoformat(), 10.0, 10.6, 9.9, 10.5),
                    BarInput(code, exit_day.isoformat(), 10.5, 11.0, 10.4, 10.8),
                )},
            )
        )
    return ScoredCandidateSnapshot(
        source="production-rule-replay",
        days=tuple(days),
    )


def test_policy_experiment_recovers_from_zero_champion_on_one_shared_snapshot() -> None:
    snapshot = _snapshot()
    # 2026-08-02 用户决策：默认关闭结构性强势 hard_veto → 收紧版（patch True）
    # 制造 0 票 champion，默认版为 challenger（恢复产出），验证 zero champion 恢复。
    champion = DEFAULT_PRODUCTION_SELECTION_POLICY.with_rule_patch(
        "structural_strong_hard_veto", True
    )
    artifact = ProductionPolicyBacktestRunner().run(
        CanonicalPolicyExperimentSpec(
            experiment_id="policy_recovery",
            hypothesis="撤销结构性强势硬否决能否恢复正期望交易",
            candidate_snapshot=snapshot,
            champion_policy=champion,
            challenger_policy=DEFAULT_PRODUCTION_SELECTION_POLICY,
            changed_rule_id="structural_strong_hard_veto",
            score_line=72.0,
            output_count=6,
            execution_policy=ExecutionPolicy(
                horizon_days=2,
                stop_loss_pct=5.0,
                take_profit_pct=10.0,
                commission_rate=0,
                stamp_tax_rate=0,
                entry_slippage_bps=0,
                exit_slippage_bps=0,
            ),
        )
    )

    assert artifact["schema_version"] == 2
    assert artifact["experiment_kind"] == "production_policy"
    assert artifact["candidate_snapshot_fingerprint"] == snapshot.fingerprint
    assert artifact["release_policy_fingerprint"]
    assert artifact["changed_rule_delta"] == artifact["opportunity_cost_evidence"]
    assert artifact["arms"]["champion"]["coverage_evidence"]["active_days"] == 0
    assert artifact["arms"]["challenger"]["coverage_evidence"]["active_days"] > 0
    assert artifact["decision_scope"] == "out_of_sample"
    assert artifact["decision"]["decision"] == "PRODUCTION_CANDIDATE"
    assert artifact["decision"]["liveness_passed"] is True


def test_scored_candidate_snapshot_is_deeply_immutable() -> None:
    snapshot = _snapshot()
    with pytest.raises(TypeError):
        snapshot.days[0].cards[0]["final_score"] = 0
    with pytest.raises(TypeError):
        snapshot.days[0].market_snapshot["sample_count"] = 0


def test_zero_challenger_is_rejected_even_when_financial_metrics_are_cash() -> None:
    snapshot = _snapshot()
    artifact = ProductionPolicyBacktestRunner().run(
        CanonicalPolicyExperimentSpec(
            experiment_id="policy_noop_zero",
            hypothesis="零票 challenger 不得发布",
            candidate_snapshot=snapshot,
            champion_policy=DEFAULT_PRODUCTION_SELECTION_POLICY,
            challenger_policy=DEFAULT_PRODUCTION_SELECTION_POLICY.with_rule_patch(
                "structural_strong_hard_veto", True
            ),
            changed_rule_id="structural_strong_hard_veto",
            score_line=72.0,
            output_count=6,
            execution_policy=ExecutionPolicy(horizon_days=2),
        )
    )

    assert artifact["decision"]["should_publish"] is False
    assert "oos_executed_trade_required" in artifact["decision"]["failed_gates"]


def test_newly_dropped_positive_trade_fails_opportunity_cost_gate() -> None:
    snapshot = _snapshot()
    payload = snapshot.canonical_payload()
    for day in payload["days"]:
        day["market_snapshot"]["market_regime"] = "弱势"
        day["cards"][0]["code"] = "600001"
        day["outcome_bars"] = {
            "600001": [
                {**bar, "code": "600001"}
                for bar in day["outcome_bars"]["300001"]
            ]
        }
    snapshot = ScoredCandidateSnapshot.from_payload(payload)
    champion = DEFAULT_PRODUCTION_SELECTION_POLICY.with_rule_patch("board_universe", "all")
    artifact = ProductionPolicyBacktestRunner().run(
        CanonicalPolicyExperimentSpec(
            experiment_id="policy_drop_winner",
            hypothesis="扩大到 all board 后再收回 20cm 不得靠丢赢家获胜",
            candidate_snapshot=snapshot,
            champion_policy=champion,
            challenger_policy=DEFAULT_PRODUCTION_SELECTION_POLICY,
            changed_rule_id="board_universe",
            score_line=72.0,
            output_count=6,
            execution_policy=ExecutionPolicy(horizon_days=2),
        )
    )

    cost = artifact["opportunity_cost_evidence"]
    assert cost["newly_rejected_positive_trades"] > 0
    assert "changed_rule_no_dropped_positive_trade" in artifact["decision"]["failed_gates"]


def test_policy_experiment_persists_snapshot_arms_verdicts_trades_and_evidence() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
                BacktestDataSnapshot.__table__,
                BacktestHoldoutClaim.__table__,
            BacktestInputArtifact.__table__,
            BacktestRequestClaim.__table__,
            BacktestRunV2.__table__,
            BacktestCandidate.__table__,
            BacktestTrade.__table__,
            BacktestFailureAttribution.__table__,
            BacktestExperiment.__table__,
        ],
    )
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        snapshot = _snapshot()
        artifact = ProductionPolicyBacktestRunner(
            ResearchBacktestRepository(session)
        ).run(
            CanonicalPolicyExperimentSpec(
                experiment_id="policy_persisted",
                hypothesis="正式 policy evidence 必须只写 backtest 研究表",
                candidate_snapshot=snapshot,
                champion_policy=DEFAULT_PRODUCTION_SELECTION_POLICY,
                challenger_policy=DEFAULT_PRODUCTION_SELECTION_POLICY.with_rule_patch(
                    "structural_strong_hard_veto", True
                ),
                changed_rule_id="structural_strong_hard_veto",
                score_line=72.0,
                output_count=6,
                    execution_policy=ExecutionPolicy(horizon_days=2),
                    holdout_fingerprint=snapshot.fingerprint,
                    holdout_scope="untouched_holdout",
                )
        )

        experiment = session.get(BacktestExperiment, "policy_persisted")
        assert session.query(BacktestDataSnapshot).count() == 1
        assert session.query(BacktestRunV2).count() == 2
        assert session.query(BacktestCandidate).count() == 48
        assert session.query(BacktestTrade).count() == 24
        assert experiment is not None
        assert experiment.status == "completed"
        assert artifact["evidence_fingerprint"] in str(experiment.conclusion_json)
    finally:
        session.close()
        engine.dispose()


def test_policy_experiment_namespaces_equal_simulated_trades_by_backtest_arm() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
                BacktestDataSnapshot.__table__,
                BacktestHoldoutClaim.__table__,
            BacktestInputArtifact.__table__,
            BacktestRequestClaim.__table__,
            BacktestRunV2.__table__,
            BacktestCandidate.__table__,
            BacktestTrade.__table__,
            BacktestFailureAttribution.__table__,
            BacktestExperiment.__table__,
        ],
    )
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        payload = _snapshot().canonical_payload()
        for day in payload["days"]:
            day["market_snapshot"]["market_regime"] = "弱势"
            day["cards"][0]["chg20"] = 20.0
        snapshot = ScoredCandidateSnapshot.from_payload(payload)
        artifact = ProductionPolicyBacktestRunner(
            ResearchBacktestRepository(session)
        ).run(
            CanonicalPolicyExperimentSpec(
                experiment_id="policy_overlapping_arm_trades",
                hypothesis="两臂相同交易仍须作为独立研究记录持久化",
                candidate_snapshot=snapshot,
                champion_policy=DEFAULT_PRODUCTION_SELECTION_POLICY,
                challenger_policy=DEFAULT_PRODUCTION_SELECTION_POLICY.with_rule_patch(
                    "strong_score_penalty", 7.0
                ),
                changed_rule_id="strong_score_penalty",
                score_line=72.0,
                output_count=6,
                    execution_policy=ExecutionPolicy(horizon_days=2),
                    holdout_fingerprint=snapshot.fingerprint,
                    holdout_scope="untouched_holdout",
                )
        )

        rows = session.query(BacktestTrade).all()
        assert artifact["status"] == "completed"
        assert len(rows) == 48
        assert len({row.trade_id for row in rows}) == 48
        assert len({row.backtest_id for row in rows}) == 2
        assert all('"trade_id"' in row.trade_json for row in rows)
    finally:
        session.close()
        engine.dispose()


def test_holdout_ledger_is_idempotent_for_same_experiment_and_rejects_reuse() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[BacktestHoldoutClaim.__table__])
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        repository = ResearchBacktestRepository(session)
        fingerprint = "b" * 64
        first = repository.claim_holdout(
            holdout_fingerprint=fingerprint,
            experiment_id="exp-one",
        )
        same = repository.claim_holdout(
            holdout_fingerprint=fingerprint,
            experiment_id="exp-one",
        )
        assert same.holdout_fingerprint == first.holdout_fingerprint
        with pytest.raises(ImmutableRecordConflict):
            repository.claim_holdout(
                holdout_fingerprint=fingerprint,
                experiment_id="exp-two",
            )
    finally:
        session.close()
        engine.dispose()
