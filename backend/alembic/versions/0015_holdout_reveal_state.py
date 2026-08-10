"""Add explicit claim/reveal fields to the research holdout ledger."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = "0015_holdout_reveal_state"
down_revision = "0014_stock_metadata_history"
branch_labels = None
depends_on = None


def _datetime_type() -> sa.types.TypeEngine:
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("backtest_holdout_claims"):
        raise RuntimeError("backtest_holdout_claims must exist before holdout reveal migration")
    columns = {str(item["name"]): item for item in inspector.get_columns("backtest_holdout_claims")}
    if "revealed_at" not in columns:
        op.add_column(
            "backtest_holdout_claims",
            sa.Column("revealed_at", _datetime_type(), nullable=True),
        )
    if "reveal_fingerprint" not in columns:
        op.add_column(
            "backtest_holdout_claims",
            sa.Column("reveal_fingerprint", sa.CHAR(length=64), nullable=True),
        )
    # Existing rows are legacy claims.  Do not relabel them as revealed: without
    # a reveal fingerprint they cannot satisfy the two-phase evidence contract.


def downgrade() -> None:
    raise RuntimeError("Holdout reveal migrations are forward-only")
