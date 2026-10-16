"""ORM models: ops domain (split from app.models.tables)."""
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



class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    backtest_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    skill_id: Mapped[str] = mapped_column(String(80), index=True)
    date_from: Mapped[str] = mapped_column(String(10))
    date_to: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(30), default="completed")
    result_json: Mapped[str] = mapped_column(MEDIUMTEXT().with_variant(Text(), "sqlite"), default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class CalibrationConfig(Base):
    __tablename__ = "calibration_config"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class ModelRouteConfig(Base):
    __tablename__ = "model_route_config"

    route_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    provider: Mapped[str] = mapped_column(String(40), default="openai_compatible")
    base_url: Mapped[str] = mapped_column(String(200), default="")
    model: Mapped[str] = mapped_column(String(120))
    api_key: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, default=1, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    # 独立配置参数（可选，为空时使用系统默认值）
    max_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    timeout_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class LLMReviewCache(Base):
    """LLM Skill 审查结果缓存。

    key = sha256(skill_hash + stock_feature_json + snapshot_json + rule_signal_json)
    相同输入直接复用历史 LLM 输出，不再调用 LLM。skill 文件改动 / 市场状态变化 /
    规则信号变化 → cache_key 自动失效。跨 run 持久化，日内重跑零 LLM 调用。
    """
    __tablename__ = "llm_review_cache"

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    skill_id: Mapped[str] = mapped_column(String(80), index=True)
    skill_hash: Mapped[str | None] = mapped_column(String(80))
    trade_date: Mapped[str | None] = mapped_column(String(10), index=True)
    code: Mapped[str | None] = mapped_column(String(6), index=True)
    review_json: Mapped[str] = mapped_column(LONGTEXT().with_variant(Text(), "sqlite"))
    model: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)


class ScheduledTask(Base):
    __tablename__ = "scheduled_tasks"

    task_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    task_type: Mapped[str] = mapped_column(String(40))
    cron_expression: Mapped[str] = mapped_column(String(80))
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_running: Mapped[bool] = mapped_column(Boolean, default=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_status: Mapped[str | None] = mapped_column(String(30))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class ScheduledTaskRun(Base):
    __tablename__ = "scheduled_task_runs"

    task_run_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(80), index=True)
    task_name: Mapped[str] = mapped_column(String(120))
    task_type: Mapped[str] = mapped_column(String(40))
    trigger_source: Mapped[str] = mapped_column(String(20), default="schedule")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(30), default="running")
    summary: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    payload_json: Mapped[str] = mapped_column(MEDIUMTEXT().with_variant(Text(), "sqlite"), default="{}")
    result_json: Mapped[str] = mapped_column(MEDIUMTEXT().with_variant(Text(), "sqlite"), default="{}")
