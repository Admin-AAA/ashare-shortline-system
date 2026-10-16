"""ORM models: strategy domain (split from app.models.tables)."""
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
    installed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class StrategyVersionSnapshot(Base):
    """Immutable, self-contained strategy artifact captured for one version id."""

    __tablename__ = "strategy_version_snapshots"
    __table_args__ = (
        Index("idx_strategy_version_snapshots_strategy_created", "strategy_id", "created_at"),
    )

    version_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(80))
    snapshot_json: Mapped[str] = mapped_column(LONGTEXT().with_variant(Text(), "sqlite"))
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class Preset(Base):
    __tablename__ = "presets"

    preset_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    run_mode: Mapped[str] = mapped_column(String(20), default="formal")  # 保留列，不再用于业务逻辑
    skip_aux_refresh: Mapped[bool] = mapped_column(Boolean, default=False)
    market_scope: Mapped[str] = mapped_column(String(30), default="all")
    market_scope_value: Mapped[str | None] = mapped_column(String(120))
    risk_template: Mapped[str] = mapped_column(String(20), default="balanced")
    score_line: Mapped[float] = mapped_column(Float, default=72.0)
    output_count: Mapped[int] = mapped_column(Integer, default=6)
    bucket_limits_json: Mapped[str] = mapped_column(Text, default="{}")
    data_source: Mapped[str] = mapped_column(String(20), default="local_pool_realtime")
    hide_recent_repeats: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class PresetSkill(Base):
    __tablename__ = "preset_skills"
    __table_args__ = (UniqueConstraint("preset_id", "skill_id", name="uq_preset_skill"),)

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    preset_id: Mapped[str] = mapped_column(String(80), index=True)
    skill_id: Mapped[str] = mapped_column(String(80), index=True)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
