"""Add immutable point-in-time stock metadata intervals for research snapshots."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0014_stock_metadata_history"
down_revision = "0013_recommendation_collection_seals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("stock_metadata_history"):
        op.create_table(
            "stock_metadata_history",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("code", sa.String(length=6), nullable=False),
            sa.Column("name", sa.String(length=80), nullable=False),
            sa.Column("industry", sa.String(length=80), nullable=True),
            sa.Column("market", sa.String(length=16), nullable=True),
            sa.Column("list_date", sa.String(length=10), nullable=True),
            sa.Column("is_st", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("effective_from", sa.String(length=10), nullable=False),
            sa.Column("effective_to", sa.String(length=10), nullable=True),
            sa.Column("source_version", sa.String(length=80), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "code",
                "effective_from",
                "source_version",
                name="uq_stock_metadata_history_interval",
            ),
        )
    indexes = {str(item.get("name")) for item in inspector.get_indexes("stock_metadata_history")}
    if "ix_stock_metadata_history_lookup" not in indexes:
        op.create_index(
            "ix_stock_metadata_history_lookup",
            "stock_metadata_history",
            ["code", "effective_from", "effective_to"],
        )
    if "ix_stock_metadata_history_code" not in indexes:
        op.create_index("ix_stock_metadata_history_code", "stock_metadata_history", ["code"])
    if "ix_stock_metadata_history_effective_from" not in indexes:
        op.create_index(
            "ix_stock_metadata_history_effective_from",
            "stock_metadata_history",
            ["effective_from"],
        )
    if "ix_stock_metadata_history_effective_to" not in indexes:
        op.create_index(
            "ix_stock_metadata_history_effective_to",
            "stock_metadata_history",
            ["effective_to"],
        )


def downgrade() -> None:
    raise RuntimeError("Point-in-time research metadata migrations are forward-only")
