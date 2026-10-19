"""ORM models: recommendation domain (split from app.models.tables)."""
from datetime import datetime

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import DATETIME, LONGBLOB, LONGTEXT, MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow_naive
from app.db.session import Base



class RecommendationReconstructionBatch(Base):
    """A complete, version-bound historical reconstruction cohort."""

    __tablename__ = "recommendation_reconstruction_batches"
    __table_args__ = (
        Index(
            "ix_rec_reconstruction_batches_status_completed",
            "status",
            "completed_at",
        ),
    )

    batch_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    schema_version: Mapped[int] = mapped_column(SmallInteger, default=1)
    window_start: Mapped[str] = mapped_column(String(10), index=True)
    window_end: Mapped[str] = mapped_column(String(10), index=True)
    expected_trade_dates_json: Mapped[str] = mapped_column(Text)
    expected_trade_date_count: Mapped[int] = mapped_column(Integer)
    completed_trade_date_count: Mapped[int] = mapped_column(Integer, default=0)
    request_template_fingerprint: Mapped[str] = mapped_column(CHAR(64))
    strategy_policy_fingerprint: Mapped[str] = mapped_column(CHAR(64))
    execution_policy_fingerprint: Mapped[str] = mapped_column(CHAR(64))
    code_revision: Mapped[str] = mapped_column(String(64))
    cohort_identity_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="building")
    failure_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime().with_variant(DATETIME(fsp=6), "mysql"),
        default=utcnow_naive,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime().with_variant(DATETIME(fsp=6), "mysql"),
        default=utcnow_naive,
        onupdate=utcnow_naive,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime().with_variant(DATETIME(fsp=6), "mysql"),
        nullable=True,
    )


class RecommendationRun(Base):
    __tablename__ = "recommendation_runs"
    __table_args__ = (
        Index(
            "ix_rec_runs_evidence_trade_generated",
            "evidence_class",
            "trade_date",
            "actual_generated_at",
        ),
        Index("ix_rec_runs_reconstruction_batch", "reconstruction_batch_id"),
        Index("ix_rec_runs_evidence_identity_hash", "evidence_identity_hash"),
        Index(
            "uq_rec_runs_reconstruction_batch_date",
            "reconstruction_batch_id",
            "trade_date",
            unique=True,
        ),
    )

    run_id: Mapped[str] = mapped_column(String(60), primary_key=True)
    preset_id: Mapped[str | None] = mapped_column(String(80), index=True)
    run_name: Mapped[str] = mapped_column(String(120), default="")
    run_mode: Mapped[str] = mapped_column(String(20), default="formal")
    evaluation_scope: Mapped[str] = mapped_column(
        String(32),
        default="historical_replay",
        server_default="historical_replay",
    )
    data_as_of: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    source_snapshot_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Nullable during the compatibility phase. Task 1.3+ owns explicit writes and
    # task 1.4 owns reviewed legacy classification/backfill; this schema migration
    # never infers provenance or rewrites legacy scope/timestamps.
    evidence_class: Mapped[str | None] = mapped_column(String(40), nullable=True)
    actual_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime().with_variant(DATETIME(fsp=6), "mysql"),
        nullable=True,
    )
    evidence_identity_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    strategy_policy_fingerprint: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    execution_policy_fingerprint: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    code_revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reconstruction_batch_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    provenance_schema_version: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    classification_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    legacy_scope_json: Mapped[str | None] = mapped_column(
        LONGTEXT().with_variant(Text(), "sqlite"),
        nullable=True,
    )

    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    universe_count: Mapped[int] = mapped_column(Integer, default=0)
    recommended_count: Mapped[int] = mapped_column(Integer, default=0)
    market_scope: Mapped[str] = mapped_column(String(30), default="all")
    market_scope_value: Mapped[str | None] = mapped_column(String(120))
    risk_template: Mapped[str] = mapped_column(String(20), default="balanced")
    score_line: Mapped[float] = mapped_column(Float, default=72.0)
    output_count: Mapped[int] = mapped_column(Integer, default=6)
    selected_skills_json: Mapped[str] = mapped_column(MEDIUMTEXT().with_variant(Text(), "sqlite"), default="[]")
    params_json: Mapped[str] = mapped_column(MEDIUMTEXT().with_variant(Text(), "sqlite"), default="{}")
    market_snapshot_json: Mapped[str] = mapped_column(MEDIUMTEXT().with_variant(Text(), "sqlite"), default="{}")
    result_summary_json: Mapped[str] = mapped_column(MEDIUMTEXT().with_variant(Text(), "sqlite"), default="{}")
    filter_audit_json: Mapped[str] = mapped_column(LONGTEXT().with_variant(Text(), "sqlite"), default="{}")
    status: Mapped[str] = mapped_column(String(30), default="running")
    error_message: Mapped[str | None] = mapped_column(Text)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    progress_phase: Mapped[str | None] = mapped_column(String(80), nullable=True)
    progress_current: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    progress_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    interrupted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    checkpoint_phase: Mapped[str | None] = mapped_column(String(80), nullable=True)

    # Versioned workbench lifecycle (V002_workbench_run_lifecycle).  These
    # additive columns provide immutable request identity, CAS revision and
    # worker-lease evidence while preserving all existing recommendation rows.
    request_schema_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_json: Mapped[str | None] = mapped_column(LONGTEXT().with_variant(Text(), "sqlite"), nullable=True)
    execution_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    run_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lifecycle_phase: Mapped[str | None] = mapped_column(String(32), nullable=True)
    checkpoint_side_effect_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    lease_owner_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_acquired_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    stable_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)


