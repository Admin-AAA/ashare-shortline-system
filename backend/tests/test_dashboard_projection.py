import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas.dashboard import (
    CandidateTraceRow,
    CockpitRecommendation,
    DashboardOverview,
    DataQualitySummary,
    MarketTemperature,
    PipelineStageSummary,
    RecommendationExplanation,
    RiskRadar,
    RunProgress,
    SkillReviewSummary,
)
from app.api.dashboard import build_validation_summary, compose_overview, serialize_market_snapshot_row, serialize_run_progress_row
from app.services.dashboard_quality_service import build_quality_scorecard
from app.services.candidates.trace import candidate_trace_row
from app.services.recommendations.projection import (
    build_cockpit_recommendation,
    build_market_temperature,
    build_recommendation_explanation,
    build_risk_radar,
    build_run_progress,
    build_skill_reviews,
)
from app.services.recommendations.explanation import consensus_label
from app.services.runs.progress import is_resumable_status, progress_pct


def test_dashboard_overview_contract_accepts_cockpit_sections():
    overview = DashboardOverview(
        market=MarketTemperature(
            trade_date="2026-05-25",
            sample_count=4200,
            up_count=2600,
            down_count=1300,
            limit_up_count=68,
            limit_down_count=4,
            up_ratio=0.619,
            heat_score=74.2,
            regime="active",
            main_industries=["机器人", "半导体"],
        ),
        recommendations=[
            CockpitRecommendation(
                recommendation_id="rec-1",
                run_id="run-1",
                trade_date="2026-05-25",
                code="000001",
                name="平安银行",
                industry="银行",
                final_score=82.4,
                score_gap=10.4,
                bucket="core",
                setup_type="趋势突破",
                rationale="规则与 Agent 共识支持，市场环境可接受。",
                risk_level="medium",
                risk_tags=["放量过快"],
                info_tags=["日K覆盖完整"],
                status="pending",
                watch_status="none",
                consensus_label="一致",
                data_quality=DataQualitySummary(
                    quote_status="fresh",
                    quote_source_count=2,
                    kline_status="covered",
                    kline_days=90,
                    auxiliary_coverage=0.8,
                    missing_fields=[],
                ),
            )
        ],
        risk_radar=RiskRadar(high=1, medium=2, info=3, tags=["放量过快"]),
        current_run=RunProgress(
            run_id="run-1",
            status="completed",
            phase="persist",
            progress_pct=100,
            message="完成",
            heartbeat_stale=False,
            resumable=False,
        ),
        data_health={"status": "ready"},
        task_health={"running_count": 0},
        validation={"last_status": "not_run"},
        quality_scorecard={"overall_score": 80},
    )

    assert overview.market.heat_score == 74.2
    assert overview.recommendations[0].data_quality.quote_source_count == 2


def test_recommendation_explanation_contract_groups_agent_reviews():
    explanation = RecommendationExplanation(
        recommendation_id="rec-1",
        score_breakdown={"agent": 31.2, "rule": 22.5, "market": 12.0, "risk": -6.0, "evidence": 8.0},
        skill_reviews=[
            SkillReviewSummary(
                skill_id="daily-stock-analysis-skill",
                skill_name="技术评分",
                stance="support",
                score=78.0,
                confidence=0.72,
                supports=["均线多头"],
                risks=["短线乖离"],
                action="watch",
            )
        ],
        pipeline_trace=[],
        trade_plan={"entry_rule": "回踩不破 5 日线", "invalid_rule": "跌破 10 日线"},
        review_feedback={"watch_status": "none"},
    )

    assert explanation.skill_reviews[0].stance == "support"
    assert explanation.trade_plan["invalid_rule"] == "跌破 10 日线"


def test_data_quality_summary_contract_defaults_to_unknown_coverage():
    summary = DataQualitySummary()

    assert summary.quote_status == "unknown"
    assert summary.quote_source_count == 0
    assert summary.kline_status == "unknown"
    assert summary.kline_days == 0
    assert summary.auxiliary_coverage == 0.0
    assert summary.missing_fields == []


def test_skill_review_summary_contract_defaults_to_watch_unknowns():
    summary = SkillReviewSummary(skill_id="x", skill_name="X")

    assert summary.stance == "unknown"
    assert summary.score == 0.0
    assert summary.confidence == 0.0
    assert summary.supports == []
    assert summary.risks == []
    assert summary.action == "watch"


def test_candidate_trace_row_contract_uses_gate_result_shape():
    row = CandidateTraceRow(code="000001", stage="skill_gate", passed=True)

    assert row.reason == ""
    assert row.metrics == {}


