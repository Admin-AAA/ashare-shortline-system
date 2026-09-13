"""Add daily_kbars.ivol_dn20 (个股20日特质下行半波动，对中证500残差下行半标准差；L-099 v4 否决用)."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0019_daily_kbar_ivol_dn20"
down_revision = "0018_daily_kbar_ivol20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("daily_kbars"):
        return
    columns = {str(col["name"]) for col in inspector.get_columns("daily_kbars")}
    if "ivol_dn20" not in columns:
        op.add_column("daily_kbars", sa.Column("ivol_dn20", sa.Float(), nullable=True))


def downgrade() -> None:
    raise RuntimeError("forward-only migration: daily_kbars.ivol_dn20 must not be dropped")
