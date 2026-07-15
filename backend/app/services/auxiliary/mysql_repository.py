"""MySQL-dialect atomic-upsert repository abstraction.

This is intentionally an adapter building block only: it owns neither a database URL
nor a production table/model declaration, performs no schema migration or duplicate-key
precheck, and never commits a transaction.  A future dataset adapter supplies a table,
unique natural key proved by an S4 migration, row encoder, and transaction-bound
executor.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Generic, Protocol, TypeVar, runtime_checkable

from sqlalchemy import PrimaryKeyConstraint, UniqueConstraint, case
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.sql.dml import Insert
from sqlalchemy.sql.schema import Table

from app.services.auxiliary.contracts import (
    AuxiliaryContractError,
    AuxiliaryDatasetRequest,
    AuxiliaryPersistResult,
)


RecordT = TypeVar("RecordT")


class MySQLUpsertConfigurationError(AuxiliaryContractError):
    """Raised during adapter construction before any persistence can be attempted."""


@runtime_checkable
class MySQLStatementExecutor(Protocol):
    """A transaction-bound SQLAlchemy Session/Connection compatible executor."""

    def execute(self, statement: Any) -> Any:
        """Execute one statement without committing the caller-owned transaction."""


RowEncoder = Callable[[RecordT], Mapping[str, object]]


@dataclass(frozen=True, slots=True)
class MySQLUpsertPolicy:
    """Per-dataset field policy; rowcount never implies exact insert/update counts."""

    overwrite_fields: tuple[str, ...]
    preserve_fields: tuple[str, ...] = ()
    latest_wins_field: str | None = None

    def __post_init__(self) -> None:
        if not self.overwrite_fields:
            raise MySQLUpsertConfigurationError("upsert policy 必须声明至少一个 overwrite_fields")
        if any(not name.strip() for name in (*self.overwrite_fields, *self.preserve_fields)):
            raise MySQLUpsertConfigurationError("upsert 字段名不能为空")
        if len(self.overwrite_fields) != len(set(self.overwrite_fields)):
            raise MySQLUpsertConfigurationError("overwrite_fields 不能重复")
        if len(self.preserve_fields) != len(set(self.preserve_fields)):
            raise MySQLUpsertConfigurationError("preserve_fields 不能重复")
        if set(self.overwrite_fields) & set(self.preserve_fields):
            raise MySQLUpsertConfigurationError("同一字段不能同时 overwrite 和 preserve")
        if self.latest_wins_field is not None and not self.latest_wins_field.strip():
            raise MySQLUpsertConfigurationError("latest_wins_field 不能是空白字符串")


class MySQLAtomicUpsertRepository(Generic[RecordT]):
    """Build and execute batched ``INSERT … ON DUPLICATE KEY UPDATE`` statements.

    ``executor`` must already be in the transaction selected by the application.  The
    class deliberately does not call ``commit`` or inspect production schema/data.
    Migration/preflight code must establish the matching UNIQUE natural key first.
    """

    def __init__(
        self,
        *,
        executor: MySQLStatementExecutor,
        table: Table,
        natural_key: tuple[str, ...],
        row_encoder: RowEncoder[RecordT],
        policy: MySQLUpsertPolicy,
        batch_size: int = 500,
    ) -> None:
        if batch_size <= 0:
            raise MySQLUpsertConfigurationError("batch_size 必须大于零")
        if not natural_key or len(natural_key) != len(set(natural_key)):
            raise MySQLUpsertConfigurationError("natural_key 必须是唯一的非空字段元组")
        if any(not key.strip() for key in natural_key):
            raise MySQLUpsertConfigurationError("natural_key 不能包含空白字段")
        self._executor = executor
        self._table = table
        self._natural_key = natural_key
        self._row_encoder = row_encoder
        self._policy = policy
        self._batch_size = batch_size
        self._validate_table_contract()

    @property
    def batch_size(self) -> int:
        return self._batch_size

    @property
    def natural_key(self) -> tuple[str, ...]:
        return self._natural_key

    def build_statement(self, records: Sequence[RecordT]) -> Insert:
        """Build, but do not execute, a single MySQL atomic-upsert statement."""
        if not records:
            raise MySQLUpsertConfigurationError("空 batch 不应构建 upsert statement")
        rows = [dict(self._row_encoder(record)) for record in records]
        for row in rows:
            missing = tuple(key for key in self._natural_key if key not in row or row[key] is None)
            if missing:
                raise MySQLUpsertConfigurationError(
                    f"upsert row 缺少 natural key 字段：{', '.join(missing)}"
                )
        statement = mysql_insert(self._table).values(rows)
        update_values: dict[str, Any] = {}
        if self._policy.latest_wins_field is None:
            update_values = {
                field: getattr(statement.inserted, field)
                for field in self._policy.overwrite_fields
            }
        else:
            freshness_column = getattr(self._table.c, self._policy.latest_wins_field)
            incoming_freshness = getattr(statement.inserted, self._policy.latest_wins_field)
            for field in self._policy.overwrite_fields:
                incoming = getattr(statement.inserted, field)
                current = getattr(self._table.c, field)
                update_values[field] = case(
                    (incoming_freshness >= freshness_column, incoming),
                    else_=current,
                )
        return statement.on_duplicate_key_update(**update_values)

    def upsert(
        self,
        records: Sequence[RecordT],
        request: AuxiliaryDatasetRequest,
    ) -> AuxiliaryPersistResult:
        """Execute at most ``ceil(N / batch_size)`` DML statements without commit.

        MySQL's affected-row count is surfaced for diagnostics only.  It must not be
        interpreted as exact inserted/updated counts because duplicate updates can
        report different values by connector/configuration.
        """
        del request  # Included to satisfy the shared AuxiliaryRepository persistence contract.
        started = perf_counter()
        attempted = len(records)
        affected = 0
        affected_known = True
        execute_count = 0
        try:
            for offset in range(0, attempted, self._batch_size):
                statement = self.build_statement(records[offset : offset + self._batch_size])
                result = self._executor.execute(statement)
                execute_count += 1
                rowcount = getattr(result, "rowcount", None)
                if isinstance(rowcount, int) and rowcount >= 0:
                    affected += rowcount
                else:
                    affected_known = False
        except Exception:
            # The caller owns transaction rollback; no fabricated partial-row metric.
            raise
        return AuxiliaryPersistResult(
            attempted_rows=attempted,
            affected_rows=affected if affected_known else None,
            failed_rows=0,
            execute_count=execute_count,
            duration_ms=(perf_counter() - started) * 1000,
        )

    def _validate_table_contract(self) -> None:
        columns = set(self._table.c.keys())
        referenced = set(self._natural_key) | set(self._policy.overwrite_fields) | set(self._policy.preserve_fields)
        if self._policy.latest_wins_field is not None:
            referenced.add(self._policy.latest_wins_field)
        missing = sorted(referenced - columns)
        if missing:
            raise MySQLUpsertConfigurationError(
                f"table '{self._table.name}' 缺少 upsert policy 字段：{', '.join(missing)}"
            )
        illegal_update = set(self._natural_key) & set(self._policy.overwrite_fields)
        if illegal_update:
            raise MySQLUpsertConfigurationError(
                f"natural key 字段不得出现在 overwrite_fields：{', '.join(sorted(illegal_update))}"
            )
        if not self._has_matching_unique_key():
            raise MySQLUpsertConfigurationError(
                f"table '{self._table.name}' 未声明与 natural_key {self._natural_key!r} 匹配的 UNIQUE/PRIMARY KEY；"
                "必须先通过独立 migration 建立并验证该约束"
            )

    def _has_matching_unique_key(self) -> bool:
        expected = tuple(self._natural_key)
        for constraint in self._table.constraints:
            columns = tuple(column.name for column in constraint.columns)
            if columns == expected and isinstance(constraint, (UniqueConstraint, PrimaryKeyConstraint)):
                return True
        for index in self._table.indexes:
            columns = tuple(column.name for column in index.columns)
            if columns == expected and index.unique:
                return True
        return False


__all__ = [
    "MySQLAtomicUpsertRepository",
    "MySQLStatementExecutor",
    "MySQLUpsertConfigurationError",
    "MySQLUpsertPolicy",
    "RowEncoder",
]
