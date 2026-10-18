"""drop config/health rows of the deleted stockai888 realtime_quote provider

Revision ID: 0023_drop_realtime_quote_prov
Revises: 0022_drop_dead_providers
"""
from alembic import op

revision = "0023_drop_realtime_quote_prov"
down_revision = "0022_drop_dead_providers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("data_source_config", "data_source_health"):
        op.execute(f"DELETE FROM {table} WHERE source_type = 'tushare_provider' AND source = 'realtime_quote'")


def downgrade() -> None:
    pass
