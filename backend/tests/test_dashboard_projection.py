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
from app.services.recommendations.projection import (
    build_cockpit_recommendation,
    build_market_temperature,
    build_recommendation_explanation,
    build_risk_radar,
    build_run_progress,
)


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
    assert result.heat_score > 60
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
