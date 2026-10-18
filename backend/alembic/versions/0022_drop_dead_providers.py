"""drop data_source_config/health rows of deleted tushare providers

Revision ID: 0022_drop_dead_providers
Revises: 0021_trade_calendar
"""
from alembic import op

revision = "0022_drop_dead_providers"
down_revision = "0021_trade_calendar"
branch_labels = None
depends_on = None

_DEAD = ("fastapic", "xiaodefa", "ai_tool", "ai_tool_key1", "ai_tool_key2", "tushare_official")


def upgrade() -> None:
    names = ", ".join(f"'{n}'" for n in _DEAD)
    for table in ("data_source_config", "data_source_health"):
        op.execute(f"DELETE FROM {table} WHERE source_type = 'tushare_provider' AND source IN ({names})")


def downgrade() -> None:
    # Data-only cleanup of providers whose code no longer exists; nothing to restore.
    pass
