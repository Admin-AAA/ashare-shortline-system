"""recommendation_runs / recommendations: 由 MySQL 自动维护的行更新时间 updated_at（微秒）+ 索引。

用途：读接口缓存的「数据版本指纹」（app/services/data_version.py）。原指纹要读运行表的大 JSON 列
（约 32MB），MySQL 缓冲池被别的大查询挤占时一次要 0.4~0.7 秒；改为只读 (主键, updated_at) 的二级索引，
永远很小。

- DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)：任何 INSERT / UPDATE（含 ORM、原生 SQL、
  executemany、INSERT ... ON DUPLICATE KEY UPDATE）只要真改了某列，MySQL 就会自动刷新这一列；
  ORM 模型里不声明这一列，应用代码也不写它，所以不会被显式赋值覆盖。
- 存量行：加列时统一取迁移执行时刻（同一个值）。指纹用「行数 + 每行 (主键, updated_at) 的 CRC 异或」，
  不依赖存量值互不相同，增 / 删 / 改都会让指纹变化。
- 加列优先 ALGORITHM=INSTANT（只改数据字典，秒级、不重建表）；服务器不支持时退回
  ALGORITHM=INPLACE, LOCK=NONE（在线重建，期间允许读写）。建索引用 INPLACE, LOCK=NONE。
- 只对 MySQL 生效；SQLite（单测）跳过，指纹在非 MySQL 上本来就不启用。
"""
from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

revision = "0028_row_updated_at"
down_revision = "0027_gateway_item_switch"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")

TABLES = ("recommendation_runs", "recommendations")
COLUMN = "updated_at"
_DDL = "DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)"


def _index_name(table: str) -> str:
    return f"ix_{table}_{COLUMN}"


def _has_column(bind, table: str) -> bool:
    return bool(bind.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
    ), {"t": table, "c": COLUMN}).scalar())


def _has_index(bind, table: str) -> bool:
    return bool(bind.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.statistics "
        "WHERE table_schema = DATABASE() AND table_name = :t AND index_name = :i"
    ), {"t": table, "i": _index_name(table)}).scalar())


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return
    # INSTANT / INPLACE 都要短暂拿表的元数据锁（MDL）。若有长事务正占着这两张表，默认会一直等，
    # 等待期间后续对这张表的读写也会排队卡住。限定 20 秒拿不到就报错退出（迁移可重跑，已加的列/索引会跳过）。
    op.execute("SET SESSION lock_wait_timeout = 20")
    for table in TABLES:
        if not _has_column(bind, table):
            try:
                op.execute(f"ALTER TABLE `{table}` ADD COLUMN `{COLUMN}` {_DDL}, ALGORITHM=INSTANT")
                log.info("%s.%s added with ALGORITHM=INSTANT", table, COLUMN)
            except sa.exc.DBAPIError as exc:  # 服务器不支持 INSTANT 这种加法时退回在线重建
                log.warning("%s: INSTANT not supported (%s); falling back to INPLACE, LOCK=NONE", table, exc.orig)
                op.execute(f"ALTER TABLE `{table}` ADD COLUMN `{COLUMN}` {_DDL}, ALGORITHM=INPLACE, LOCK=NONE")
        if not _has_index(bind, table):
            op.execute(
                f"ALTER TABLE `{table}` ADD INDEX `{_index_name(table)}` (`{COLUMN}`), ALGORITHM=INPLACE, LOCK=NONE"
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return
    for table in TABLES:
        if _has_index(bind, table):
            op.execute(f"ALTER TABLE `{table}` DROP INDEX `{_index_name(table)}`")
        if _has_column(bind, table):
            op.execute(f"ALTER TABLE `{table}` DROP COLUMN `{COLUMN}`")
