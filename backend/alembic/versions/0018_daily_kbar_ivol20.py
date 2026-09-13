"""Add daily_kbars.ivol20 (个股20日特质波动，对中证500残差标准差；L-093 v3 否决用)."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0018_daily_kbar_ivol20"
down_revision = "0017_data_health_cache_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("daily_kbars"):
        return
    columns = {str(col["name"]) for col in inspector.get_columns("daily_kbars")}
    if "ivol20" not in columns:
        op.add_column("daily_kbars", sa.Column("ivol20", sa.Float(), nullable=True))


def downgrade() -> None:
    raise RuntimeError("forward-only migration: daily_kbars.ivol20 must not be dropped")
