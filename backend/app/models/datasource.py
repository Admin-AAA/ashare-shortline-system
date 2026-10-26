"""ORM models: datasource domain (split from app.models.tables)."""
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
# 数据源管理（简化）
# ═════════════════════════════════════════════════════════════════════════════


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
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class DataSourceConfig(Base):
    __tablename__ = "data_source_config"

    source: Mapped[str] = mapped_column(String(40), primary_key=True)
    source_type: Mapped[str] = mapped_column(String(20), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    note: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class DataGatewayItemSwitch(Base):
    """数据项级网关开关：只在某个数据类型（dataset_key）里启停某个 provider。

    与 ``DataSourceConfig`` 的 (source, source_type) 总开关相互独立、叠加生效：
    总开关关 → 该源在所有数据类型里都跳过；这里关 → 只在 dataset_key 这一列跳过。
    没有行 = 启用。
    """

    __tablename__ = "data_gateway_item_switch"

    dataset_key: Mapped[str] = mapped_column(String(40), primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class ApiCallLog(Base):
    """LLM API 调用日志。"""
    __tablename__ = "api_call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str | None] = mapped_column(String(60), index=True)
    skill_id: Mapped[str | None] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(80))
    endpoint: Mapped[str] = mapped_column(String(200))
    prompt_chars: Mapped[int] = mapped_column(Integer, default=0)
    completion_chars: Mapped[int] = mapped_column(Integer, default=0)
    # 真实 token 用量（来自 provider usage 字段，None 表示本次调用未取得真实值）
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_hit_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(30), default="success")
    error_message: Mapped[str | None] = mapped_column(Text)
    # prompt context 投影/裁剪审计摘要（结构化 JSON，不含完整 prompt 原文）
    context_audit_json: Mapped[str | None] = mapped_column(
        LONGTEXT().with_variant(Text(), "sqlite")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class DataSourceRequestTrace(Base):
    """append-only 数据源请求 trace 摘要（R-SOURCE-3）。

    一行 = 一次最终请求（可能含多次 provider attempt / fallback）。
    不含 credential / 完整请求 token / 上游原始敏感错误正文。
    幂等键 (correlation_id, request_identity_hash) 唯一。
    """
    __tablename__ = "data_source_request_traces"

    trace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    correlation_id: Mapped[str] = mapped_column(String(80), index=True)
    operation_name: Mapped[str] = mapped_column(String(80), index=True)
    request_identity_hash: Mapped[str] = mapped_column(CHAR(64))
    contract_id: Mapped[str] = mapped_column(String(80))
    contract_version: Mapped[int] = mapped_column(Integer)
    selected_provider: Mapped[str | None] = mapped_column(String(40))
    final_outcome: Mapped[str] = mapped_column(String(20))
    attempt_count: Mapped[int] = mapped_column(Integer, default=1)
    fallback_count: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    telemetry_status: Mapped[str] = mapped_column(String(20), default="ok")
    started_at: Mapped[datetime] = mapped_column(
        DateTime().with_variant(DATETIME(fsp=6), "mysql"), index=True
    )
    completed_at: Mapped[datetime] = mapped_column(
        DateTime().with_variant(DATETIME(fsp=6), "mysql")
    )


class DataSourceAttempt(Base):
    """append-only 单次 provider attempt（R-SOURCE-3）。

    唯一键 (trace_id, ordinal)；同一 trace 的 attempts 原子写入。
    contract_validation_json 为结构化校验摘要，不含敏感正文。
    """
    __tablename__ = "data_source_attempts"

    attempt_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(80), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    outcome: Mapped[str] = mapped_column(String(20), index=True)
    duration_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    failure_category: Mapped[str | None] = mapped_column(String(40))
    retryable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    fallback_allowed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    fallback_from: Mapped[str | None] = mapped_column(String(40))
    fallback_to: Mapped[str | None] = mapped_column(String(40))
    field_fingerprint: Mapped[str | None] = mapped_column(CHAR(64))
    contract_validation_json: Mapped[str | None] = mapped_column(
        LONGTEXT().with_variant(Text(), "sqlite")
    )


class ApiRequestMetric(Base):
    """HTTP 端点请求延迟采样（G3 性能门禁数据源）。

    只记录 /api/ 的 GET 只读端点（驾驶舱关键路径），由 main.py 的请求计时
    middleware 顺手持久化；用于 self_check 计算 api_p95，替代硬编码 None。
    path 已归一化（路径参数折叠为 {}），避免高基数。
    """
    __tablename__ = "api_request_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(200))
    status_code: Mapped[int] = mapped_column(Integer, default=200)
    duration_ms: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)
