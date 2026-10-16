"""ORM models: backtest domain (split from app.models.tables)."""
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



# ═════════════════════════════════════════════════════════════════════════════
# P0 研究回测数据表
#
# 这些表与 Recommendation/RecommendationOutcome 和旧 backtest_runs 隔离。
# 研究回测的可重放输入、attempt 认领与实验结论均只写入本组专属表。
# ═════════════════════════════════════════════════════════════════════════════

# MySQL 需要保留足够的 JSON/BLOB 容量，SQLite 单元测试则使用通用兼容类型。
_BACKTEST_DATETIME = DATETIME(fsp=6).with_variant(DateTime(), "sqlite")
_BACKTEST_LONGTEXT = LONGTEXT().with_variant(Text(), "sqlite")
_BACKTEST_LONGBLOB = LONGBLOB().with_variant(LargeBinary(), "sqlite")


class BacktestDataSnapshot(Base):
    """一次研究回测实际读取数据分区的不可变清单。"""

    __tablename__ = "backtest_data_snapshots"
    __table_args__ = (
        UniqueConstraint("manifest_hash", name="uq_backtest_snapshot_manifest"),
    )

    snapshot_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    snapshot_version: Mapped[int] = mapped_column(Integer)
    manifest_hash: Mapped[str] = mapped_column(CHAR(64))
    partition_manifest_json: Mapped[str] = mapped_column(_BACKTEST_LONGTEXT)
    coverage_json: Mapped[str] = mapped_column(_BACKTEST_LONGTEXT)
    missing_requirements_json: Mapped[str] = mapped_column(_BACKTEST_LONGTEXT)
    created_at: Mapped[datetime] = mapped_column(_BACKTEST_DATETIME, default=utcnow_naive, index=True)


class BacktestHoldoutClaim(Base):
    """Concurrency-safe consumption ledger for research holdout partitions."""

    __tablename__ = "backtest_holdout_claims"

    holdout_fingerprint: Mapped[str] = mapped_column(CHAR(64), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(String(80), index=True)
    phase: Mapped[str] = mapped_column(String(24), default="untouched_holdout")
    status: Mapped[str] = mapped_column(String(24), default="claimed")
    claimed_at: Mapped[datetime] = mapped_column(_BACKTEST_DATETIME, default=utcnow_naive)
    revealed_at: Mapped[datetime | None] = mapped_column(_BACKTEST_DATETIME, nullable=True)
    reveal_fingerprint: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)


class BacktestInputArtifact(Base):
    """按信号日保存的、带摘要校验的不可变策略/结果输入。"""

    __tablename__ = "backtest_input_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "signal_date",
            "artifact_scope",
            "artifact_type",
            name="uq_backtest_artifact",
        ),
    )

    artifact_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(String(80), index=True)
    signal_date: Mapped[str] = mapped_column(String(10), index=True)
    artifact_scope: Mapped[str] = mapped_column(String(24))
    artifact_type: Mapped[str] = mapped_column(String(48))
    schema_version: Mapped[int] = mapped_column(Integer)
    row_count: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(CHAR(64))
    content_encoding: Mapped[str] = mapped_column(String(24))
    payload: Mapped[bytes] = mapped_column(_BACKTEST_LONGBLOB)
    created_at: Mapped[datetime] = mapped_column(_BACKTEST_DATETIME, default=utcnow_naive)


class BacktestRequestClaim(Base):
    """按语义请求指纹串行化回测 attempt 的并发认领记录。"""

    __tablename__ = "backtest_request_claims"

    request_fingerprint: Mapped[str] = mapped_column(CHAR(64), primary_key=True)
    active_backtest_id: Mapped[str | None] = mapped_column(String(80), index=True)
    completed_backtest_id: Mapped[str | None] = mapped_column(String(80), index=True)
    next_attempt_no: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(_BACKTEST_DATETIME, default=utcnow_naive)


class BacktestRunV2(Base):
    """单策略研究回测 attempt；不复用历史兼容读模型 ``backtest_runs``。"""

    __tablename__ = "backtest_runs_v2"
    __table_args__ = (
        UniqueConstraint("request_fingerprint", "attempt_no", name="uq_backtest_request_attempt"),
    )

    backtest_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    experiment_id: Mapped[str | None] = mapped_column(String(80), index=True)
    parent_backtest_id: Mapped[str | None] = mapped_column(String(80), index=True)
    request_fingerprint: Mapped[str] = mapped_column(CHAR(64), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer)
    snapshot_id: Mapped[str] = mapped_column(String(80), index=True)
    strategy_id: Mapped[str] = mapped_column(String(80), index=True)
    strategy_version: Mapped[str] = mapped_column(String(160))
    request_schema_version: Mapped[int] = mapped_column(Integer)
    request_json: Mapped[str] = mapped_column(_BACKTEST_LONGTEXT)
    execution_version: Mapped[str] = mapped_column(String(128))
    execution_policy_fingerprint: Mapped[str] = mapped_column(CHAR(64), index=True)
    signal_date: Mapped[str | None] = mapped_column(String(10), index=True)
    date_from: Mapped[str | None] = mapped_column(String(10))
    date_to: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(40), index=True)
    result_json: Mapped[str] = mapped_column(_BACKTEST_LONGTEXT)
    started_at: Mapped[datetime | None] = mapped_column(_BACKTEST_DATETIME)
    finished_at: Mapped[datetime | None] = mapped_column(_BACKTEST_DATETIME)
    created_at: Mapped[datetime] = mapped_column(_BACKTEST_DATETIME, default=utcnow_naive, index=True)


