"""Add trade_calendar: local SSE trading calendar so trade-date readers never guess."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0021_trade_calendar"
down_revision = "0020_holder_number_ann_date"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("trade_calendar"):
        return
    op.create_table(
        "trade_calendar",
        sa.Column("exchange", sa.String(8), primary_key=True),
        sa.Column("cal_date", sa.String(8), primary_key=True),
        sa.Column("is_open", sa.Boolean(), nullable=False),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("trade_calendar")