class RecommendationEvidenceClassificationAudit(Base):
    """Reviewed, append-only storage for legacy evidence classification facts."""

    __tablename__ = "recommendation_evidence_classification_audits"
    __table_args__ = (
        Index(
            "ix_rec_evidence_audit_plan_class",
            "plan_hash",
            "planned_evidence_class",
        ),
    )

    # Declaration order preserves the reviewed composite primary-key order.
    plan_hash: Mapped[str] = mapped_column(CHAR(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(60), primary_key=True)
    planned_evidence_class: Mapped[str] = mapped_column(String(40))
    classification_reason: Mapped[str] = mapped_column(String(80))
    legacy_evaluation_scope: Mapped[str | None] = mapped_column(String(32), nullable=True)
    legacy_started_at: Mapped[datetime | None] = mapped_column(
        DateTime().with_variant(DATETIME(fsp=6), "mysql"),
        nullable=True,
    )
    legacy_trade_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    legacy_recommended_at_min: Mapped[datetime | None] = mapped_column(
        DateTime().with_variant(DATETIME(fsp=6), "mysql"),
        nullable=True,
    )
    legacy_recommended_at_max: Mapped[datetime | None] = mapped_column(
        DateTime().with_variant(DATETIME(fsp=6), "mysql"),
        nullable=True,
    )
    recommendation_count: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )
    outcome_count: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )
    planned_at: Mapped[datetime] = mapped_column(
        DateTime().with_variant(DATETIME(fsp=6), "mysql"),
        nullable=False,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime().with_variant(DATETIME(fsp=6), "mysql"),
        nullable=True,
    )
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime().with_variant(DATETIME(fsp=6), "mysql"),
        nullable=True,
    )


class RecommendationCollectionSeal(Base):
    """Immutable identity of the recommendation rows persisted by one run."""

    __tablename__ = "recommendation_collection_seals"
    __table_args__ = (
        Index(
            "uq_rec_collection_seals_side_effect_key",
            "checkpoint_side_effect_key",
            unique=True,
        ),
    )

    run_id: Mapped[str] = mapped_column(String(60), primary_key=True)
    schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    recommendation_row_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    collection_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    checkpoint_side_effect_key: Mapped[str] = mapped_column(String(160), nullable=False)
    sealed_at: Mapped[datetime] = mapped_column(
        DateTime().with_variant(DATETIME(fsp=6), "mysql"),
        nullable=False,
    )


class Recommendation(Base):
    __tablename__ = "recommendations"

    recommendation_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(60), index=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    recommended_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    code: Mapped[str] = mapped_column(String(6), index=True)
    name: Mapped[str] = mapped_column(String(80))
    industry: Mapped[str | None] = mapped_column(String(80))
    recommend_price: Mapped[float] = mapped_column(Float, default=0)
    bucket: Mapped[str] = mapped_column(String(64), default="")
    final_score: Mapped[float] = mapped_column(Float)
    consensus_score: Mapped[float] = mapped_column(Float, default=0)
    setup_type: Mapped[str | None] = mapped_column(String(80))
    entry_rule: Mapped[str | None] = mapped_column(Text)
    invalid_rule: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)
    risk_tags_json: Mapped[str] = mapped_column(MEDIUMTEXT().with_variant(Text(), "sqlite"), default="[]")
    info_tags_json: Mapped[str] = mapped_column(MEDIUMTEXT().with_variant(Text(), "sqlite"), default="[]")
    recent_repeat: Mapped[bool] = mapped_column(Boolean, default=False)
    feature_snapshot_json: Mapped[str] = mapped_column(LONGTEXT().with_variant(Text(), "sqlite"), default="{}")
    market_context_json: Mapped[str] = mapped_column(MEDIUMTEXT().with_variant(Text(), "sqlite"), default="{}")
    skill_breakdown_json: Mapped[str] = mapped_column(MEDIUMTEXT().with_variant(Text(), "sqlite"), default="[]")
    agent_summary_json: Mapped[str] = mapped_column(MEDIUMTEXT().with_variant(Text(), "sqlite"), default="{}")
    status: Mapped[str] = mapped_column(String(30), default="pending")


