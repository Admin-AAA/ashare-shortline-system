"""share_floats: drop exact duplicates, add natural unique key + indexes.

Natural key (no share_type column exists in this table):
  code, IFNULL(ann_date,''), float_date, IFNULL(holder_name,''), ROUND(IFNULL(float_share,0))
float_share is MySQL FLOAT (~7 significant digits) so it is keyed via a stored generated
BIGINT column (whole shares).  ann_date is kept (point-in-time backtests).
Key text columns use utf8mb4_bin so only byte-identical rows collide (e.g. 全角/半角 brackets
in holder_name stay distinct). Only rows identical on that key are removed (lowest id kept); count is logged.
Indexes (code, float_date) and (float_date) are created only if missing.
"""
from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

revision = "0024_share_floats_unique"
down_revision = "0023_drop_realtime_quote_prov"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")


def _indexes(bind) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for name, col in bind.execute(sa.text(
        "SELECT index_name, column_name FROM information_schema.statistics "
        "WHERE table_schema = DATABASE() AND table_name = 'share_floats' ORDER BY index_name, seq_in_index"
    )):
        out.setdefault(name, []).append(col)
    return out


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return
    codes = [c for (c,) in bind.execute(sa.text("SELECT DISTINCT code FROM share_floats"))]
    dels: list[int] = []
    for i in range(0, len(codes), 300):
        seen: set = set()
        for row in bind.execute(sa.text(
            "SELECT id, code, IFNULL(ann_date,''), float_date, IFNULL(holder_name,''), "
            "ROUND(IFNULL(float_share,0)) FROM share_floats WHERE code IN :c ORDER BY id"
        ).bindparams(sa.bindparam("c", expanding=True)), {"c": codes[i:i + 300]}):
            k = tuple(row[1:])
            if k in seen:
                dels.append(row[0])
            else:
                seen.add(k)
    for i in range(0, len(dels), 5000):
        bind.execute(sa.text("DELETE FROM share_floats WHERE id IN :i").bindparams(
            sa.bindparam("i", expanding=True)), {"i": dels[i:i + 5000]})
    log.info("share_floats: removed %d exact duplicate rows", len(dels))
    print(f"share_floats: removed {len(dels)} exact duplicate rows")

    idx = _indexes(bind)
    op.execute(
        "ALTER TABLE share_floats "
        "ADD COLUMN ann_key VARCHAR(10) COLLATE utf8mb4_bin AS (IFNULL(ann_date,'')) STORED, "
        "ADD COLUMN holder_key VARCHAR(120) COLLATE utf8mb4_bin AS (IFNULL(holder_name,'')) STORED, "
        "ADD COLUMN float_share_key BIGINT AS (ROUND(IFNULL(float_share,0))) STORED, "
        "ADD UNIQUE KEY uq_share_floats_natural (code, ann_key, float_date, holder_key, float_share_key)"
        + ("" if any(v == ["code", "float_date"] for v in idx.values()) else ", ADD INDEX ix_share_floats_code_float_date (code, float_date)")
        + ("" if any(v == ["float_date"] for v in idx.values()) else ", ADD INDEX ix_share_floats_float_date (float_date)")
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return
    op.execute(
        "ALTER TABLE share_floats DROP INDEX uq_share_floats_natural, "
        "DROP COLUMN ann_key, DROP COLUMN holder_key, DROP COLUMN float_share_key"
    )
