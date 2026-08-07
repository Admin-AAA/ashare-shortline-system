"""Add version-bound historical reconstruction batch manifests.

Revision 0011 is additive, forward-only, and re-entrant. It creates the batch
manifest table and a nullable unique index that prevents two runs for the same
batch/trading date. Existing reconstruction rows remain untouched and are not
implicitly attached to a manifest.
"""
from __future__ import annotations

from typing import Any

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = "0011_reconstruction_batches"
down_revision = "0010_data_source_trace"
branch_labels = None
depends_on = None

_BATCH_TABLE = "recommendation_reconstruction_batches"
_RUN_TABLE = "recommendation_runs"
_STATUS_INDEX = "ix_rec_reconstruction_batches_status_completed"
_BATCH_DATE_INDEX = "uq_rec_runs_reconstruction_batch_date"


def _datetime_type() -> sa.types.TypeEngine[Any]:
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


def _required_batch_columns() -> dict[str, tuple[type[sa.types.TypeEngine[Any]], bool]]:
    return {
        "batch_id": (sa.String, False),
        "schema_version": (sa.SmallInteger, False),
        "window_start": (sa.String, False),
        "window_end": (sa.String, False),
        "expected_trade_dates_json": (sa.Text, False),
        "expected_trade_date_count": (sa.Integer, False),
        "completed_trade_date_count": (sa.Integer, False),
        "request_template_fingerprint": (sa.CHAR, False),
        "strategy_policy_fingerprint": (sa.CHAR, False),
        "execution_policy_fingerprint": (sa.CHAR, False),
        "code_revision": (sa.String, False),
        "cohort_identity_hash": (sa.CHAR, True),
        "status": (sa.String, False),
        "failure_message": (sa.Text, True),
        "created_at": (sa.DateTime, False),
        "updated_at": (sa.DateTime, False),
        "completed_at": (sa.DateTime, True),
    }


def _assert_compatible_batch_table(bind: sa.engine.Connection) -> bool:
    inspector = sa.inspect(bind)
    if _BATCH_TABLE not in set(inspector.get_table_names()):
        return False
    columns = {str(item["name"]): item for item in inspector.get_columns(_BATCH_TABLE)}
    expected = _required_batch_columns()
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
        for item in inspector.get_pk_constraint(_BATCH_TABLE).get(
            "constrained_columns", ()
        )
    )
    if missing or incompatible or primary_key != ("batch_id",):
        raise RuntimeError(
            "reconstruction batch table conflicts with revision 0011: "
            f"missing={missing}, incompatible={incompatible}, primary_key={primary_key}"
        )
    return True


def _index_state(
    bind: sa.engine.Connection,
    *,
    table: str,
    name: str,
    columns: tuple[str, ...],
    unique: bool,
) -> bool:
    indexes = {
        str(item["name"]): item
        for item in sa.inspect(bind).get_indexes(table)
        if item.get("name")
    }
    observed = indexes.get(name)
    if observed is None:
        return False
    observed_columns = tuple(str(item) for item in (observed.get("column_names") or ()))
    if observed_columns != columns or bool(observed.get("unique", False)) != unique:
        raise RuntimeError(
            f"index {name} conflicts with revision 0011: "
            f"columns={observed_columns}, unique={bool(observed.get('unique', False))}"
        )
    return True


def _assert_no_batch_date_duplicates(bind: sa.engine.Connection) -> None:
    duplicate = bind.execute(
        sa.text(
            "SELECT reconstruction_batch_id, trade_date, COUNT(*) AS row_count "
            "FROM recommendation_runs "
            "WHERE reconstruction_batch_id IS NOT NULL "
            "GROUP BY reconstruction_batch_id, trade_date "
            "HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "cannot add reconstruction batch/date uniqueness; duplicate rows exist: "
            f"batch_id={duplicate[0]}, trade_date={duplicate[1]}, count={duplicate[2]}"
        )


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if _RUN_TABLE not in tables:
        raise RuntimeError("revision 0011 requires recommendation_runs")

    if not _assert_compatible_batch_table(bind):
        op.create_table(
            _BATCH_TABLE,
            sa.Column("batch_id", sa.String(length=80), nullable=False),
            sa.Column("schema_version", sa.SmallInteger(), nullable=False),
            sa.Column("window_start", sa.String(length=10), nullable=False),
            sa.Column("window_end", sa.String(length=10), nullable=False),
            sa.Column("expected_trade_dates_json", sa.Text(), nullable=False),
            sa.Column("expected_trade_date_count", sa.Integer(), nullable=False),
            sa.Column("completed_trade_date_count", sa.Integer(), nullable=False),
            sa.Column("request_template_fingerprint", sa.CHAR(length=64), nullable=False),
            sa.Column("strategy_policy_fingerprint", sa.CHAR(length=64), nullable=False),
            sa.Column("execution_policy_fingerprint", sa.CHAR(length=64), nullable=False),
            sa.Column("code_revision", sa.String(length=64), nullable=False),
            sa.Column("cohort_identity_hash", sa.CHAR(length=64), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("failure_message", sa.Text(), nullable=True),
            sa.Column("created_at", _datetime_type(), nullable=False),
            sa.Column("updated_at", _datetime_type(), nullable=False),
            sa.Column("completed_at", _datetime_type(), nullable=True),
            sa.PrimaryKeyConstraint("batch_id"),
        )
    if not _assert_compatible_batch_table(bind):
        raise RuntimeError("revision 0011 failed to create reconstruction batch table")

    if not _index_state(
        bind,
        table=_BATCH_TABLE,
        name=_STATUS_INDEX,
        columns=("status", "completed_at"),
        unique=False,
    ):
        op.create_index(
            _STATUS_INDEX,
            _BATCH_TABLE,
            ["status", "completed_at"],
            unique=False,
        )

    if not _index_state(
        bind,
        table=_RUN_TABLE,
        name=_BATCH_DATE_INDEX,
        columns=("reconstruction_batch_id", "trade_date"),
        unique=True,
    ):
        _assert_no_batch_date_duplicates(bind)
        op.create_index(
            _BATCH_DATE_INDEX,
            _RUN_TABLE,
            ["reconstruction_batch_id", "trade_date"],
            unique=True,
        )

    if not _index_state(
        bind,
        table=_BATCH_TABLE,
        name=_STATUS_INDEX,
        columns=("status", "completed_at"),
        unique=False,
    ) or not _index_state(
        bind,
        table=_RUN_TABLE,
        name=_BATCH_DATE_INDEX,
        columns=("reconstruction_batch_id", "trade_date"),
        unique=True,
    ):
        raise RuntimeError("revision 0011 final index verification failed")


def downgrade() -> None:
    raise RuntimeError(
        "Reconstruction batch migrations are forward-only; unpublish the batch instead"
    )
