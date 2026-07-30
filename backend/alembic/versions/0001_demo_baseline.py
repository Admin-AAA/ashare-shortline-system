"""Create the complete demo schema from canonical SQLAlchemy metadata."""
from __future__ import annotations

from alembic import op

from app.db.session import Base
from app.models import tables  # noqa: F401 - register all ORM metadata


revision = "0001_demo_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    raise RuntimeError("Demo schema migrations are forward-only")
