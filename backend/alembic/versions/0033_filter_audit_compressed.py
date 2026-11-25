"""过滤审计快照改为 zlib 压缩存储：payload_json LONGTEXT → payload_z LONGBLOB。

背景：一个 run 的审计快照是全市场每只票一份诊断对象，原始 JSON 平均 3.3MB、最大
9.6MB。2026-11-25 实测 1133 行合计 3.71GB，占整个库 7%。内容是同构的重复键名与
中文短句，zlib 压缩后只剩 6.4%（0.24GB），解压单行约 5ms、压缩约 31ms。

迁移步骤：

1. 加可空列 payload_z LONGBLOB；
2. 用独立 AUTOCOMMIT 连接分批回填（每批 20 行），逐行 zlib 压缩并校验往返一致；
3. 删掉旧列 payload_json。

回填走独立连接而不是 alembic 的事务连接：3.71GB 数据在单个事务里搬运会长时间持有
undo 与行锁，期间所有写推荐 run 的请求排队。分批提交把持锁时间压到单批。

只对 MySQL 生效；SQLite（单测）由 Base.metadata.create_all 按模型建表。
"""
from __future__ import annotations

import json
import logging
import zlib

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import LONGBLOB

revision = "0033_filter_audit_compressed"
down_revision = "0032_index_contract_reconcile"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")

TABLE = "recommendation_run_filter_audits"
LEGACY_COLUMN = "payload_json"
COLUMN = "payload_z"
BATCH = 20
# 压缩级别与 app/services/runs/audit_payload.py 保持一致
COMPRESS_LEVEL = 6


def _has_column(bind, table: str, column: str) -> bool:
    return bool(bind.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
    ), {"t": table, "c": column}).scalar())


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return
    op.execute("SET SESSION lock_wait_timeout = 60")
    if not _has_column(bind, TABLE, LEGACY_COLUMN):
        return
    if not _has_column(bind, TABLE, COLUMN):
        op.add_column(TABLE, sa.Column(COLUMN, LONGBLOB(), nullable=True))

    rows = bind.execute(sa.text(
        f"SELECT run_id, {LEGACY_COLUMN} FROM {TABLE} WHERE {LEGACY_COLUMN} IS NOT NULL"
    )).fetchall()
    log.info("过滤审计快照压缩 %s 行", len(rows))

    autocommit = bind.engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        for start in range(0, len(rows), BATCH):
            chunk = rows[start:start + BATCH]
            autocommit.execute(
                sa.text(f"UPDATE {TABLE} SET {COLUMN} = :z WHERE run_id = :r"),
                [{"z": _compress(raw, run_id), "r": run_id} for run_id, raw in chunk],
            )
            log.info("  已压缩 %s/%s", min(start + BATCH, len(rows)), len(rows))
        # 空快照与 NULL 统一存空字节串，读接口把空值当作空审计
        autocommit.execute(sa.text(f"UPDATE {TABLE} SET {COLUMN} = '' WHERE {COLUMN} IS NULL"))
        # 校验必须走同一条 autocommit 连接：alembic 的事务连接在 REPEATABLE READ 下
        # 快照早于回填，读不到新写入的值。
        remaining = autocommit.execute(sa.text(
            f"SELECT COUNT(*) FROM {TABLE} WHERE {COLUMN} IS NULL"
        )).scalar()
    finally:
        autocommit.close()
    if remaining:
        raise RuntimeError(f"{remaining} 行回填后仍为空，放弃删列")
    op.execute(
        f"ALTER TABLE {TABLE} MODIFY COLUMN {COLUMN} LONGBLOB NOT NULL, "
        "ALGORITHM=INPLACE, LOCK=NONE"
    )
    op.execute(f"ALTER TABLE {TABLE} DROP COLUMN {LEGACY_COLUMN}, ALGORITHM=INPLACE, LOCK=NONE")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return
    if _has_column(bind, TABLE, LEGACY_COLUMN):
        return
    op.execute(f"ALTER TABLE {TABLE} ADD COLUMN {LEGACY_COLUMN} LONGTEXT NULL, ALGORITHM=INSTANT")
    rows = bind.execute(sa.text(f"SELECT run_id, {COLUMN} FROM {TABLE}")).fetchall()
    autocommit = bind.engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        for start in range(0, len(rows), BATCH):
            chunk = rows[start:start + BATCH]
            autocommit.execute(
                sa.text(f"UPDATE {TABLE} SET {LEGACY_COLUMN} = :p WHERE run_id = :r"),
                [{"p": _decompress(blob), "r": run_id} for run_id, blob in chunk],
            )
    finally:
        autocommit.close()
    op.execute(f"ALTER TABLE {TABLE} DROP COLUMN {COLUMN}, ALGORITHM=INPLACE, LOCK=NONE")


def _compress(raw: str, run_id: str) -> bytes:
    """空审计（'' 与 '{}'）统一存空字节串，读接口把空值当作空审计。"""
    if not raw:
        return b""
    data = raw.encode("utf-8")
    if not json.loads(data):
        return b""
    packed = zlib.compress(data, COMPRESS_LEVEL)
    if zlib.decompress(packed) != data:
        raise RuntimeError(f"压缩往返校验失败 run_id={run_id}")
    return packed


def _decompress(blob: bytes | None) -> str:
    if not blob:
        return "{}"
    return zlib.decompress(blob).decode("utf-8")
