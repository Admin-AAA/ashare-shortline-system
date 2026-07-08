from typing import Any, Literal

from pydantic import BaseModel, Field


class DataQualitySummary(BaseModel):
    quote_status: str = "unknown"
    quote_source_count: int = 0
    kline_status: str = "unknown"
    kline_days: int = 0
    auxiliary_coverage: float = 0.0
    missing_fields: list[str] = Field(default_factory=list)


class SkillBreakdownItem(BaseModel):
    """精简的 skill 评分分解项，用于推荐卡片展示（用户看分数怎么来的）."""
    strategy_name: str = ""
    score: float = 0.0
    phase: str = ""
    setup_match: str = ""
    confidence: float = 0.0
    evidence: list[str] = Field(default_factory=list)
    hard_veto: bool = False
    veto_reason: str = ""


class SkillReviewSummary(BaseModel):
    skill_id: str
    skill_name: str
    stance: str = "unknown"
    score: float = 0.0
    confidence: float = 0.0
    supports: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    action: str = "watch"


class CandidateTraceRow(BaseModel):
    code: str
    stage: str
    passed: bool
    reason: str = ""
    metrics: dict[str, Any] = Field(default_factory=dict)


class MarketTemperature(BaseModel):
    trade_date: str
    sample_count: int = 0
    up_count: int = 0
    down_count: int = 0
    limit_up_count: int = 0
    limit_down_count: int = 0
    up_ratio: float = 0.0
    heat_score: float = 0.0
    regime: str = "unknown"
    main_industries: list[str] = Field(default_factory=list)


class CockpitRecommendation(BaseModel):
    recommendation_id: str
    run_id: str
    trade_date: str
    code: str
    name: str
    industry: str | None = None
    final_score: float = 0.0
    score_gap: float | None = None
    bucket: str = ""
    setup_type: str | None = None
    rationale: str = ""
    risk_level: Literal["low", "medium", "high", "unknown"] = "unknown"
    risk_tags: list[str] = Field(default_factory=list)
    info_tags: list[str] = Field(default_factory=list)
    status: str = "pending"
    watch_status: str = "none"
    consensus_label: str = "未知"
    data_quality: DataQualitySummary = Field(default_factory=DataQualitySummary)
    preset_name: str | None = None
    preset_id: str | None = None
    run_name: str | None = None
    ideal_buy: float | None = None
    secondary_buy: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    recommend_price: float | None = None
    entry_rule: str | None = None
    invalid_rule: str | None = None
    degraded_reason: str | None = None
    market_context: dict[str, Any] = Field(default_factory=dict)
    skill_breakdown: list[SkillBreakdownItem] = Field(default_factory=list)
    # LLM 审查顶层快捷字段（B 项：候选池卡片不展开即可见 LLM 态度）
    llm_stance: str | None = None
    llm_delta: float | None = None


class RiskRadar(BaseModel):
    high: int = 0
    medium: int = 0
    info: int = 0
    tags: list[str] = Field(default_factory=list)


class RunProgress(BaseModel):
    run_id: str | None = None
    status: str = "idle"
    phase: str | None = None
    progress_pct: float | None = None
    message: str = ""
    error_message: str | None = None
    heartbeat_stale: bool = False
    resumable: bool = False


class PipelineStageSummary(BaseModel):
    stage: str
    label: str
    input_count: int = 0
    pass_count: int = 0
    filtered_count: int = 0
    reason_counts: dict[str, int] = Field(default_factory=dict)


class RecommendationExplanation(BaseModel):
    recommendation_id: str
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    skill_reviews: list[SkillReviewSummary] = Field(default_factory=list)
    pipeline_trace: list[CandidateTraceRow] = Field(default_factory=list)
    trade_plan: dict[str, Any] = Field(default_factory=dict)
    review_feedback: dict[str, Any] = Field(default_factory=dict)


class DashboardOverview(BaseModel):
    market: MarketTemperature
    recommendations: list[CockpitRecommendation] = Field(default_factory=list)
    risk_radar: RiskRadar = Field(default_factory=RiskRadar)
    current_run: RunProgress = Field(default_factory=RunProgress)
    data_health: dict[str, Any] = Field(default_factory=dict)
    task_health: dict[str, Any] = Field(default_factory=dict)
    validation: dict[str, Any] = Field(default_factory=dict)
    quality_scorecard: dict[str, Any] = Field(default_factory=dict)