def test_pipeline_stage_summary_contract_defaults_counts():
    summary = PipelineStageSummary(stage="skill_gate", label="Skill Gate")

    assert summary.input_count == 0
    assert summary.pass_count == 0
    assert summary.filtered_count == 0
    assert summary.reason_counts == {}


def test_cockpit_recommendation_contract_allows_minimal_identity_defaults():
    recommendation = CockpitRecommendation(
        recommendation_id="rec-1",
        run_id="run-1",
        trade_date="2026-05-25",
        code="000001",
        name="平安银行",
        risk_level="unknown",
    )

    assert recommendation.industry is None
    assert recommendation.final_score == 0.0
    assert recommendation.score_gap is None
    assert recommendation.bucket == ""
    assert recommendation.setup_type is None
    assert recommendation.rationale == ""
    assert recommendation.risk_tags == []
    assert recommendation.info_tags == []
    assert recommendation.status == "pending"
    assert recommendation.watch_status == "none"
    assert recommendation.consensus_label == "未知"
    assert recommendation.data_quality.quote_status == "unknown"


def test_dashboard_overview_contract_defaults_sections():
    overview = DashboardOverview(market=MarketTemperature(trade_date="2026-05-25"))

    assert overview.risk_radar.high == 0
    assert overview.risk_radar.medium == 0
    assert overview.risk_radar.info == 0
    assert overview.current_run.status == "idle"
    assert overview.current_run.heartbeat_stale is False


def test_cockpit_recommendation_rejects_invalid_risk_level():
    with pytest.raises(ValidationError):
        CockpitRecommendation(
            recommendation_id="rec-1",
            run_id="run-1",
            trade_date="2026-05-25",
            code="000001",
            name="平安银行",
            risk_level="extreme",
        )


def test_build_market_temperature_calculates_heat_and_ratio():
    snapshot = {
        "trade_date": "2026-05-25",
        "sample_count": 100,
        "up_count": 62,
        "down_count": 30,
        "limit_up_count": 7,
        "limit_down_count": 1,
        "main_industries": ["机器人"],
        "breadth": {"market_regime": "active"},
    }

    result = build_market_temperature(snapshot)

    assert result.up_ratio == 0.62
    assert result.heat_score == 45.4
    assert result.regime == "active"


def test_build_cockpit_recommendation_normalizes_json_fields():
    rec = SimpleNamespace(
        recommendation_id="rec-1",
        run_id="run-1",
        trade_date="2026-05-25",
        code="000001",
        name="平安银行",
        industry="银行",
        final_score=82.0,
        setup_type="趋势突破",
        rationale="规则和 Agent 共识支持。",
        risk_tags_json=json.dumps(["短线放量"], ensure_ascii=False),
        info_tags_json=json.dumps(["日K覆盖完整"], ensure_ascii=False),
        status="pending",
        recent_repeat=False,
        feature_snapshot_json=json.dumps(
            {
                "score_line_meta": {"effective_score_line": 72.0},
                "data_quality": {
                    "quote_cache_status": "fresh",
                    "quote_source_count": 2,
                    "kline_cache_status": "covered",
                    "kline_days": 90,
                },
            },
            ensure_ascii=False,
        ),
        skill_breakdown_json=json.dumps(
            [
                {
                    "strategy_id": "skill-a",
                    "strategy_name": "Skill A",
                    "score": 80,
                    "agent_skill_review": {"stance": "support"},
                }
            ],
            ensure_ascii=False,
        ),
    )
    watch = SimpleNamespace(watch_status="watching")

    result = build_cockpit_recommendation(rec, watch)

    assert result.score_gap == 10.0
    assert result.risk_level == "medium"
    assert result.consensus_label == "一致"
    assert result.data_quality.quote_source_count == 2


def test_build_run_progress_marks_stale_and_resumable():
    run = {
        "run_id": "run-1",
        "status": "interrupted",
        "progress_phase": "skill_review",
        "progress_pct": 45,
        "progress_message": "审查中",
        "is_stale": True,
    }

    result = build_run_progress(run)

    assert result.status == "interrupted"
    assert result.heartbeat_stale is True
    assert result.resumable is True


def test_build_risk_radar_counts_levels():
    recs = [
        CockpitRecommendation(
            recommendation_id="r1",
            run_id="run",
            trade_date="2026-05-25",
            code="000001",
            name="A",
            risk_level="high",
            risk_tags=["高位加速"],
            data_quality=DataQualitySummary(),
        ),
        CockpitRecommendation(
            recommendation_id="r2",
            run_id="run",
            trade_date="2026-05-25",
            code="000002",
            name="B",
            risk_level="medium",
            risk_tags=["放量过快"],
            data_quality=DataQualitySummary(),
        ),
    ]

    radar = build_risk_radar(recs)

    assert radar.high == 1
    assert radar.medium == 1
    assert radar.tags == ["高位加速", "放量过快"]


