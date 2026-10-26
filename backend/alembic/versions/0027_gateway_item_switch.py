"""data_gateway_item_switch: per-(dataset, provider) gateway switch.

The admin page's disable button used to write the shared (source, source_type) row in
data_source_config, which several datasets share (e.g. disabling Promax rt_k in quote.realtime
turned off the whole Promax Tushare provider).  This table lets one dataset column skip one
provider without touching any other dataset.  No row = enabled.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0027_gateway_item_switch"
down_revision = "0026_outcome_rule_version"
branch_labels = None
depends_on = None

_TABLE = "data_gateway_item_switch"


def upgrade() -> None:
    if _TABLE in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        _TABLE,
        sa.Column("dataset_key", sa.String(40), primary_key=True),
        sa.Column("provider", sa.String(40), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table(_TABLE)
