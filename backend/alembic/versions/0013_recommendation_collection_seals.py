"""Add immutable recommendation-collection seals.

Revision 0013 is additive, forward-only, and re-entrant. Existing runs are not
backfilled: a persistence checkpoint without a seal must remain non-recoverable.
"""
from __future__ import annotations

from typing import Any

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = "0013_recommendation_collection_seals"
down_revision = "0012_minute_kbar_composite_index"
branch_labels = None
depends_on = None

_TABLE = "recommendation_collection_seals"
_INDEX = "uq_rec_collection_seals_side_effect_key"
_VERSION_TABLE = "alembic_version"
_VERSION_COLUMN = "version_num"


def _widen_version_num(bind: sa.engine.Connection) -> None:
    """Allow Alembic revision ids longer than the default ``varchar(32)``.

    The Alembic default stores the current revision in a 32-character column;
    this revision's own id is longer, so MySQL must widen the column before
    Alembic writes the new revision id. SQLite does not enforce string lengths
    and does not support ``ALTER COLUMN ... TYPE``, so it is skipped. Additive
    and re-entrant.
    """
    if bind.dialect.name == "sqlite":
        return
    inspector = sa.inspect(bind)
    if _VERSION_TABLE not in set(inspector.get_table_names()):
        return
    columns = {
        str(item["name"]): item
        for item in inspector.get_columns(_VERSION_TABLE)
    }
    if _VERSION_COLUMN not in columns:
        return
    observed = columns[_VERSION_COLUMN]
    if isinstance(observed["type"], sa.String) and observed["type"].length >= 64:
        return
    op.alter_column(
        _VERSION_TABLE,
        _VERSION_COLUMN,
        existing_type=sa.String(length=32),
        type_=sa.String(length=64),
        existing_nullable=False,
    )


def _datetime_type() -> sa.types.TypeEngine[Any]:
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


def _required_columns() -> dict[str, tuple[type[sa.types.TypeEngine[Any]], bool]]:
    return {
        "run_id": (sa.String, False),
        "schema_version": (sa.SmallInteger, False),
        "recommendation_row_count": (sa.BigInteger, False),
        "collection_digest": (sa.CHAR, False),
        "checkpoint_side_effect_key": (sa.String, False),
        "sealed_at": (sa.DateTime, False),
    }


def _assert_compatible_table(bind: sa.engine.Connection) -> bool:
    inspector = sa.inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        return False
    columns = {str(item["name"]): item for item in inspector.get_columns(_TABLE)}
    expected = _required_columns()
    missing = sorted(set(expected) - set(columns))
    incompatible = sorted(
        name
        for name, (type_class, nullable) in expected.items()
        if name in columns
        and (
            not isinstance(columns[name]["type"], type_class)
            or bool(columns[name].get("nullable", True)) != nullable
        )
    )
    primary_key = tuple(
        str(item)
        for item in inspector.get_pk_constraint(_TABLE).get("constrained_columns", ())
    )
    if missing or incompatible or primary_key != ("run_id",):
        raise RuntimeError(
            "recommendation collection seal table conflicts with revision 0013: "
            f"missing={missing}, incompatible={incompatible}, primary_key={primary_key}"
        )
    return True


def _index_exists(bind: sa.engine.Connection) -> bool:
    indexes = {
        str(item["name"]): item
        for item in sa.inspect(bind).get_indexes(_TABLE)
        if item.get("name")
    }
    observed = indexes.get(_INDEX)
    if observed is None:
        return False
    columns = tuple(str(item) for item in (observed.get("column_names") or ()))
    unique = bool(observed.get("unique", False))
    if columns != ("checkpoint_side_effect_key",) or not unique:
        raise RuntimeError(
            f"index {_INDEX} conflicts with revision 0013: "
            f"columns={columns}, unique={unique}"
        )
    return True


def upgrade() -> None:
    bind = op.get_bind()
    _widen_version_num(bind)
    if not _assert_compatible_table(bind):
        op.create_table(
            _TABLE,
            sa.Column("run_id", sa.String(length=60), nullable=False),
            sa.Column("schema_version", sa.SmallInteger(), nullable=False),
            sa.Column("recommendation_row_count", sa.BigInteger(), nullable=False),
            sa.Column("collection_digest", sa.CHAR(length=64), nullable=False),
            sa.Column("checkpoint_side_effect_key", sa.String(length=160), nullable=False),
            sa.Column("sealed_at", _datetime_type(), nullable=False),
            sa.PrimaryKeyConstraint("run_id"),
        )
    if not _assert_compatible_table(bind):
        raise RuntimeError("revision 0013 failed to establish collection seal table")
    if not _index_exists(bind):
        op.create_index(
            _INDEX,
            _TABLE,
            ["checkpoint_side_effect_key"],
            unique=True,
        )
    if not _index_exists(bind):
        raise RuntimeError("revision 0013 failed to establish collection seal index")


def downgrade() -> None:
    raise RuntimeError(
        "Recommendation collection seal migrations are forward-only; keep durable seals"
    )
