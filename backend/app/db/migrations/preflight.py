"""Strict, read-only safety gates for MySQL online DDL.

The preflight accepts only an immutable :class:`DdlTarget` manifest.  It never
executes DDL/DML, never writes a report to the database, and treats unavailable
safety evidence as a block rather than silently continuing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import re
import shutil
from typing import Mapping

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LOCAL_MYSQL_HOSTS = {"", "localhost", "127.0.0.1", "::1"}


class DdlOperation(str, Enum):
    """Whitelisted online schema operations understood by the capability gate."""

    ADD_COLUMN = "add_column"
    ADD_INDEX = "add_index"
    ADD_UNIQUE = "add_unique"
    ALTER_COLUMN = "alter_column"


class PreflightStatus(str, Enum):
    PASS = "pass"
    BLOCK = "block"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class DdlTarget:
    """Immutable, non-SQL description of one forward-only DDL operation."""

    migration_version: str
    operation: DdlOperation
    table: str
    column: str | None = None
    expected_data_types: tuple[str, ...] = ()
    expected_nullable: bool | None = None
    expected_default: str | None = None
    natural_key: tuple[str, ...] = ()
    algorithm: str = "INSTANT"
    lock: str = "NONE"
    minimum_free_bytes: int = 256 * 1024 * 1024
    long_transaction_limit_seconds: int = 120

    def __post_init__(self) -> None:
        _validate_identifier(self.table)
        if not self.migration_version.strip():
            raise ValueError("migration_version must not be blank")
        if self.column is not None:
            _validate_identifier(self.column)
        if self.operation is DdlOperation.ADD_COLUMN and self.column is None:
            raise ValueError("add_column target requires column")
        if self.expected_data_types and self.column is None:
            raise ValueError("expected_data_types requires column")
        if not self.expected_data_types and self.operation is DdlOperation.ADD_COLUMN:
            raise ValueError("add_column target requires expected_data_types")
        for column in self.natural_key:
            _validate_identifier(column)
        if self.minimum_free_bytes < 0:
            raise ValueError("minimum_free_bytes must be non-negative")
        if self.long_transaction_limit_seconds < 1:
            raise ValueError("long_transaction_limit_seconds must be positive")
        if self.algorithm not in {"INSTANT", "INPLACE"}:
            raise ValueError("unsupported online DDL algorithm")
        if self.lock not in {"NONE", "SHARED"}:
            raise ValueError("unsupported online DDL lock level")


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    status: PreflightStatus
    details: tuple[tuple[str, str], ...] = ()

    @classmethod
    def create(
        cls,
        name: str,
        status: PreflightStatus,
        details: Mapping[str, object] | None = None,
    ) -> "PreflightCheck":
        return cls(
            name=name,
            status=status,
            details=tuple(sorted((str(key), str(value)) for key, value in (details or {}).items())),
        )

    @property
    def detail_map(self) -> Mapping[str, str]:
        return dict(self.details)


@dataclass(frozen=True)
class DdlPreflightReport:
    target: DdlTarget
    checks: tuple[PreflightCheck, ...] = field(default_factory=tuple)

    @property
    def is_safe(self) -> bool:
        return all(
            check.status in {PreflightStatus.PASS, PreflightStatus.NOT_APPLICABLE}
            for check in self.checks
        )

    @property
    def blocking_checks(self) -> tuple[PreflightCheck, ...]:
        return tuple(
            check
            for check in self.checks
            if check.status not in {PreflightStatus.PASS, PreflightStatus.NOT_APPLICABLE}
        )

    def as_dict(self) -> dict[str, object]:
        """Return operator-safe evidence without connection details or business rows."""
        return {
            "migration_version": self.target.migration_version,
            "operation": self.target.operation.value,
            "table": self.target.table,
            "column": self.target.column,
            "safe": self.is_safe,
            "checks": [
                {"name": check.name, "status": check.status.value, "details": dict(check.details)}
                for check in self.checks
            ],
        }


class DdlPreflight:
    """Collect the evidence required before the runner can issue online MySQL DDL."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def run(self, target: DdlTarget) -> DdlPreflightReport:
        if self._connection.dialect.name != "mysql":
            return DdlPreflightReport(
                target=target,
                checks=(
                    PreflightCheck.create(
                        "dialect",
                        PreflightStatus.NOT_APPLICABLE,
                        {"dialect": self._connection.dialect.name, "reason": "online MySQL DDL is not executed"},
                    ),
                ),
            )

        checks = (
            self._check_table(target),
            self._check_existing_column_shape(target),
            self._check_online_capability(target),
            self._check_local_disk(target),
            self._check_metadata_lock_waits(target),
            self._check_long_transactions(target),
            self._check_natural_key_duplicates(target),
        )
        return DdlPreflightReport(target=target, checks=checks)

    def _check_table(self, target: DdlTarget) -> PreflightCheck:
        try:
            row = self._connection.execute(
                text(
                    "SELECT ENGINE AS engine, TABLE_ROWS AS estimated_rows, "
                    "DATA_LENGTH AS data_bytes, INDEX_LENGTH AS index_bytes, DATA_FREE AS free_bytes "
                    "FROM information_schema.tables "
                    "WHERE table_schema = DATABASE() AND table_name = :table"
                ),
                {"table": target.table},
            ).mappings().first()
        except SQLAlchemyError as exc:
            return _unknown("table_inventory", exc)
        if row is None:
            return PreflightCheck.create("table_inventory", PreflightStatus.BLOCK, {"reason": "target_table_missing"})
        engine = str(row.get("engine") or "").lower()
        if engine != "innodb":
            return PreflightCheck.create(
                "table_inventory",
                PreflightStatus.BLOCK,
                {"engine": engine or "unknown", "reason": "online DDL requires InnoDB"},
            )
        return PreflightCheck.create(
            "table_inventory",
            PreflightStatus.PASS,
            {
                "engine": engine,
                "estimated_rows": int(row.get("estimated_rows") or 0),
                "data_bytes": int(row.get("data_bytes") or 0),
                "index_bytes": int(row.get("index_bytes") or 0),
                "free_bytes": int(row.get("free_bytes") or 0),
            },
        )

    def _check_existing_column_shape(self, target: DdlTarget) -> PreflightCheck:
        if target.column is None:
            return PreflightCheck.create("existing_column_shape", PreflightStatus.NOT_APPLICABLE)
        try:
            row = self._connection.execute(
                text(
                    "SELECT DATA_TYPE AS data_type, IS_NULLABLE AS is_nullable, "
                    "COLUMN_DEFAULT AS column_default FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = :table AND column_name = :column"
                ),
                {"table": target.table, "column": target.column},
            ).mappings().first()
        except SQLAlchemyError as exc:
            return _unknown("existing_column_shape", exc)
        if row is None:
            return PreflightCheck.create("existing_column_shape", PreflightStatus.PASS, {"state": "absent"})
        data_type = str(row.get("data_type") or "").lower()
        nullable = str(row.get("is_nullable") or "").upper() == "YES"
        default = _normalize_default(row.get("column_default"))
        expected_types = {item.lower() for item in target.expected_data_types}
        matches = (
            data_type in expected_types
            and (target.expected_nullable is None or nullable is target.expected_nullable)
            and default == _normalize_default(target.expected_default)
        )
        return PreflightCheck.create(
            "existing_column_shape",
            PreflightStatus.PASS if matches else PreflightStatus.BLOCK,
            {
                "state": "matching" if matches else "mismatch",
                "data_type": data_type,
                "nullable": nullable,
                "default": default if default is not None else "NULL",
            },
        )

    def _check_online_capability(self, target: DdlTarget) -> PreflightCheck:
        try:
            version = str(self._connection.scalar(text("SELECT VERSION()")) or "")
        except SQLAlchemyError as exc:
            return _unknown("online_capability", exc)
        match = re.match(r"^(\d+)\.(\d+)", version)
        if match is None:
            return PreflightCheck.create("online_capability", PreflightStatus.UNKNOWN, {"reason": "unknown_server_version"})
        major, minor = (int(match.group(1)), int(match.group(2)))
        supported = target.operation is DdlOperation.ADD_COLUMN and (major, minor) >= (8, 0)
        return PreflightCheck.create(
            "online_capability",
            PreflightStatus.PASS if supported else PreflightStatus.BLOCK,
            {
                "server_major": major,
                "server_minor": minor,
                "algorithm": target.algorithm,
                "lock": target.lock,
                "reason": "supported" if supported else "operation_or_server_not_in_capability_matrix",
            },
        )

    def _check_local_disk(self, target: DdlTarget) -> PreflightCheck:
        host = str(getattr(self._connection.engine.url, "host", "") or "").lower()
        if host not in _LOCAL_MYSQL_HOSTS:
            return PreflightCheck.create(
                "local_disk",
                PreflightStatus.UNKNOWN,
                {"reason": "remote_mysql_disk_is_not_observable_from_application_host"},
            )
        try:
            datadir = str(self._connection.scalar(text("SELECT @@datadir")) or "").strip()
            if not datadir:
                return PreflightCheck.create("local_disk", PreflightStatus.UNKNOWN, {"reason": "mysql_datadir_unavailable"})
            available = shutil.disk_usage(Path(datadir)).free
        except (OSError, SQLAlchemyError) as exc:
            return _unknown("local_disk", exc)
        return PreflightCheck.create(
            "local_disk",
            PreflightStatus.PASS if available >= target.minimum_free_bytes else PreflightStatus.BLOCK,
            {
                "available_bytes": available,
                "required_bytes": target.minimum_free_bytes,
                "reason": "sufficient" if available >= target.minimum_free_bytes else "insufficient_free_space",
            },
        )

    def _check_metadata_lock_waits(self, target: DdlTarget) -> PreflightCheck:
        try:
            pending = int(
                self._connection.scalar(
                    text(
                        "SELECT COUNT(*) FROM performance_schema.metadata_locks "
                        "WHERE object_type = 'TABLE' AND object_schema = DATABASE() "
                        "AND object_name = :table AND lock_status = 'PENDING'"
                    ),
                    {"table": target.table},
                )
                or 0
            )
        except SQLAlchemyError as exc:
            return _unknown("metadata_lock_waits", exc)
        return PreflightCheck.create(
            "metadata_lock_waits",
            PreflightStatus.PASS if pending == 0 else PreflightStatus.BLOCK,
            {"pending_waits": pending},
        )

    def _check_long_transactions(self, target: DdlTarget) -> PreflightCheck:
        try:
            blockers = int(
                self._connection.scalar(
                    text(
                        "SELECT COUNT(*) FROM information_schema.innodb_trx "
                        "WHERE TIMESTAMPDIFF(SECOND, trx_started, UTC_TIMESTAMP()) >= :limit_seconds"
                    ),
                    {"limit_seconds": target.long_transaction_limit_seconds},
                )
                or 0
            )
        except SQLAlchemyError as exc:
            return _unknown("long_transactions", exc)
        return PreflightCheck.create(
            "long_transactions",
            PreflightStatus.PASS if blockers == 0 else PreflightStatus.BLOCK,
            {"transactions_at_or_over_limit": blockers, "limit_seconds": target.long_transaction_limit_seconds},
        )

    def _check_natural_key_duplicates(self, target: DdlTarget) -> PreflightCheck:
        if not target.natural_key:
            return PreflightCheck.create("natural_key_duplicates", PreflightStatus.NOT_APPLICABLE)
        columns = ", ".join(_quote_identifier(column) for column in target.natural_key)
        try:
            duplicates = int(
                self._connection.scalar(
                    text(
                        f"SELECT COUNT(*) FROM (SELECT 1 FROM {_quote_identifier(target.table)} "
                        f"GROUP BY {columns} HAVING COUNT(*) > 1) AS duplicate_groups"
                    )
                )
                or 0
            )
        except SQLAlchemyError as exc:
            return _unknown("natural_key_duplicates", exc)
        return PreflightCheck.create(
            "natural_key_duplicates",
            PreflightStatus.PASS if duplicates == 0 else PreflightStatus.BLOCK,
            {"duplicate_groups": duplicates, "key_columns": ",".join(target.natural_key)},
        )


def _validate_identifier(value: str) -> None:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"unsafe identifier: {value!r}")


def _quote_identifier(value: str) -> str:
    _validate_identifier(value)
    return f"`{value}`"


def _normalize_default(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized or normalized.upper() == "NULL":
        return None
    return normalized.strip("'")


def _unknown(name: str, error: Exception) -> PreflightCheck:
    """Preserve actionable driver evidence without exposing a configured DB identity."""
    details: dict[str, object] = {"error_type": type(error).__name__}
    original = getattr(error, "orig", error)
    arguments = getattr(original, "args", ())
    if arguments and isinstance(arguments[0], int):
        details["error_code"] = arguments[0]
    message = re.sub(r"user '[^']+'@'[^']+'", "configured MySQL user", str(original))
    if message:
        details["error_message"] = message[:240]
    return PreflightCheck.create(name, PreflightStatus.UNKNOWN, details)


__all__ = [
    "DdlOperation",
    "DdlPreflight",
    "DdlPreflightReport",
    "DdlTarget",
    "PreflightCheck",
    "PreflightStatus",
]
