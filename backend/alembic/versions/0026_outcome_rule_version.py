"""recommendation_outcomes: rule_version + preserved original settlement + bars_verified_at.

Owner ruling 2026-10-19: re-project all historical outcomes under the current rules without silent
overwrite. original_* columns keep the first pre-reprojection values (filled once, never overwritten);
rule_version records which rule set produced the current projection; bars_verified_at records when the
outcome was last checked against daily_kbars revisions (auto re-projection on backfill rewrites).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026_outcome_rule_version"
down_revision = "0025_prune_dead_presets"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("rule_version", sa.String(80)),
    ("bars_verified_at", sa.DateTime()),
    ("original_outcome_label", sa.String(30)),
    ("original_exit_date", sa.String(10)),
    ("original_exit_price", sa.Float()),
    ("original_exit_reason", sa.String(40)),
    ("original_executed_return", sa.Float()),
    ("original_rule_version", sa.String(80)),
)


def upgrade() -> None:
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("recommendation_outcomes")}
    with op.batch_alter_table("recommendation_outcomes") as batch:
        for name, type_ in _COLUMNS:
            if name not in existing:
                batch.add_column(sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("recommendation_outcomes") as batch:
        for name, _ in reversed(_COLUMNS):
            batch.drop_column(name)
