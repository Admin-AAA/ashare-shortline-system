"""删除 minute_kbars 的冗余索引 idx_mk_code_time (code, trade_time)。

生产库上 minute_kbars 有 5713 万行、索引 9.96GB，其中 idx_mk_code_time 占 2.49GB。
所有读取分钟K线的查询都同时带 code 与 freq（`temporal_consistency.py` 的两处查询
都固定 `freq == "15min"`），唯一键 uq_minute_kbar_code_freq_time (code, freq,
trade_time) 已能覆盖 code 前缀过滤与 trade_time 排序；实测强制走唯一键 8.08ms，
强制走 idx_mk_code_time 115.75ms，后者没有独立价值。

只对 MySQL 生效；SQLite（单测）由 Base.metadata.create_all 按模型建表，模型已同步删除该索引。
"""
from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

revision = "0034_drop_redundant_minute_index"
down_revision = "0033_filter_audit_compressed"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")

TABLE = "minute_kbars"
INDEX = "idx_mk_code_time"
COVERING_INDEX = "uq_minute_kbar_code_freq_time"


def _has_index(bind, name: str) -> bool:
    return bool(bind.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.statistics "
        "WHERE table_schema = DATABASE() AND table_name = :t AND index_name = :i"
    ), {"t": TABLE, "i": name}).scalar())


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return
    if not _has_index(bind, COVERING_INDEX):
        log.warning("覆盖索引 %s 不存在，跳过删除 %s", COVERING_INDEX, INDEX)
        return
    if not _has_index(bind, INDEX):
        return
    op.execute("SET SESSION lock_wait_timeout = 60")
    op.execute(f"ALTER TABLE {TABLE} DROP INDEX {INDEX}, ALGORITHM=INPLACE, LOCK=NONE")
    log.info("已删除冗余索引 %s", INDEX)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return
    if _has_index(bind, INDEX):
        return
    op.execute(
        f"ALTER TABLE {TABLE} ADD INDEX {INDEX} (code, trade_time), ALGORITHM=INPLACE, LOCK=NONE"
    )
