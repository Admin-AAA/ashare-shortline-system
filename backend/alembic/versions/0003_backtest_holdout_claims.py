"""Add the concurrency-safe research holdout consumption ledger."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_backtest_holdout_claims"
down_revision = "0002_remove_legacy_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backtest_holdout_claims",
        sa.Column("holdout_fingerprint", sa.CHAR(length=64), nullable=False),
        sa.Column("experiment_id", sa.String(length=80), nullable=False),
        sa.Column("phase", sa.String(length=24), nullable=False, server_default="untouched_holdout"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="consumed"),
        sa.Column("claimed_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("holdout_fingerprint"),
        sa.Index("ix_backtest_holdout_claims_experiment_id", "experiment_id"),
    )


def downgrade() -> None:
    raise RuntimeError("Research holdout ledger migrations are forward-only")
