"""Forward-only migration contract with a deliberately restricted execution context."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import re
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_FORBIDDEN_SQL = re.compile(r"\b(?:DROP|TRUNCATE)\b|;", re.IGNORECASE)


class MigrationError(RuntimeError):
    """Base error for a migration failure that must prevent startup success."""

    code = "migration_error"

    def __init__(self, message: str, *, version: str | None = None) -> None:
        super().__init__(message)
        self.version = version


class MigrationDefinitionError(MigrationError):
    code = "migration_definition_error"


class MigrationChecksumMismatchError(MigrationError):
    code = "migration_checksum_mismatch"


class MigrationLockError(MigrationError):
    code = "migration_lock_unavailable"


class MigrationConditionError(MigrationError):
    code = "migration_condition_failed"


class MigrationExecutionTimeoutError(MigrationError):
    code = "migration_execution_timeout"


class MigrationSafetyError(MigrationError):
    code = "migration_operation_rejected"


class MigrationBaselineError(MigrationError):
    code = "migration_baseline_missing"


class OperationState(str, Enum):
    """The observable state required by a migration operation."""

    PENDING = "pending"
    SATISFIED = "satisfied"


Condition = Callable[["MigrationContext"], OperationState]
OperationApply = Callable[["MigrationContext"], None]
MigrationVerify = Callable[["MigrationContext"], None]


@dataclass(frozen=True)
class ColumnSignature:
    """Normalized column shape used to reject same-name schema drift."""

    name: str
    data_type: str
    nullable: bool
    default: str | None
    type_expression: str


@dataclass(frozen=True)
class IndexSignature:
    """Ordered index/UNIQUE shape used to reject same-name schema drift."""

    name: str
    columns: tuple[str, ...]
    unique: bool


@dataclass(frozen=True)
class ForeignKeySignature:
    """Observable foreign-key shape used by create-table postconditions."""

    columns: tuple[str, ...]
    referred_table: str
    referred_columns: tuple[str, ...]
    ondelete: str | None


@dataclass(frozen=True)
class MigrationOperation:
    """One independently re-runnable schema or bounded-data change."""

    name: str
    checksum_material: str
    precondition: Condition
    apply: OperationApply
    postcondition: Condition

    def __post_init__(self) -> None:
        if not self.name or not self.checksum_material:
            raise MigrationDefinitionError("migration operation requires name and checksum material")


@dataclass(frozen=True)
class Migration:
    """An immutable, forward-only migration definition."""

    version: str
    description: str
    checksum: str
    operations: tuple[MigrationOperation, ...]
    verify: MigrationVerify

    def __post_init__(self) -> None:
        if not self.version or not self.description:
            raise MigrationDefinitionError("migration requires a version and description", version=self.version or None)
        if not re.fullmatch(r"V\d{3}(?:_[a-z0-9_]+)?", self.version):
            raise MigrationDefinitionError(f"invalid migration version: {self.version}", version=self.version)
        if not re.fullmatch(r"[0-9a-f]{64}", self.checksum):
            raise MigrationDefinitionError(f"invalid migration checksum: {self.version}", version=self.version)
        names = [operation.name for operation in self.operations]
        if len(names) != len(set(names)):
            raise MigrationDefinitionError(f"duplicate operation in migration: {self.version}", version=self.version)


def stable_checksum(
    *,
    version: str,
    description: str,
    operations: Sequence[MigrationOperation],
) -> str:
    """Hash the immutable migration manifest using canonical JSON serialization."""
    payload = {
        "description": description,
        "operations": [
            {"checksum_material": operation.checksum_material, "name": operation.name}
            for operation in operations
        ],
        "version": version,
    }
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return sha256(canonical.encode("utf-8")).hexdigest()


def define_migration(
    *,
    version: str,
    description: str,
    operations: Sequence[MigrationOperation] = (),
    verify: MigrationVerify | None = None,
) -> Migration:
    """Create a migration whose checksum is deterministic across processes."""
    frozen_operations = tuple(operations)
    return Migration(
        version=version,
        description=description,
        checksum=stable_checksum(
            version=version,
            description=description,
            operations=frozen_operations,
        ),
        operations=frozen_operations,
        verify=verify or _no_op_verify,
    )


def _no_op_verify(context: "MigrationContext") -> None:
    del context


class MigrationContext:
    """Whitelist-only migration API; migrations never receive a raw connection."""

    __slots__ = ("__connection",)

    def __init__(self, connection: Connection) -> None:
        self.__connection = connection

    @property
    def dialect_name(self) -> str:
        return self.__connection.dialect.name

    def list_tables(self) -> tuple[str, ...]:
        return tuple(sorted(inspect(self.__connection).get_table_names()))

    def has_table(self, table: str) -> bool:
        return self._validate_identifier(table) in set(self.list_tables())

    def column_signature(self, table: str, column: str) -> ColumnSignature | None:
        """Return the exact observable shape, or ``None`` only when absent."""
        safe_table = self._validate_identifier(table)
        safe_column = self._validate_identifier(column)
        if not self.has_table(safe_table):
            return None
        raw = next(
            (item for item in inspect(self.__connection).get_columns(safe_table) if item["name"] == safe_column),
            None,
        )
        if raw is None:
            return None
        type_expression = re.sub(r"\s+", " ", str(raw.get("type") or "")).strip().lower()
        return ColumnSignature(
            name=safe_column,
            data_type=self._base_type(type_expression),
            nullable=bool(raw.get("nullable")),
            default=self._normalize_default(raw.get("default")),
            type_expression=type_expression,
        )

    def has_column(self, table: str, column: str) -> bool:
        return self.column_signature(table, column) is not None

    def has_column_shape(
        self,
        table: str,
        column: str,
        *,
        data_types: Sequence[str],
        nullable: bool,
        default: str | None,
    ) -> bool:
        """Accept a column only if all requested type/null/default properties match."""
        signature = self.column_signature(table, column)
        expected_types = {self._base_type(item) for item in data_types}
        return bool(
            signature is not None
            and signature.data_type in expected_types
            and signature.nullable is nullable
            and signature.default == self._normalize_default(default)
        )

    def index_signature(self, table: str, index: str) -> IndexSignature | None:
        """Return ordered index/UNIQUE fields; name-only presence is insufficient."""
        safe_table = self._validate_identifier(table)
        safe_index = self._validate_identifier(index)
        if not self.has_table(safe_table):
            return None
        inspector = inspect(self.__connection)
        for raw in inspector.get_indexes(safe_table):
            if raw.get("name") == safe_index:
                return IndexSignature(
                    name=safe_index,
                    columns=tuple(str(column) for column in raw.get("column_names") or ()),
                    unique=bool(raw.get("unique")),
                )
        for raw in inspector.get_unique_constraints(safe_table):
            if raw.get("name") == safe_index:
                return IndexSignature(
                    name=safe_index,
                    columns=tuple(str(column) for column in raw.get("column_names") or ()),
                    unique=True,
                )
        return None

    def has_index(self, table: str, index: str) -> bool:
        return self.index_signature(table, index) is not None

    def has_index_shape(
        self,
        table: str,
        index: str,
        *,
        columns: Sequence[str],
        unique: bool,
    ) -> bool:
        signature = self.index_signature(table, index)
        expected_columns = tuple(self._validate_identifier(column) for column in columns)
        return bool(
            signature is not None
            and signature.columns == expected_columns
            and signature.unique is unique
        )

    def primary_key_columns(self, table: str) -> tuple[str, ...]:
        """Return the ordered primary-key columns for exact create-table verification."""
        safe_table = self._validate_identifier(table)
        if not self.has_table(safe_table):
            return ()
        raw = inspect(self.__connection).get_pk_constraint(safe_table)
        return tuple(str(column) for column in raw.get("constrained_columns") or ())

    def unique_signatures(self, table: str) -> tuple[IndexSignature, ...]:
        """Return every non-primary UNIQUE shape, including unique indexes."""
        safe_table = self._validate_identifier(table)
        if not self.has_table(safe_table):
            return ()
        inspector = inspect(self.__connection)
        signatures: dict[tuple[tuple[str, ...], str], IndexSignature] = {}
        for raw in inspector.get_unique_constraints(safe_table):
            columns = tuple(str(column) for column in raw.get("column_names") or ())
            name = str(raw.get("name") or "")
            signatures[(columns, name)] = IndexSignature(name=name, columns=columns, unique=True)
        for raw in inspector.get_indexes(safe_table):
            if not raw.get("unique"):
                continue
            columns = tuple(str(column) for column in raw.get("column_names") or ())
            name = str(raw.get("name") or "")
            signatures.setdefault(
                (columns, name),
                IndexSignature(name=name, columns=columns, unique=True),
            )
        return tuple(sorted(signatures.values(), key=lambda item: (item.columns, item.name)))

    def foreign_key_signatures(self, table: str) -> tuple[ForeignKeySignature, ...]:
        """Return exact FK column/target/delete-policy shapes without exposing SQL."""
        safe_table = self._validate_identifier(table)
        if not self.has_table(safe_table):
            return ()
        signatures = []
        for raw in inspect(self.__connection).get_foreign_keys(safe_table):
            options = raw.get("options") or {}
            ondelete = options.get("ondelete")
            signatures.append(
                ForeignKeySignature(
                    columns=tuple(str(column) for column in raw.get("constrained_columns") or ()),
                    referred_table=str(raw.get("referred_table") or ""),
                    referred_columns=tuple(str(column) for column in raw.get("referred_columns") or ()),
                    ondelete=str(ondelete).upper() if ondelete else None,
                )
            )
        return tuple(
            sorted(
                signatures,
                key=lambda item: (item.columns, item.referred_table, item.referred_columns),
            )
        )

    def require_ddl_preflight(self, target: Any) -> Any:
        """Run strict read-only DDL checks and reject BLOCK/UNKNOWN evidence."""
        from .preflight import DdlPreflight

        report = DdlPreflight(self.__connection).run(target)
        if not report.is_safe:
            blocked = ", ".join(check.name for check in report.blocking_checks)
            raise MigrationSafetyError(
                f"online DDL preflight rejected {target.migration_version}:{target.table}; checks={blocked}",
                version=target.migration_version,
            )
        return report

    def create_table(
        self,
        table: str,
        columns: Sequence[tuple[str, str]],
        *,
        constraints: Sequence[str] = (),
    ) -> None:
        """Create a table only when absent; table definitions reject destructive SQL."""
        safe_table = self._validate_identifier(table)
        if self.has_table(safe_table):
            return
        if not columns:
            raise MigrationSafetyError("create_table requires at least one column")
        definitions = [
            f"{self._quote(self._validate_identifier(column))} {self._validate_fragment(definition)}"
            for column, definition in columns
        ]
        definitions.extend(self._validate_fragment(constraint) for constraint in constraints)
        self.__connection.execute(
            text(f"CREATE TABLE {self._quote(safe_table)} ({', '.join(definitions)})")
        )

    def add_column(
        self,
        table: str,
        column: str,
        definition: str,
        *,
        mysql_algorithm: str | None = None,
        mysql_lock: str | None = None,
    ) -> None:
        """Add a column only when absent, with explicit MySQL online DDL when requested."""
        safe_table = self._validate_identifier(table)
        safe_column = self._validate_identifier(column)
        if self.has_column(safe_table, safe_column):
            return
        if (mysql_algorithm is None) != (mysql_lock is None):
            raise MigrationSafetyError("MySQL online DDL requires both algorithm and lock")
        suffix = ""
        if mysql_algorithm is not None and mysql_lock is not None:
            algorithm = mysql_algorithm.upper()
            lock = mysql_lock.upper()
            if algorithm not in {"INSTANT", "INPLACE"} or lock not in {"NONE", "SHARED"}:
                raise MigrationSafetyError("unsupported MySQL online DDL option")
            if self.dialect_name == "mysql":
                suffix = f", ALGORITHM={algorithm}, LOCK={lock}"
        self.__connection.execute(
            text(
                f"ALTER TABLE {self._quote(safe_table)} "
                f"ADD COLUMN {self._quote(safe_column)} {self._validate_fragment(definition)}{suffix}"
            )
        )

    def create_index(
        self,
        table: str,
        index: str,
        columns: Sequence[str],
        *,
        unique: bool = False,
        ddl_target: Any | None = None,
    ) -> None:
        """Create an index only when absent.

        Existing-table indexes must pass an explicit read-only DDL preflight;
        callers creating indexes as part of a brand-new table may omit the
        target because the table has no existing data or lock surface yet.
        """
        safe_table = self._validate_identifier(table)
        safe_index = self._validate_identifier(index)
        safe_columns = tuple(self._validate_identifier(column) for column in columns)
        if not safe_columns:
            raise MigrationSafetyError("create_index requires at least one column")
        if ddl_target is not None:
            self.require_ddl_preflight(ddl_target)
        if self.has_index(safe_table, safe_index):
            return
        unique_sql = "UNIQUE " if unique else ""
        self.__connection.execute(
            text(
                f"CREATE {unique_sql}INDEX {self._quote(safe_index)} "
                f"ON {self._quote(safe_table)} ({', '.join(self._quote(column) for column in safe_columns)})"
            )
        )

    def alter_column(
        self,
        table: str,
        column: str,
        definition: str,
        *,
        ddl_target: Any,
    ) -> None:
        """Apply a preflighted compatible MySQL column type adjustment.

        There is intentionally no generic SQL escape hatch.  SQLite unit tests
        cannot emulate MySQL ``MODIFY COLUMN`` and therefore fail closed rather
        than pretending that a type migration ran.
        """
        safe_table = self._validate_identifier(table)
        safe_column = self._validate_identifier(column)
        if not self.has_column(safe_table, safe_column):
            raise MigrationSafetyError(f"cannot alter missing column: {safe_table}.{safe_column}")
        self.require_ddl_preflight(ddl_target)
        if self.dialect_name != "mysql":
            raise MigrationSafetyError("compatible column type adjustment requires MySQL")
        self.__connection.execute(
            text(
                f"ALTER TABLE {self._quote(safe_table)} MODIFY COLUMN "
                f"{self._quote(safe_column)} {self._validate_fragment(definition)}, "
                "ALGORITHM=INPLACE, LOCK=NONE"
            )
        )

    def update_rows(
        self,
        table: str,
        assignments: Mapping[str, Any],
        *,
        where_sql: str,
        where_params: Mapping[str, Any] | None = None,
    ) -> int:
        """Perform a parameterized, conditional update; broad deletes are not exposed."""
        safe_table = self._validate_identifier(table)
        if not assignments:
            raise MigrationSafetyError("update_rows requires assignments")
        predicate = self._validate_predicate(where_sql)
        values: dict[str, Any] = dict(where_params or {})
        set_sql: list[str] = []
        for position, (column, value) in enumerate(assignments.items()):
            parameter = f"migration_set_{position}"
            set_sql.append(f"{self._quote(self._validate_identifier(column))} = :{parameter}")
            values[parameter] = value
        result = self.__connection.execute(
            text(f"UPDATE {self._quote(safe_table)} SET {', '.join(set_sql)} WHERE {predicate}"),
            values,
        )
        return int(result.rowcount or 0)

    def count_rows(self, table: str, *, where_sql: str | None = None, params: Mapping[str, Any] | None = None) -> int:
        """Read-only row count for migration verification and online-DDL preflight code."""
        safe_table = self._validate_identifier(table)
        where = ""
        if where_sql is not None:
            where = f" WHERE {self._validate_predicate(where_sql)}"
        value = self.__connection.scalar(
            text(f"SELECT COUNT(*) FROM {self._quote(safe_table)}{where}"),
            params or {},
        )
        return int(value or 0)

    @staticmethod
    def _base_type(value: str) -> str:
        normalized = re.sub(r"\s+", " ", str(value).strip().lower())
        return re.split(r"[\s(]", normalized, maxsplit=1)[0]

    @staticmethod
    def _normalize_default(value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        if not normalized or normalized.upper() == "NULL":
            return None
        return normalized.strip("'")

    @staticmethod
    def _validate_identifier(identifier: str) -> str:
        if not _IDENTIFIER.fullmatch(identifier):
            raise MigrationSafetyError(f"unsafe identifier: {identifier!r}")
        return identifier

    @staticmethod
    def _validate_fragment(fragment: str) -> str:
        if not fragment.strip() or _FORBIDDEN_SQL.search(fragment):
            raise MigrationSafetyError("schema fragment contains forbidden SQL")
        return fragment

    @staticmethod
    def _validate_predicate(predicate: str) -> str:
        normalized = re.sub(r"\s+", "", predicate).lower()
        if (
            not normalized
            or normalized in {"1=1", "(1=1)", "true", "(true)"}
            or _FORBIDDEN_SQL.search(predicate)
        ):
            raise MigrationSafetyError("migration updates require a bounded, safe WHERE predicate")
        return predicate

    @staticmethod
    def _quote(identifier: str) -> str:
        return f"`{identifier}`"
