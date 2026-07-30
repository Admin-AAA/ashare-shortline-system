"""Remove the migration ledger superseded by Alembic."""
from __future__ import annotations

from alembic import op
from sqlalchemy import inspect


revision = "0002_remove_legacy_ledger"
down_revision = "0001_demo_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    if "schema_migrations" in inspect(connection).get_table_names():
        op.drop_table("schema_migrations")


def downgrade() -> None:
    raise RuntimeError("Demo schema migrations are forward-only")
