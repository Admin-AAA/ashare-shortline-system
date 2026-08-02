"""Add the concurrency-safe research holdout consumption ledger."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_backtest_holdout_claims"
down_revision = "0002_remove_legacy_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("backtest_holdout_claims"):
        op.create_table(
            "backtest_holdout_claims",
            sa.Column("holdout_fingerprint", sa.CHAR(length=64), nullable=False),
            sa.Column("experiment_id", sa.String(length=80), nullable=False),
            sa.Column("phase", sa.String(length=24), nullable=False, server_default="untouched_holdout"),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="consumed"),
            sa.Column("claimed_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("holdout_fingerprint"),
        )
    if not any(
        index.get("name") == "ix_backtest_holdout_claims_experiment_id"
        for index in inspector.get_indexes("backtest_holdout_claims")
    ):
        op.create_index(
            "ix_backtest_holdout_claims_experiment_id",
            "backtest_holdout_claims",
            ["experiment_id"],
        )


def downgrade() -> None:
    raise RuntimeError("Research holdout ledger migrations are forward-only")