def test_build_skill_reviews_prefers_agent_review_score():
    reviews = build_skill_reviews(
        [
            {
                "strategy_id": "skill-a",
                "strategy_name": "Skill A",
                "score": 60,
                "agent_skill_review": {
                    "score": 88,
                    "confidence": 0.9,
                    "stance": "strong_support",
                    "supports": ["主线加强"],
                },
            }
        ]
    )

    assert reviews[0].score == 88
    assert reviews[0].stance == "strong_support"
    assert reviews[0].supports == ["主线加强"]


def test_build_recommendation_explanation_uses_rec_trade_rules_and_feature_prices():
    rec = SimpleNamespace(
        recommendation_id="rec-1",
        code="000001",
        entry_rule="回踩不破 5 日线",
        invalid_rule="跌破 10 日线",
        feature_snapshot_json=json.dumps({"stop_loss": 9.8, "take_profit": 12.5}, ensure_ascii=False),
        skill_breakdown_json="[]",
    )

    explanation = build_recommendation_explanation(rec)

    assert explanation.trade_plan == {
        "entry_rule": "回踩不破 5 日线",
        "invalid_rule": "跌破 10 日线",
        "stop_loss": 9.8,
        "take_profit": 12.5,
    }


def test_consensus_label_detects_support_family_conflict():
    assert (
        consensus_label(
            [
                {"agent_skill_review": {"stance": "strong_support"}},
                {"agent_skill_review": {"stance": "veto"}},
            ]
        )
        == "多空冲突"
    )
    assert (
        consensus_label(
            [
                {"agent_skill_review": {"stance": "neutral_support"}},
                {"agent_skill_review": {"stance": "oppose"}},
            ]
        )
        == "多空冲突"
    )


def test_compose_overview_builds_all_cockpit_sections():
    overview = compose_overview(
        market_snapshot={
            "trade_date": "2026-05-25",
            "sample_count": 100,
            "up_count": 50,
            "down_count": 40,
            "limit_up_count": 5,
            "limit_down_count": 1,
            "main_industries": ["机器人"],
            "breadth": {"market_regime": "active"},
        },
        recent_run={"run_id": "run-1", "status": "completed", "progress_pct": 100},
        recommendations=[],
        data_health={"status": "ready"},
        task_health={"running_count": 0},
        validation={"last_status": "not_run"},
        quality_scorecard={"overall_score": 72, "overall_status": "warning"},
    )

    assert overview.market.trade_date == "2026-05-25"
    assert overview.current_run.status == "completed"
    assert overview.data_health["status"] == "ready"
    assert overview.quality_scorecard["overall_score"] == 72


def test_dashboard_serializers_use_lightweight_rows():
    market = serialize_market_snapshot_row(
        SimpleNamespace(
            trade_date="2026-05-25",
            sample_count=100,
            up_count=50,
            down_count=40,
            limit_up_count=5,
            limit_down_count=1,
            main_industries_json=json.dumps(["机器人"], ensure_ascii=False),
            breadth_json=json.dumps({"market_regime": "active"}, ensure_ascii=False),
        )
    )
    run = serialize_run_progress_row(
        SimpleNamespace(
            run_id="run-1",
            status="running",
            progress_phase="skill_review",
            checkpoint_phase=None,
            progress_current=2,
            progress_total=4,
            progress_message="审查中",
            heartbeat_at=None,
        )
    )

    assert market["main_industries"] == ["机器人"]
    assert market["breadth"]["market_regime"] == "active"
    assert run["progress_pct"] == 50.0


def test_build_validation_summary_uses_cached_pool_messages():
    validation = build_validation_summary(
        {
            "recommendation_readiness": {
                "status": "blocked",
                "messages": ["日K覆盖不足"],
            }
        },
        quality_scorecard={
            "overall_score": 68,
            "overall_status": "warning",
            "highlights": ["最近验证仍主要依赖全量指标。"],
            "dimensions": {
                "validation": {"score": 61, "status": "warning"},
                "execution": {"score": 92, "status": "healthy"},
            },
        },
    )

    assert validation["last_status"] == "blocked"
    assert validation["alerts"]["medium"] == 1
    assert validation["alerts"]["messages"] == ["日K覆盖不足"]
    assert validation["quality_gate"]["overall_score"] == 68
    assert validation["quality_gate"]["validation_score"] == 61


