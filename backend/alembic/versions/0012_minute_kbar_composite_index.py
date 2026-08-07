"""Restore the canonical minute-kbar trade-date/code index contract.

Revision 0012 is additive, forward-only, and re-entrant. The index already
exists in established demo databases from the retired schema inventory, so a
compatible index is a no-op. A missing index is created; an incompatible
same-named index blocks rather than being replaced on the large minute table.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0012_minute_kbar_composite_index"
down_revision = "0011_reconstruction_batches"
branch_labels = None
depends_on = None

_TABLE = "minute_kbars"
_INDEX = "idx_mk_td_code"
_COLUMNS = ("trade_date", "code")


def _index_state(bind: sa.engine.Connection) -> bool:
    inspector = sa.inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        raise RuntimeError("revision 0012 requires minute_kbars")
    indexes = {
        str(item["name"]): item
        for item in inspector.get_indexes(_TABLE)
        if item.get("name")
    }
    observed = indexes.get(_INDEX)
    if observed is None:
        return False
    columns = tuple(str(item) for item in (observed.get("column_names") or ()))
    unique = bool(observed.get("unique", False))
    if columns != _COLUMNS or unique:
        raise RuntimeError(
            f"index {_INDEX} conflicts with revision 0012: "
            f"columns={columns}, unique={unique}"
        )
    return True


def upgrade() -> None:
    bind = op.get_bind()
    if not _index_state(bind):
        op.create_index(_INDEX, _TABLE, list(_COLUMNS), unique=False)
    if not _index_state(bind):
        raise RuntimeError("revision 0012 failed to establish minute-kbar index")


def downgrade() -> None:
    raise RuntimeError(
        "Minute-kbar index migrations are forward-only; keep the additive index"
    )
