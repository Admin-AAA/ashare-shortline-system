"""share_floats.float_ratio 索引：解禁比例口径检查从全表扫描降到索引区间扫描。

数据质量巡检的 check_share_float_ratio 查询 `WHERE float_ratio > 100 ORDER BY float_ratio DESC
LIMIT 200`。share_floats 有 312 万行、float_ratio 上没有索引，每次都要全表扫描再排序，
实测 0.6~2.3 秒，占整轮巡检约一成时间。加上索引后区间为空时直接返回，异常行存在时
反向索引扫描取前 200 行即可。

- 建索引走 INPLACE, LOCK=NONE，期间允许读写。
- 只对 MySQL 生效；SQLite（单测）由模型上的 index=True 建索引。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0031_share_float_ratio_index"
down_revision = "0030_filter_audit_side_table"
branch_labels = None
depends_on = None

TABLE = "share_floats"
INDEX = "ix_share_floats_float_ratio"


def _has_index(bind) -> bool:
    return bool(bind.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.statistics "
        "WHERE table_schema = DATABASE() AND table_name = :t AND index_name = :i"
    ), {"t": TABLE, "i": INDEX}).scalar())


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return
    op.execute("SET SESSION lock_wait_timeout = 60")
    if _has_index(bind):
        return
    op.execute(
        f"ALTER TABLE {TABLE} ADD INDEX {INDEX} (float_ratio), ALGORITHM=INPLACE, LOCK=NONE"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return
    if not _has_index(bind):
        return
    op.execute(f"ALTER TABLE {TABLE} DROP INDEX {INDEX}, ALGORITHM=INPLACE, LOCK=NONE")
