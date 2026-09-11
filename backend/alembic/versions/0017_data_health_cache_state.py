"""Add the cross-process data-health cache generation marker."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0017_data_health_cache_state"
down_revision = "0016_daily_basic"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0001_demo_baseline builds the schema from the ORM metadata.  That means
    # a fresh demo database may already contain this table before Alembic
    # reaches 0017; existing production databases still need the DDL.
    if "data_health_cache_state" in inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "data_health_cache_state",
        sa.Column("cache_key", sa.String(length=64), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("cache_key"),
    )


def downgrade() -> None:
    raise RuntimeError("forward-only migration: data-health cache state must not be dropped")
