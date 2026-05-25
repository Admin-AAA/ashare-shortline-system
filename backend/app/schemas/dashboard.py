from typing import Any, Literal

from pydantic import BaseModel, Field


class DataQualitySummary(BaseModel):
    quote_status: str
    quote_source_count: int
    kline_status: str
    kline_days: int
    auxiliary_coverage: float
    missing_fields: list[str] = Field(default_factory=list)


class SkillReviewSummary(BaseModel):
    skill_id: str
    skill_name: str
    stance: str
    score: float
    confidence: float
    supports: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    action: str


class CandidateTraceRow(BaseModel):
    stage: str
    status: str
    message: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class MarketTemperature(BaseModel):
    trade_date: str
    sample_count: int
    up_count: int
    down_count: int
    limit_up_count: int
    limit_down_count: int
    up_ratio: float
    heat_score: float
    regime: str
    main_industries: list[str] = Field(default_factory=list)


class CockpitRecommendation(BaseModel):
    recommendation_id: str
    run_id: str
    trade_date: str
    code: str
    name: str
    industry: str
    final_score: float
    score_gap: float
    bucket: str
    setup_type: str
    rationale: str
    risk_level: Literal["low", "medium", "high", "unknown"]
    risk_tags: list[str] = Field(default_factory=list)
    info_tags: list[str] = Field(default_factory=list)
    status: str
    watch_status: str
    consensus_label: str
    data_quality: DataQualitySummary


class RiskRadar(BaseModel):
    high: int
    medium: int
    info: int
    tags: list[str] = Field(default_factory=list)


class RunProgress(BaseModel):
    run_id: str
    status: str
    phase: str
    progress_pct: int
    message: str
    heartbeat_stale: bool
    resumable: bool


class PipelineStageSummary(BaseModel):
    stage: str
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    message: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)


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
    risk_radar: RiskRadar
    current_run: RunProgress | None = None
    data_health: dict[str, Any] = Field(default_factory=dict)
    task_health: dict[str, Any] = Field(default_factory=dict)
    validation: dict[str, Any] = Field(default_factory=dict)
