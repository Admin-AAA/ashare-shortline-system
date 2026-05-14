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


# ── 辅助数据源：原始数据表 ──────────────────────────────────────────────

class RawLimitUpBoard(Base):
    __tablename__ = "raw_limit_up_board"
    __table_args__ = (UniqueConstraint("source", "code", "trade_date", name="uq_raw_limit_up_board_s_c_d"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(40), index=True)
    code: Mapped[str] = mapped_column(String(12), index=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class RawConceptSector(Base):
    __tablename__ = "raw_concept_sector"
    __table_args__ = (UniqueConstraint("source", "sector_code", "trade_date", name="uq_raw_concept_sector_sc_td"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(40), index=True)
    sector_code: Mapped[str] = mapped_column(String(20), index=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class RawCapitalFlow(Base):
    __tablename__ = "raw_capital_flow"
    __table_args__ = (UniqueConstraint("source", "code", "trade_date", name="uq_raw_capital_flow_s_c_d"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(40), index=True)
    code: Mapped[str] = mapped_column(String(12), index=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class RawDragonTiger(Base):
    __tablename__ = "raw_dragon_tiger"
    __table_args__ = (UniqueConstraint("source", "code", "trade_date", name="uq_raw_dragon_tiger_s_c_d"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(40), index=True)
    code: Mapped[str] = mapped_column(String(12), index=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class RawNewsAnnouncement(Base):
    __tablename__ = "raw_news_announcement"
    __table_args__ = (UniqueConstraint("source", "article_id", name="uq_raw_news_announcement_s_aid"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(40), index=True)
    code: Mapped[str] = mapped_column(String(12), index=True)
    article_id: Mapped[str] = mapped_column(String(80), index=True)
    announce_date: Mapped[str] = mapped_column(String(10), index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


# ── 辅助数据源：标准化数据表 ────────────────────────────────────────────

class LimitUpBoard(Base):
    __tablename__ = "limit_up_board"
    __table_args__ = (UniqueConstraint("code", "trade_date", name="uq_limit_up_board_code_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(12), index=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    name: Mapped[str | None] = mapped_column(String(80))
    consecutive_days: Mapped[int] = mapped_column(Integer, default=1)
    limit_up_time: Mapped[str | None] = mapped_column(String(8))
    first_limit_up_time: Mapped[str | None] = mapped_column(String(8))
    is_sealed: Mapped[bool] = mapped_column(Boolean, default=True)
    seal_amount_yi: Mapped[float | None] = mapped_column(Float)
    limit_up_reason: Mapped[str | None] = mapped_column(Text)
    concept_sector: Mapped[str | None] = mapped_column(String(80))
    turnover_amount_yi: Mapped[float | None] = mapped_column(Float)
    turnover_rate: Mapped[float | None] = mapped_column(Float)
    seal_count: Mapped[int] = mapped_column(Integer, default=0)
    open_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ConceptSector(Base):
    __tablename__ = "concept_sector"
    __table_args__ = (UniqueConstraint("sector_code", "trade_date", name="uq_concept_sector_code_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sector_code: Mapped[str] = mapped_column(String(20), index=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    sector_name: Mapped[str] = mapped_column(String(80))
    pct_chg: Mapped[float | None] = mapped_column(Float)
    leading_code: Mapped[str | None] = mapped_column(String(12))
    leading_name: Mapped[str | None] = mapped_column(String(80))
    leading_pct: Mapped[float | None] = mapped_column(Float)
    stock_count: Mapped[int] = mapped_column(Integer, default=0)
    limit_up_count: Mapped[int] = mapped_column(Integer, default=0)
    total_amount_yi: Mapped[float | None] = mapped_column(Float)
    heat_rank: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ConceptSectorStock(Base):
    __tablename__ = "concept_sector_stock"
    __table_args__ = (UniqueConstraint("sector_code", "code", "trade_date", name="uq_concept_sector_stock_sc_c_d"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sector_code: Mapped[str] = mapped_column(String(20), index=True)
    code: Mapped[str] = mapped_column(String(12), index=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    name: Mapped[str | None] = mapped_column(String(80))
    pct_chg: Mapped[float | None] = mapped_column(Float)
    market_cap_yi: Mapped[float | None] = mapped_column(Float)
    is_leading: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CapitalFlow(Base):
    __tablename__ = "capital_flow"
    __table_args__ = (UniqueConstraint("code", "trade_date", name="uq_capital_flow_code_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(12), index=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    main_net_inflow_yi: Mapped[float | None] = mapped_column(Float)
    super_large_net_inflow_yi: Mapped[float | None] = mapped_column(Float)
    large_net_inflow_yi: Mapped[float | None] = mapped_column(Float)
    medium_net_inflow_yi: Mapped[float | None] = mapped_column(Float)
    small_net_inflow_yi: Mapped[float | None] = mapped_column(Float)
    main_net_inflow_ratio: Mapped[float | None] = mapped_column(Float)
    cumulative_inflow_3d_yi: Mapped[float | None] = mapped_column(Float)
    cumulative_inflow_5d_yi: Mapped[float | None] = mapped_column(Float)
    cumulative_inflow_10d_yi: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DragonTiger(Base):
    __tablename__ = "dragon_tiger"
    __table_args__ = (UniqueConstraint("code", "trade_date", name="uq_dragon_tiger_code_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(12), index=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    name: Mapped[str | None] = mapped_column(String(80))
    reason: Mapped[str | None] = mapped_column(Text)
    buy_amount_yi: Mapped[float | None] = mapped_column(Float)
    sell_amount_yi: Mapped[float | None] = mapped_column(Float)
    net_amount_yi: Mapped[float | None] = mapped_column(Float)
    buy_seat_count: Mapped[int] = mapped_column(Integer, default=0)
    sell_seat_count: Mapped[int] = mapped_column(Integer, default=0)
    institution_net_buy_yi: Mapped[float | None] = mapped_column(Float)
    top_buy_broker: Mapped[str | None] = mapped_column(String(80))
    top_sell_broker: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class NewsAnnouncement(Base):
    __tablename__ = "news_announcement"
    __table_args__ = (UniqueConstraint("source", "article_id", name="uq_news_announcement_src_aid"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(40))
    article_id: Mapped[str] = mapped_column(String(80), index=True)
    code: Mapped[str] = mapped_column(String(12), index=True)
    announce_date: Mapped[str] = mapped_column(String(10), index=True)
    title: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(40))
    sentiment: Mapped[str | None] = mapped_column(String(20))
    is_risk_warning: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ApiCallLog(Base):
    """LLM API 调用日志 — 用于成本追踪。"""
    __tablename__ = "api_call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str | None] = mapped_column(String(60), index=True)
    skill_id: Mapped[str | None] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(80))
    endpoint: Mapped[str] = mapped_column(String(200))
    prompt_chars: Mapped[int] = mapped_column(Integer, default=0)
    completion_chars: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(30), default="success")  # success / error / timeout
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ScheduledTask(Base):
    """定时任务配置 — 支持按 Cron 表达式调度。"""
    __tablename__ = "scheduled_tasks"

    task_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    task_type: Mapped[str] = mapped_column(String(40))  # pool_refresh / recommendation_run / data_backfill
    cron_expression: Mapped[str] = mapped_column(String(80))  # e.g. "30 9 * * 1-5" (weekdays 9:30)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_status: Mapped[str | None] = mapped_column(String(30))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
