"""Add holder_numbers.ann_date：股东人数按公告日判定可见性，回放不再看到未公告的期末数据（L-160）."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0020_holder_number_ann_date"
down_revision = "0019_daily_kbar_ivol_dn20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("holder_numbers"):
        return
    columns = {str(col["name"]) for col in inspector.get_columns("holder_numbers")}
    if "ann_date" not in columns:
        op.add_column("holder_numbers", sa.Column("ann_date", sa.String(10), nullable=True))
    indexes = {str(index["name"]) for index in inspector.get_indexes("holder_numbers")}
    if "ix_holder_numbers_ann_date" not in indexes:
        op.create_index("ix_holder_numbers_ann_date", "holder_numbers", ["ann_date"])


def downgrade() -> None:
    raise RuntimeError("forward-only migration: holder_numbers.ann_date must not be dropped")
