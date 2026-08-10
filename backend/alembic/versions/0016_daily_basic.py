"""Add daily_basic valuation table for historical replay (pe_ttm/market value)."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0016_daily_basic"
down_revision = "0015_holdout_reveal_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("daily_basic"):
        op.create_table(
            "daily_basic",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("code", sa.String(length=6), nullable=False),
            sa.Column("trade_date", sa.String(length=10), nullable=False),
            sa.Column("turnover_rate", sa.Float(), nullable=True),
            sa.Column("pe_ttm", sa.Float(), nullable=True),
            sa.Column("total_mv", sa.Float(), nullable=True),
            sa.Column("circ_mv", sa.Float(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("code", "trade_date", name="uq_daily_basic_code_date"),
        )
    indexes = {str(item.get("name")) for item in inspector.get_indexes("daily_basic")}
    if "ix_daily_basic_code" not in indexes:
        op.create_index("ix_daily_basic_code", "daily_basic", ["code"])
    if "ix_daily_basic_trade_date" not in indexes:
        op.create_index("ix_daily_basic_trade_date", "daily_basic", ["trade_date"])


def downgrade() -> None:
    raise RuntimeError("forward-only migration: daily_basic must not be dropped")