class BacktestCandidate(Base):
    """研究回测的信号/无信号候选与过滤审计，按 backtest 隔离。"""

    __tablename__ = "backtest_candidates"
    __table_args__ = (
        UniqueConstraint("backtest_id", "signal_date", "code", name="uq_backtest_candidate"),
    )

    candidate_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    backtest_id: Mapped[str] = mapped_column(String(80), index=True)
    signal_date: Mapped[str] = mapped_column(String(10), index=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    decision_status: Mapped[str] = mapped_column(String(32))
    signal_json: Mapped[str | None] = mapped_column(_BACKTEST_LONGTEXT)
    audit_json: Mapped[str] = mapped_column(_BACKTEST_LONGTEXT)
    input_artifact_id: Mapped[str] = mapped_column(String(100), index=True)


class BacktestTrade(Base):
    """研究回测的逐笔交易；不会写入正式 RecommendationOutcome。"""

    __tablename__ = "backtest_trades"
    __table_args__ = (
        UniqueConstraint("backtest_id", "signal_date", "code", name="uq_backtest_trade"),
    )

    trade_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    backtest_id: Mapped[str] = mapped_column(String(80), index=True)
    signal_date: Mapped[str] = mapped_column(String(10), index=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    entry_date: Mapped[str | None] = mapped_column(String(10))
    exit_date: Mapped[str | None] = mapped_column(String(10))
    outcome_label: Mapped[str] = mapped_column(String(32))
    trade_json: Mapped[str] = mapped_column(_BACKTEST_LONGTEXT)


class BacktestFailureAttribution(Base):
    """亏损/失败成交的结构化归因及其可审计证据引用。"""

    __tablename__ = "backtest_failure_attributions"
    __table_args__ = (
        UniqueConstraint("trade_id", "reason_code", name="uq_backtest_trade_attribution"),
    )

    attribution_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    backtest_id: Mapped[str] = mapped_column(String(80), index=True)
    trade_id: Mapped[str] = mapped_column(String(100), index=True)
    reason_code: Mapped[str] = mapped_column(String(48), index=True)
    rule_or_factor_reference: Mapped[str | None] = mapped_column(String(160))
    evidence_references_json: Mapped[str] = mapped_column(_BACKTEST_LONGTEXT)
    confidence: Mapped[float] = mapped_column(Float)
    is_unknown: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(_BACKTEST_DATETIME, default=utcnow_naive)


class BacktestExperiment(Base):
    """冻结快照、成本 policy 和时间切分的候选策略对照实验。"""

    __tablename__ = "backtest_experiments"

    experiment_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    hypothesis: Mapped[str] = mapped_column(Text)
    baseline_backtest_id: Mapped[str] = mapped_column(String(80), index=True)
    snapshot_id: Mapped[str] = mapped_column(String(80), index=True)
    execution_policy_fingerprint: Mapped[str] = mapped_column(CHAR(64), index=True)
    split_hash: Mapped[str] = mapped_column(CHAR(64))
    split_json: Mapped[str] = mapped_column(_BACKTEST_LONGTEXT)
    candidate_strategy_id: Mapped[str] = mapped_column(String(80), index=True)
    candidate_strategy_version: Mapped[str] = mapped_column(String(160))
    frozen_candidate_fingerprint: Mapped[str] = mapped_column(CHAR(64))
    acceptance_json: Mapped[str] = mapped_column(_BACKTEST_LONGTEXT)
    status: Mapped[str] = mapped_column(String(32), index=True)
    conclusion_json: Mapped[str | None] = mapped_column(_BACKTEST_LONGTEXT)
    created_at: Mapped[datetime] = mapped_column(_BACKTEST_DATETIME, default=utcnow_naive)
    frozen_at: Mapped[datetime | None] = mapped_column(_BACKTEST_DATETIME)
    finished_at: Mapped[datetime | None] = mapped_column(_BACKTEST_DATETIME)
