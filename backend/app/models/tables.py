from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Stock(Base):
    __tablename__ = "stocks"

    code: Mapped[str] = mapped_column(String(12), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    exchange: Mapped[str | None] = mapped_column(String(20))
    industry: Mapped[str | None] = mapped_column(String(80))
    is_st: Mapped[bool] = mapped_column(Boolean, default=False)
    list_status: Mapped[str] = mapped_column(String(20), default="listed")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DailyBar(Base):
    __tablename__ = "daily_bars"
    __table_args__ = (UniqueConstraint("code", "trade_date", name="uq_daily_bar_code_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(12), index=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float, default=0)
    amount: Mapped[float] = mapped_column(Float, default=0)
    pct_chg: Mapped[float] = mapped_column(Float, default=0)
    turnover_rate: Mapped[float] = mapped_column(Float, default=0)
    ma5: Mapped[float | None] = mapped_column(Float, nullable=True)
    ma10: Mapped[float | None] = mapped_column(Float, nullable=True)
    ma20: Mapped[float | None] = mapped_column(Float, nullable=True)
    ma60: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_ratio_5d: Mapped[float | None] = mapped_column(Float, nullable=True)


class DailyIndicator(Base):
    __tablename__ = "daily_indicators"
    __table_args__ = (UniqueConstraint("code", "trade_date", name="uq_daily_indicator_code_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(12), index=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    rsi_6: Mapped[float | None] = mapped_column(Float, nullable=True)
    rsi_12: Mapped[float | None] = mapped_column(Float, nullable=True)
    rsi_24: Mapped[float | None] = mapped_column(Float, nullable=True)
    macd_dif: Mapped[float | None] = mapped_column(Float, nullable=True)
    macd_dea: Mapped[float | None] = mapped_column(Float, nullable=True)
    macd_bar: Mapped[float | None] = mapped_column(Float, nullable=True)
    bias_ma5: Mapped[float | None] = mapped_column(Float, nullable=True)
    bias_ma10: Mapped[float | None] = mapped_column(Float, nullable=True)
    bias_ma20: Mapped[float | None] = mapped_column(Float, nullable=True)
    breakout20: Mapped[int] = mapped_column(Integer, default=0)
    breakout60: Mapped[int] = mapped_column(Integer, default=0)
    upper_shadow_count20: Mapped[int] = mapped_column(Integer, default=0)
    max_dd20: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_dd60: Mapped[float | None] = mapped_column(Float, nullable=True)


class RawQuote(Base):
    __tablename__ = "raw_quotes"
    __table_args__ = (UniqueConstraint("source", "code", "trade_date", name="uq_raw_quote_source_code_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(40), index=True)
    code: Mapped[str] = mapped_column(String(12), index=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    quality_json: Mapped[str] = mapped_column(Text, default="{}")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class RawDailyBar(Base):
    __tablename__ = "raw_daily_bars"
    __table_args__ = (UniqueConstraint("source", "code", "trade_date", name="uq_raw_daily_bar_source_code_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(40), index=True)
    code: Mapped[str] = mapped_column(String(12), index=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class UnifiedQuote(Base):
    __tablename__ = "unified_quotes"
    __table_args__ = (UniqueConstraint("code", "trade_date", name="uq_unified_quote_code_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(12), index=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    quote_json: Mapped[str] = mapped_column(Text, default="{}")
    source_trace_json: Mapped[str] = mapped_column(Text, default="{}")
    source_count: Mapped[int] = mapped_column(Integer, default=0)
    quality_score: Mapped[float] = mapped_column(Float, default=0)
    freshness_status: Mapped[str] = mapped_column(String(30), default="fresh")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DataSourceHealth(Base):
    __tablename__ = "data_source_health"

    source: Mapped[str] = mapped_column(String(40), primary_key=True)
    source_type: Mapped[str] = mapped_column(String(20), primary_key=True)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    empty_count: Mapped[int] = mapped_column(Integer, default=0)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[float] = mapped_column(Float, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DataSourceConfig(Base):
    __tablename__ = "data_source_config"

    source: Mapped[str] = mapped_column(String(40), primary_key=True)
    source_type: Mapped[str] = mapped_column(String(20), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    note: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    up_count: Mapped[int] = mapped_column(Integer, default=0)
    down_count: Mapped[int] = mapped_column(Integer, default=0)
    limit_up_count: Mapped[int] = mapped_column(Integer, default=0)
    limit_down_count: Mapped[int] = mapped_column(Integer, default=0)
    main_industries_json: Mapped[str] = mapped_column(Text, default="[]")
    breadth_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SkillRegistry(Base):
    __tablename__ = "skill_registry"

    skill_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    source_type: Mapped[str] = mapped_column(String(30), default="local")
    source_url: Mapped[str | None] = mapped_column(String(500))
    installed_path: Mapped[str] = mapped_column(String(500))
    current_version: Mapped[str] = mapped_column(String(40), default="0.1.0")
    mode: Mapped[str] = mapped_column(String(20), default="formal")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    has_executable_contract: Mapped[bool] = mapped_column(Boolean, default=False)
    compatibility_json: Mapped[str] = mapped_column(Text, default="{}")
    manifest_json: Mapped[str] = mapped_column(Text, default="{}")
    installed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SkillVersion(Base):
    __tablename__ = "skill_versions"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    skill_id: Mapped[str] = mapped_column(String(80), index=True)
    version: Mapped[str] = mapped_column(String(40))
    source_ref: Mapped[str | None] = mapped_column(String(200))
    installed_path: Mapped[str] = mapped_column(String(500))
    manifest_json: Mapped[str] = mapped_column(Text, default="{}")
    contract_hash: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(30), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class StrategyVersion(Base):
    __tablename__ = "strategy_versions"

    version_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(80), index=True)
    name: Mapped[str] = mapped_column(String(120))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    skill_repo: Mapped[str | None] = mapped_column(String(500))
    skill_ref: Mapped[str | None] = mapped_column(String(200))
    skill_path: Mapped[str] = mapped_column(String(500))
    yaml_path: Mapped[str] = mapped_column(String(500))
    yaml_hash: Mapped[str] = mapped_column(String(80))
    manifest_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Preset(Base):
    __tablename__ = "presets"

    preset_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    run_mode: Mapped[str] = mapped_column(String(20), default="formal")
    market_scope: Mapped[str] = mapped_column(String(30), default="all")
    market_scope_value: Mapped[str | None] = mapped_column(String(120))
    risk_template: Mapped[str] = mapped_column(String(20), default="balanced")
    output_count: Mapped[int] = mapped_column(Integer, default=6)
    bucket_limits_json: Mapped[str] = mapped_column(Text, default="{}")
    data_source: Mapped[str] = mapped_column(String(20), default="local_pool")
    hide_recent_repeats: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PresetSkill(Base):
    __tablename__ = "preset_skills"
    __table_args__ = (UniqueConstraint("preset_id", "skill_id", name="uq_preset_skill"),)

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    preset_id: Mapped[str] = mapped_column(String(80), ForeignKey("presets.preset_id"), index=True)
    skill_id: Mapped[str] = mapped_column(String(80), ForeignKey("skill_registry.skill_id"), index=True)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RecommendationRun(Base):
    __tablename__ = "recommendation_runs"

    run_id: Mapped[str] = mapped_column(String(60), primary_key=True)
    preset_id: Mapped[str | None] = mapped_column(String(80), ForeignKey("presets.preset_id"), index=True)
    run_name: Mapped[str] = mapped_column(String(120), default="")
    run_mode: Mapped[str] = mapped_column(String(20), default="formal")
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    universe_count: Mapped[int] = mapped_column(Integer, default=0)
    recommended_count: Mapped[int] = mapped_column(Integer, default=0)
    market_scope: Mapped[str] = mapped_column(String(30), default="all")
    market_scope_value: Mapped[str | None] = mapped_column(String(120))
    risk_template: Mapped[str] = mapped_column(String(20), default="balanced")
    output_count: Mapped[int] = mapped_column(Integer, default=6)
    selected_skills_json: Mapped[str] = mapped_column(Text, default="[]")
    params_json: Mapped[str] = mapped_column(Text, default="{}")
    market_snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    result_summary_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(30), default="running")
    error_message: Mapped[str | None] = mapped_column(Text)


class Recommendation(Base):
    __tablename__ = "recommendations"

    recommendation_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(60), ForeignKey("recommendation_runs.run_id"), index=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    recommended_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    code: Mapped[str] = mapped_column(String(12), index=True)
    name: Mapped[str] = mapped_column(String(80))
    industry: Mapped[str | None] = mapped_column(String(80))
    recommend_price: Mapped[float] = mapped_column(Float, default=0)
    bucket: Mapped[str] = mapped_column(String(30))
    final_score: Mapped[float] = mapped_column(Float)
    consensus_score: Mapped[float] = mapped_column(Float, default=0)
    setup_type: Mapped[str | None] = mapped_column(String(80))
    entry_rule: Mapped[str | None] = mapped_column(Text)
    invalid_rule: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)
    risk_tags_json: Mapped[str] = mapped_column(Text, default="[]")
    recent_repeat: Mapped[bool] = mapped_column(Boolean, default=False)
    feature_snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    market_context_json: Mapped[str] = mapped_column(Text, default="{}")
    skill_breakdown_json: Mapped[str] = mapped_column(Text, default="[]")
    agent_summary_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(30), default="pending")


class RecommendationOutcome(Base):
    __tablename__ = "recommendation_outcomes"

    recommendation_id: Mapped[str] = mapped_column(String(80), ForeignKey("recommendations.recommendation_id"), primary_key=True)
    horizon_days: Mapped[int] = mapped_column(Integer, default=5)
    entry_triggered: Mapped[bool] = mapped_column(Boolean, default=True)
    entry_date: Mapped[str | None] = mapped_column(String(10))
    entry_price: Mapped[float | None] = mapped_column(Float)
    max_return: Mapped[float] = mapped_column(Float, default=0)
    min_return: Mapped[float] = mapped_column(Float, default=0)
    close_return: Mapped[float] = mapped_column(Float, default=0)
    achievable_return: Mapped[float] = mapped_column(Float, default=0)
    max_drawdown: Mapped[float] = mapped_column(Float, default=0)
    hit_target: Mapped[bool] = mapped_column(Boolean, default=False)
    hit_invalid: Mapped[bool] = mapped_column(Boolean, default=False)
    outcome_label: Mapped[str] = mapped_column(String(30), default="pending")
    # 旧标准（max_return>=5%&&min>-4%）命中的标签，用于新旧标准对比
    outcome_label_legacy: Mapped[str] = mapped_column(String(30), default="pending")
    outcome_reason: Mapped[str | None] = mapped_column(Text)
    label_source: Mapped[str] = mapped_column(String(20), default="auto")
    labeled_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ReviewNote(Base):
    __tablename__ = "review_notes"
    __table_args__ = (UniqueConstraint("recommendation_id", name="uq_review_note_recommendation"),)

    note_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    recommendation_id: Mapped[str] = mapped_column(String(80), ForeignKey("recommendations.recommendation_id"), index=True)
    user_label: Mapped[str | None] = mapped_column(String(80))
    failure_reason: Mapped[str | None] = mapped_column(String(120))
    review_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class WatchItem(Base):
    __tablename__ = "watch_items"
    __table_args__ = (UniqueConstraint("source_recommendation_id", name="uq_watch_recommendation"),)

    watch_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    code: Mapped[str] = mapped_column(String(12), index=True)
    source_run_id: Mapped[str] = mapped_column(String(60), ForeignKey("recommendation_runs.run_id"), index=True)
    source_recommendation_id: Mapped[str] = mapped_column(String(80), ForeignKey("recommendations.recommendation_id"), index=True)
    watch_status: Mapped[str] = mapped_column(String(20), default="watching")
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AgentDecisionLog(Base):
    __tablename__ = "agent_decision_logs"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    recommendation_id: Mapped[str | None] = mapped_column(String(80), index=True)
    run_id: Mapped[str | None] = mapped_column(String(60), index=True)
    agent_name: Mapped[str] = mapped_column(String(80))
    output_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    backtest_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    skill_id: Mapped[str] = mapped_column(String(80), index=True)
    date_from: Mapped[str] = mapped_column(String(10))
    date_to: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(30), default="completed")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CalibrationConfig(Base):
    __tablename__ = "calibration_config"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
