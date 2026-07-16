"""MySQL-dialect atomic-upsert repository abstraction.

This module deliberately owns no database URL, session lifecycle, migration, DDL, or
business-table inspection.  SQLAlchemy ``Table`` constraints are only an adapter
construction check: they are **not** evidence that a deployed MySQL schema has the
same UNIQUE constraint.  A production composition root must separately require the
versioned migration, stored-data duplicate preflight, and two-connection verification
recorded by the frozen auxiliary inventory before it enables this writer.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from math import ceil
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
    AuxiliaryRepositorySnapshot,
)


RecordT = TypeVar("RecordT")


class MySQLUpsertConfigurationError(AuxiliaryContractError):
    """Raised before DML when an adapter declaration is unsafe or incomplete."""


class MySQLDuplicateNaturalKeyError(MySQLUpsertConfigurationError):
    """Raised when one submitted batch contains duplicate logical records."""

    def __init__(self, natural_key: tuple[str, ...], duplicate_keys: tuple[tuple[object, ...], ...]) -> None:
        self.natural_key = natural_key
        self.duplicate_keys = duplicate_keys
        super().__init__(
            "upsert batch 包含重复 natural key "
            f"{natural_key!r}：{duplicate_keys!r}；未执行任何 DML"
        )


@dataclass(frozen=True, slots=True)
class MySQLDuplicateKeyPreflight:
    """Pure in-memory duplicate-key evidence for one proposed upsert call.

    This validates the input batch only.  It intentionally does not claim to inspect
    pre-existing rows in a real MySQL table; that production-only check remains a
    versioned migration blocker.
    """

    attempted_rows: int
    unique_key_count: int
    duplicate_keys: tuple[tuple[object, ...], ...] = ()

    @property
    def is_clear(self) -> bool:
        return not self.duplicate_keys

    def require_clear(self, natural_key: tuple[str, ...]) -> None:
        if not self.is_clear:
            raise MySQLDuplicateNaturalKeyError(natural_key, self.duplicate_keys)


@runtime_checkable
class MySQLStatementExecutor(Protocol):
    """A transaction-bound SQLAlchemy Session/Connection compatible executor."""

    def execute(self, statement: Any) -> Any:
        """Execute one statement without committing the caller-owned transaction."""


RowEncoder = Callable[[RecordT], Mapping[str, object]]
SnapshotInspector = Callable[[AuxiliaryDatasetRequest], AuxiliaryRepositorySnapshot]


@dataclass(frozen=True, slots=True)
class MySQLUpsertPolicy:
    """Per-dataset update whitelist; rowcount never implies insert/update counts.

    ``overwrite_fields`` is the complete and explicit ``ON DUPLICATE KEY UPDATE``
    whitelist.  Fields absent from it, including ``preserve_fields``, are never
    updated by this repository.  A latest-wins comparison field must itself be
    overwritten; otherwise a successful newer update would leave its watermark stale.
    """

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
        if self.latest_wins_field is not None and self.latest_wins_field not in self.overwrite_fields:
            raise MySQLUpsertConfigurationError(
                "latest_wins_field 必须包含在 overwrite_fields，确保成功更新会推进 watermark"
            )

    @property
    def update_whitelist(self) -> tuple[str, ...]:
        """The only columns emitted in ``ON DUPLICATE KEY UPDATE``."""
        return self.overwrite_fields


class MySQLAuxiliaryRepository(Generic[RecordT]):
    """Adapt an inspect callback plus atomic writer to the shared repository protocol.

    The callback is supplied by the production composition root.  It can be backed by
    a future verified SQLAlchemy adapter or by an in-memory fake; this skeleton never
    opens a connection or guesses coverage itself.
    """

    def __init__(
        self,
        *,
        inspector: SnapshotInspector,
        writer: "MySQLAtomicUpsertRepository[RecordT]",
    ) -> None:
        self._inspector = inspector
        self._writer = writer

    def inspect(self, request: AuxiliaryDatasetRequest) -> AuxiliaryRepositorySnapshot:
        return self._inspector(request)

    def upsert(
        self,
        records: Sequence[RecordT],
        request: AuxiliaryDatasetRequest,
    ) -> AuxiliaryPersistResult:
        return self._writer.upsert(records, request)


class MySQLAtomicUpsertRepository(Generic[RecordT]):
    """Build and execute batched ``INSERT … ON DUPLICATE KEY UPDATE`` statements.

    ``executor`` must already be in the transaction selected by the application.  The
    class never calls ``commit`` or a schema/data inspection query.  Before *any* DML,
    it validates every encoded row, rejects duplicate natural keys across the complete
    input (including batch boundaries), and fixes the number of statements at exactly
    ``ceil(N / batch_size)`` with zero fixed extra statements.
    """

    fixed_statement_count = 0

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

    @property
    def update_whitelist(self) -> tuple[str, ...]:
        return self._policy.update_whitelist

    def dml_statement_upper_bound(self, record_count: int) -> int:
        """Return the exact DML count; duplicate preflight adds no SQL statement."""
        if record_count < 0:
            raise MySQLUpsertConfigurationError("record_count 不能为负数")
        return ceil(record_count / self._batch_size)

    def preflight_duplicate_keys(self, records: Sequence[RecordT]) -> MySQLDuplicateKeyPreflight:
        """Validate encoded input and return pure duplicate-key evidence without DML."""
        rows = self._encode_and_validate(records)
        return self._preflight_rows(rows)

    def build_statement(self, records: Sequence[RecordT]) -> Insert:
        """Build, but do not execute, one validated MySQL atomic-upsert statement."""
        rows = self._encode_and_validate(records)
        self._preflight_rows(rows).require_clear(self._natural_key)
        return self._build_statement_from_rows(rows)

    def upsert(
        self,
        records: Sequence[RecordT],
        request: AuxiliaryDatasetRequest,
    ) -> AuxiliaryPersistResult:
        """Execute only validated batches and never infer insert/update counts.

        The full collection is encoded and preflighted before the first executor call,
        so a duplicate in a later batch cannot leave an earlier batch partially written.
        """
        del request  # Required by the shared AuxiliaryRepository persistence protocol.
        started = perf_counter()
        rows = self._encode_and_validate(records)
        self._preflight_rows(rows).require_clear(self._natural_key)
        attempted = len(rows)
        affected = 0
        affected_known = True
        execute_count = 0
        for offset in range(0, attempted, self._batch_size):
            statement = self._build_statement_from_rows(rows[offset : offset + self._batch_size])
            result = self._executor.execute(statement)
            execute_count += 1
            rowcount = getattr(result, "rowcount", None)
            if isinstance(rowcount, int) and rowcount >= 0:
                affected += rowcount
            else:
                affected_known = False
        return AuxiliaryPersistResult(
            attempted_rows=attempted,
            affected_rows=affected if affected_known else None,
            failed_rows=0,
            execute_count=execute_count,
            duration_ms=(perf_counter() - started) * 1000,
            batch_size=self._batch_size,
            batch_statement_bound=self.dml_statement_upper_bound(attempted),
            fixed_statement_count=self.fixed_statement_count,
        )

    def _encode_and_validate(self, records: Sequence[RecordT]) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        columns = set(self._table.c.keys())
        required_payload = set(self._policy.overwrite_fields)
        for index, record in enumerate(records):
            encoded = self._row_encoder(record)
            if not isinstance(encoded, Mapping):
                raise MySQLUpsertConfigurationError(f"row_encoder 第 {index} 行必须返回 Mapping")
            row = dict(encoded)
            unknown = sorted(set(row) - columns)
            if unknown:
                raise MySQLUpsertConfigurationError(
                    f"upsert row 包含 table '{self._table.name}' 不存在的字段：{', '.join(unknown)}"
                )
            missing_key = tuple(
                key for key in self._natural_key if key not in row or self._is_missing_key_value(row[key])
            )
            if missing_key:
                raise MySQLUpsertConfigurationError(
                    f"upsert row 缺少 natural key 字段：{', '.join(missing_key)}"
                )
            missing_payload = tuple(field for field in required_payload if field not in row)
            if missing_payload:
                raise MySQLUpsertConfigurationError(
                    "upsert row 缺少 update whitelist 字段：" + ", ".join(sorted(missing_payload))
                )
            rows.append(row)
        return rows

    @staticmethod
    def _is_missing_key_value(value: object) -> bool:
        return value is None or (isinstance(value, str) and not value.strip())

    def _preflight_rows(self, rows: Sequence[Mapping[str, object]]) -> MySQLDuplicateKeyPreflight:
        seen: set[tuple[object, ...]] = set()
        duplicates: list[tuple[object, ...]] = []
        duplicate_set: set[tuple[object, ...]] = set()
        for row in rows:
            key = tuple(row[field] for field in self._natural_key)
            try:
                already_seen = key in seen
            except TypeError as exc:
                raise MySQLUpsertConfigurationError("natural key 值必须可哈希以执行重复键预检") from exc
            if already_seen:
                if key not in duplicate_set:
                    duplicates.append(key)
                    duplicate_set.add(key)
            else:
                seen.add(key)
        return MySQLDuplicateKeyPreflight(
            attempted_rows=len(rows),
            unique_key_count=len(seen),
            duplicate_keys=tuple(duplicates),
        )

    def _build_statement_from_rows(self, rows: Sequence[Mapping[str, object]]) -> Insert:
        if not rows:
            raise MySQLUpsertConfigurationError("空 batch 不应构建 upsert statement")
        statement = mysql_insert(self._table).values([dict(row) for row in rows])
        if self._policy.latest_wins_field is None:
            update_values = {
                field: getattr(statement.inserted, field)
                for field in self._policy.update_whitelist
            }
        else:
            freshness_field = self._policy.latest_wins_field
            freshness_column = getattr(self._table.c, freshness_field)
            incoming_freshness = getattr(statement.inserted, freshness_field)
            update_values = {}
            for field in self._policy.update_whitelist:
                incoming = getattr(statement.inserted, field)
                current = getattr(self._table.c, field)
                update_values[field] = case(
                    (incoming_freshness >= freshness_column, incoming),
                    else_=current,
                )
        return statement.on_duplicate_key_update(**update_values)

    def _validate_table_contract(self) -> None:
        columns = set(self._table.c.keys())
        referenced = (
            set(self._natural_key)
            | set(self._policy.overwrite_fields)
            | set(self._policy.preserve_fields)
        )
        if self._policy.latest_wins_field is not None:
            referenced.add(self._policy.latest_wins_field)
        missing = sorted(referenced - columns)
        if missing:
            raise MySQLUpsertConfigurationError(
                f"table '{self._table.name}' 缺少 upsert policy 字段：{', '.join(missing)}"
            )
        illegal_update = set(self._natural_key) & set(self._policy.update_whitelist)
        if illegal_update:
            raise MySQLUpsertConfigurationError(
                f"natural key 字段不得出现在 overwrite_fields：{', '.join(sorted(illegal_update))}"
            )
        if not self._has_matching_metadata_unique_key():
            raise MySQLUpsertConfigurationError(
                f"table '{self._table.name}' 的 SQLAlchemy metadata 未声明与 natural_key "
                f"{self._natural_key!r} 匹配的 UNIQUE/PRIMARY KEY；"
                "仍须通过独立 MySQL migration 核验已部署约束"
            )

    def _has_matching_metadata_unique_key(self) -> bool:
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
    "MySQLAuxiliaryRepository",
    "MySQLDuplicateKeyPreflight",
    "MySQLDuplicateNaturalKeyError",
    "MySQLStatementExecutor",
    "MySQLUpsertConfigurationError",
    "MySQLUpsertPolicy",
    "RowEncoder",
    "SnapshotInspector",
]
