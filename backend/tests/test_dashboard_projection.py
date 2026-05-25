from app.schemas.dashboard import (
    CockpitRecommendation,
    DashboardOverview,
    DataQualitySummary,
    MarketTemperature,
    RecommendationExplanation,
    RiskRadar,
    RunProgress,
    SkillReviewSummary,
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