def test_build_quality_scorecard_combines_data_validation_execution_research():
    scorecard = build_quality_scorecard(
        data_health={
            "eligible_bar_coverage_pct": 98,
            "missing_bar_count": 0,
            "stale_bar_count": 0,
            "short_history_count": 2,
            "can_run_local_recommendation": True,
        },
        task_health={"failed_count": 0, "running_count": 0},
        current_run={"status": "completed", "is_stale": False},
        latest_backtest={
            "status": "completed",
            "decision_scope": "walk_forward",
            "decision_scope_label": "滚动样本外验证",
            "metrics": {"trade_count": 36, "sample_confidence": "normal"},
            "data_coverage": {
                "candidate_minute_coverage_rate": 1.0,
                "benchmark_minute_coverage_rate": 1.0,
                "temporal_consistency": {"passed": True},
            },
        },
        recent_task_runs=[{"status": "completed"} for _ in range(4)],
        research_metrics={
            "total_recommendations": 80,
            "labeled_count": 42,
            "review_note_count": 18,
            "latest_trade_date": "2026-05-25",
        },
    )

    assert scorecard["overall_score"] >= 80
    assert scorecard["dimensions"]["validation"]["status"] == "healthy"
    assert scorecard["dimensions"]["research"]["metrics"]["labeled_count"] == 42


def test_build_quality_scorecard_penalizes_unprofitable_validation():
    scorecard = build_quality_scorecard(
        data_health={
            "eligible_bar_coverage_pct": 99,
            "missing_bar_count": 0,
            "stale_bar_count": 0,
            "short_history_count": 0,
            "can_run_local_recommendation": True,
        },
        task_health={"failed_count": 0, "running_count": 0},
        current_run={"status": "completed", "is_stale": False},
        latest_backtest={
            "status": "completed",
            "decision_scope": "walk_forward",
            "decision_scope_label": "滚动样本外验证",
            "metrics": {
                "trade_count": 34,
                "sample_confidence": "normal",
                "recommendation_accuracy": 0.47,
                "average_net_return": -0.12,
                "max_drawdown": -5.6,
                "benchmark_excess_return": -1.3,
            },
            "data_coverage": {
                "candidate_minute_coverage_rate": 1.0,
                "benchmark_minute_coverage_rate": 1.0,
                "temporal_consistency": {"passed": True},
            },
        },
        recent_task_runs=[{"status": "completed"} for _ in range(4)],
        research_metrics={
            "total_recommendations": 80,
            "labeled_count": 42,
            "review_note_count": 18,
            "latest_trade_date": "2026-05-25",
        },
    )

    validation = scorecard["dimensions"]["validation"]
    assert validation["status"] != "healthy"
    assert validation["metrics"]["recommendation_accuracy"] == 0.47
    assert any("扣费后不占优" in item for item in validation["highlights"])


def test_build_quality_scorecard_penalizes_low_executability_even_when_accuracy_ok():
    scorecard = build_quality_scorecard(
        data_health={
            "eligible_bar_coverage_pct": 99,
            "missing_bar_count": 0,
            "stale_bar_count": 0,
            "short_history_count": 0,
            "can_run_local_recommendation": True,
        },
        task_health={"failed_count": 0, "running_count": 0},
        current_run={"status": "completed", "is_stale": False},
        latest_backtest={
            "status": "completed",
            "decision_scope": "walk_forward",
            "decision_scope_label": "滚动样本外验证",
            "metrics": {
                "trade_count": 40,
                "accuracy_denominator": 40,
                "executed_trade_count": 22,
                "unexecuted_count": 18,
                "sample_confidence": "normal",
                "recommendation_accuracy": 0.55,
                "trade_accuracy": 0.45,
                "average_net_return": 0.22,
                "max_drawdown": -1.2,
                "benchmark_excess_return": 0.3,
            },
            "data_coverage": {
                "candidate_minute_coverage_rate": 1.0,
                "benchmark_minute_coverage_rate": 1.0,
                "temporal_consistency": {"passed": True},
            },
        },
        recent_task_runs=[{"status": "completed"} for _ in range(4)],
        research_metrics={
            "total_recommendations": 80,
            "labeled_count": 42,
            "review_note_count": 18,
            "latest_trade_date": "2026-05-25",
        },
    )

    validation = scorecard["dimensions"]["validation"]
    assert validation["status"] != "healthy"
    assert validation["metrics"]["trade_accuracy"] == 0.45
    assert validation["metrics"]["execution_rate"] == 0.55
    assert any("可成交率" in item for item in validation["highlights"])


def test_progress_helpers_are_stable():
    assert progress_pct(5, 10) == 50.0
    assert progress_pct(0, 0) is None
    assert is_resumable_status("interrupted") is True
    assert is_resumable_status("completed") is False


def test_candidate_trace_row_normalizes_metrics():
    row = candidate_trace_row(
        code="000001",
        stage="skill_gate",
        passed=True,
        reason="规则通过",
        metrics={"amount_yi": 8.2},
    )

    assert row["code"] == "000001"
    assert row["metrics"]["amount_yi"] == 8.2