class RecommendationOutcome(Base):
    __tablename__ = "recommendation_outcomes"

    recommendation_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    calculation_version_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    current_calculation_fingerprint: Mapped[str | None] = mapped_column(
        CHAR(64), nullable=True, index=True
    )
    projection_revision: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    projected_at: Mapped[datetime | None] = mapped_column(
        DateTime().with_variant(DATETIME(fsp=6), "mysql"), nullable=True
    )
    entry_policy_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    horizon_days: Mapped[int] = mapped_column(Integer, default=5)
    entry_triggered: Mapped[bool] = mapped_column(Boolean, default=True)
    entry_date: Mapped[str | None] = mapped_column(String(10))
    entry_price: Mapped[float | None] = mapped_column(Float)
    exit_date: Mapped[str | None] = mapped_column(String(10))
    exit_price: Mapped[float | None] = mapped_column(Float)
    executed_return: Mapped[float] = mapped_column(Float, default=0)
    weighted_return: Mapped[float] = mapped_column(Float, default=0)
    position_size: Mapped[float] = mapped_column(Float, default=1.0)
    holding_days: Mapped[int] = mapped_column(Integer, default=0)
    entry_slippage_bps: Mapped[float] = mapped_column(Float, default=0)
    exit_slippage_bps: Mapped[float] = mapped_column(Float, default=0)
    stop_loss_price: Mapped[float | None] = mapped_column(Float)
    take_profit_price: Mapped[float | None] = mapped_column(Float)
    exit_reason: Mapped[str | None] = mapped_column(String(40))
    max_return: Mapped[float] = mapped_column(Float, default=0)
    min_return: Mapped[float] = mapped_column(Float, default=0)
    close_return: Mapped[float] = mapped_column(Float, default=0)
    achievable_return: Mapped[float] = mapped_column(Float, default=0)
    max_drawdown: Mapped[float] = mapped_column(Float, default=0)
    hit_target: Mapped[bool] = mapped_column(Boolean, default=False)
    hit_invalid: Mapped[bool] = mapped_column(Boolean, default=False)
    outcome_label: Mapped[str] = mapped_column(String(30), default="pending")
    outcome_label_legacy: Mapped[str] = mapped_column(String(30), default="pending")
    outcome_reason: Mapped[str | None] = mapped_column(Text)
    label_source: Mapped[str] = mapped_column(String(20), default="auto")
    labeled_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    # 0026：当前投影所用规则版本；original_* 为首次重投影前的原始结算（只写一次，不覆盖）
    rule_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    bars_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    original_outcome_label: Mapped[str | None] = mapped_column(String(30), nullable=True)
    original_exit_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    original_exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    original_exit_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    original_executed_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    original_rule_version: Mapped[str | None] = mapped_column(String(80), nullable=True)


class RecommendationOutcomeVersion(Base):
    """Append-only calculation evidence; RecommendationOutcome is only the current projection."""

    __tablename__ = "recommendation_outcome_versions"

    version_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    recommendation_id: Mapped[str] = mapped_column(String(80), index=True)
    calculation_version: Mapped[str] = mapped_column(String(80))
    entry_policy_id: Mapped[str] = mapped_column(String(80))
    horizon_days: Mapped[int] = mapped_column(Integer)
    label_source: Mapped[str] = mapped_column(String(20))
    result_json: Mapped[str] = mapped_column(LONGTEXT().with_variant(Text(), "sqlite"))
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)
    calculation_fingerprint: Mapped[str | None] = mapped_column(
        CHAR(64), nullable=True, unique=True
    )
    result_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    recommendation_identity_hash: Mapped[str | None] = mapped_column(
        CHAR(64), nullable=True
    )
    market_snapshot_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    market_artifact_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    execution_policy_fingerprint: Mapped[str | None] = mapped_column(
        CHAR(64), nullable=True
    )
    calculation_code_revision: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    label_policy_fingerprint: Mapped[str | None] = mapped_column(
        CHAR(64), nullable=True
    )
    calculation_schema_version: Mapped[int | None] = mapped_column(
        SmallInteger, nullable=True
    )
    validity_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    legacy_verification_status: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )


