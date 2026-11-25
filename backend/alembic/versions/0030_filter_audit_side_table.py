"""把推荐 run 的过滤审计快照拆到独立表 recommendation_run_filter_audits。

recommendation_runs.filter_audit_json 平均 3MB/行、最大 9MB，2026-11-25 实测 1164 行
合计 3.5GB，占主表数据量 99%。主表因此 3.5GB，任何未显式 defer 的整行读取都要搬几 MB，
缓冲池也被它挤占。迁移把非空快照复制到独立表，再从主表删除该列。

- 复制：INSERT ... SELECT 在服务端完成，不经过客户端；空快照（'' 与 '{}'）不复制，
  读接口把「没有行」当作空审计。
- 删列：INPLACE 在线重建，期间允许读写，重建后主表占用从 3.5GB 降到约 2MB。
- 只对 MySQL 生效；SQLite（单测）由 Base.metadata.create_all 建表。
"""
from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME, LONGTEXT

revision = "0030_filter_audit_side_table"
down_revision = "0029_text_event_labels"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")

SIDE_TABLE = "recommendation_run_filter_audits"


def _has_table(bind, table: str) -> bool:
    return bool(bind.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema = DATABASE() AND table_name = :t"
    ), {"t": table}).scalar())


def _has_column(bind, table: str, column: str) -> bool:
    return bool(bind.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
    ), {"t": table, "c": column}).scalar())


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return
    # INPLACE 重建要短暂拿元数据锁；长事务占表时默认会一直等，后续读写全部排队。
    op.execute("SET SESSION lock_wait_timeout = 60")
    if not _has_table(bind, SIDE_TABLE):
        op.create_table(
            SIDE_TABLE,
            sa.Column("run_id", sa.String(length=60), primary_key=True),
            sa.Column("payload_json", LONGTEXT(), nullable=False),
            sa.Column("updated_at", DATETIME(fsp=6), nullable=True),
        )
    if not _has_column(bind, "recommendation_runs", "filter_audit_json"):
        return
    copied = bind.execute(sa.text(
        f"INSERT INTO {SIDE_TABLE} (run_id, payload_json, updated_at) "
        "SELECT run_id, filter_audit_json, UTC_TIMESTAMP(6) FROM recommendation_runs "
        "WHERE filter_audit_json IS NOT NULL AND filter_audit_json NOT IN ('', '{}') "
        "ON DUPLICATE KEY UPDATE payload_json = VALUES(payload_json)"
    )).rowcount
    log.info("filter audit 快照复制 %s 行", copied)
    op.execute(
        "ALTER TABLE recommendation_runs DROP COLUMN filter_audit_json, "
        "ALGORITHM=INPLACE, LOCK=NONE"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return
    if _has_column(bind, "recommendation_runs", "filter_audit_json"):
        return
    op.execute(
        "ALTER TABLE recommendation_runs ADD COLUMN filter_audit_json LONGTEXT NULL, "
        "ALGORITHM=INSTANT"
    )
    if _has_table(bind, SIDE_TABLE):
        bind.execute(sa.text(
            "UPDATE recommendation_runs r JOIN recommendation_run_filter_audits a "
            "ON a.run_id = r.run_id SET r.filter_audit_json = a.payload_json"
        ))