class RecommendationExecutionState(Base):
    """推荐实时买入确认的最新状态与首次命中事实。"""

    __tablename__ = "recommendation_execution_states"

    recommendation_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    latest_decision_code: Mapped[str] = mapped_column(String(20))
    latest_decision_label: Mapped[str] = mapped_column(String(80), default="")
    latest_reason: Mapped[str | None] = mapped_column(Text)
    latest_checked_at: Mapped[datetime] = mapped_column(DateTime)
    latest_quote_time: Mapped[str | None] = mapped_column(String(40))
    latest_data_as_of: Mapped[str | None] = mapped_column(String(10))
    latest_price: Mapped[float | None] = mapped_column(Float)
    latest_strategy_id: Mapped[str | None] = mapped_column(String(80))
    latest_setup_match: Mapped[str | None] = mapped_column(String(120))
    latest_condition_json: Mapped[str] = mapped_column(MEDIUMTEXT().with_variant(Text(), "sqlite"), default="{}")
    ever_buy_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    first_buy_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    first_buy_confirmed_price: Mapped[float | None] = mapped_column(Float)
    cancelled_before_entry: Mapped[bool] = mapped_column(Boolean, default=False)
    first_cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)
    first_cancelled_price: Mapped[float | None] = mapped_column(Float)
    first_cancel_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)


class ReviewNote(Base):
    __tablename__ = "review_notes"
    __table_args__ = (UniqueConstraint("recommendation_id", name="uq_review_note_recommendation"),)

    note_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    recommendation_id: Mapped[str] = mapped_column(String(80), index=True)
    user_label: Mapped[str | None] = mapped_column(String(80))
    failure_reason: Mapped[str | None] = mapped_column(String(120))
    review_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class WatchItem(Base):
    __tablename__ = "watch_items"
    __table_args__ = (UniqueConstraint("source_recommendation_id", name="uq_watch_recommendation"),)

    watch_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    source_run_id: Mapped[str] = mapped_column(String(60), index=True)
    source_recommendation_id: Mapped[str] = mapped_column(String(80), index=True)
    watch_status: Mapped[str] = mapped_column(String(20), default="watching")
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class AgentDecisionLog(Base):
    __tablename__ = "agent_decision_logs"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    recommendation_id: Mapped[str | None] = mapped_column(String(80), index=True)
    run_id: Mapped[str | None] = mapped_column(String(60), index=True)
    agent_name: Mapped[str] = mapped_column(String(80))
    output_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class IntradayLockMonitor(Base):
    """盘中锁利影子监控任务(只记录不交易)。每个精选持仓一条监控任务。"""

    __tablename__ = "intraday_lock_monitors"

    monitor_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    recommendation_id: Mapped[str] = mapped_column(String(80), index=True)
    code: Mapped[str] = mapped_column(String(6))
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    entry_date: Mapped[str] = mapped_column(String(10))
    entry_price: Mapped[float] = mapped_column(Float)
    target_pct: Mapped[float] = mapped_column(Float)
    lock_ratio: Mapped[float] = mapped_column(Float, default=0.5)
    status: Mapped[str] = mapped_column(String(20), index=True, default="monitoring")
    triggered_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class IntradayLockSignal(Base):
    """盘中锁利信号。触及锁利目标时记录,盘后回填走势并与实际出场对比。"""

    __tablename__ = "intraday_lock_signals"

    signal_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    monitor_id: Mapped[str] = mapped_column(String(80), index=True)
    code: Mapped[str] = mapped_column(String(6))
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    target_pct: Mapped[float] = mapped_column(Float)
    lock_ratio: Mapped[float] = mapped_column(Float, default=0.5)
    trigger_time: Mapped[str] = mapped_column(String(40))
    trigger_price: Mapped[float] = mapped_column(Float)
    post_trigger_max_pct: Mapped[float | None] = mapped_column(Float)
    post_trigger_min_pct: Mapped[float | None] = mapped_column(Float)
    actual_exit_price: Mapped[float | None] = mapped_column(Float)
    actual_exit_return: Mapped[float | None] = mapped_column(Float)
    lock_delta_pct: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), index=True, default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
