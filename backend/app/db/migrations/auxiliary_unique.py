"""Safe, reusable declarations for auxiliary natural-key UNIQUE migrations.

This module declares one forward-only migration per auxiliary dataset but deliberately
registers none of them.  S4.4 must choose each dataset's concrete version only after
its inventory review and real-MySQL go/no-go evidence.  The declaration never receives
a raw connection and can only create its constraint through ``MigrationContext``.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from .contract import (
    Migration,
    MigrationContext,
    MigrationOperation,
    MigrationSafetyError,
    OperationState,
    define_migration,
)
from .preflight import DdlOperation, DdlTarget


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# These provider feeds are event-like multisets, not mutable state rows.  Their
# approved identity contract is an immutable partition snapshot whose row identity is
# (batch_id, row_ordinal).  Keeping this guard in the declaration itself makes it
# impossible for inventory mistakes or future callers to materialize a per-row
# natural-key UNIQUE migration for them.
FORBIDDEN_PER_ROW_UNIQUE_DATASETS = frozenset(
    {"block_trade", "share_float", "holder_trade", "institutional_survey", "news_announcement"}
)


@dataclass(frozen=True)
class AuxiliaryUniqueConstraintDeclaration:
    """One dataset's independently versioned, preflighted natural-key constraint.

    The declared index name, ordered natural key, and target migration version form a
    stable schema contract.  A same-name index with another shape is rejected; the
    migration never deletes or replaces it automatically.
    """

    version: str
    dataset_key: str
    table: str
    index: str
    natural_key: tuple[str, ...]
    minimum_free_bytes: int = 256 * 1024 * 1024
    long_transaction_limit_seconds: int = 120
    maximum_estimated_rows: int | None = None
    maximum_table_bytes: int | None = None
    duplicate_sample_limit: int = 10

    def __post_init__(self) -> None:
        if not self.dataset_key.strip():
            raise ValueError("dataset_key must not be blank")
        if self.dataset_key in FORBIDDEN_PER_ROW_UNIQUE_DATASETS:
            raise ValueError(
                f"dataset {self.dataset_key!r} uses immutable partition snapshots and "
                "must not declare a per-row natural-key UNIQUE migration"
            )
        for label, value in (("table", self.table), ("index", self.index)):
            if not _IDENTIFIER.fullmatch(value):
                raise ValueError(f"unsafe {label} identifier: {value!r}")
        if len(self.index) > 64:
            raise ValueError("MySQL index name must be at most 64 characters")
        if not self.natural_key or len(self.natural_key) != len(set(self.natural_key)):
            raise ValueError("natural_key must contain unique, ordered columns")
        for column in self.natural_key:
            if not _IDENTIFIER.fullmatch(column):
                raise ValueError(f"unsafe natural_key identifier: {column!r}")
        # Construct once during declaration validation so invalid DDL limits fail
        # before a migration is handed to the production runner.
        self.ddl_target()

    def ddl_target(self) -> DdlTarget:
        """Return the immutable target consumed by ``MigrationContext.create_index``."""
        return DdlTarget(
            migration_version=self.version,
            operation=DdlOperation.ADD_UNIQUE,
            table=self.table,
            natural_key=self.natural_key,
            algorithm="INPLACE",
            lock="NONE",
            minimum_free_bytes=self.minimum_free_bytes,
            long_transaction_limit_seconds=self.long_transaction_limit_seconds,
            maximum_estimated_rows=self.maximum_estimated_rows,
            maximum_table_bytes=self.maximum_table_bytes,
            duplicate_sample_limit=self.duplicate_sample_limit,
        )

    def as_migration(self) -> Migration:
        """Build a one-operation migration using only the restricted context API."""
        target = self.ddl_target()

        def state(context: MigrationContext) -> OperationState:
            missing_columns = tuple(
                column for column in self.natural_key if not context.has_column(self.table, column)
            )
            if missing_columns:
                raise MigrationSafetyError(
                    f"auxiliary dataset {self.dataset_key!r} cannot create UNIQUE on "
                    f"{self.table}: missing columns={','.join(missing_columns)}",
                    version=self.version,
                )
            signature = context.index_signature(self.table, self.index)
            if signature is None:
                return OperationState.PENDING
            if context.has_index_shape(
                self.table,
                self.index,
                columns=self.natural_key,
                unique=True,
            ):
                return OperationState.SATISFIED
            raise MigrationSafetyError(
                f"auxiliary dataset {self.dataset_key!r} found incompatible index "
                f"{self.table}.{self.index}: columns={signature.columns!r}, unique={signature.unique}",
                version=self.version,
            )

        def apply(context: MigrationContext) -> None:
            # Passing ddl_target is mandatory: MigrationContext invokes strict
            # DdlPreflight and rejects BLOCK/UNKNOWN evidence before issuing DDL.
            context.create_index(
                self.table,
                self.index,
                self.natural_key,
                unique=True,
                ddl_target=target,
            )

        def verify(context: MigrationContext) -> None:
            if state(context) is not OperationState.SATISFIED:
                raise MigrationSafetyError(
                    f"auxiliary natural-key UNIQUE was not established: {self.table}.{self.index}",
                    version=self.version,
                )

        return define_migration(
            version=self.version,
            description=(
                f"Establish preflighted natural-key UNIQUE {self.index} for auxiliary dataset "
                f"{self.dataset_key}"
            ),
            operations=(
                MigrationOperation(
                    name=f"create_{self.index}",
                    checksum_material=(
                        f"dataset={self.dataset_key}; create unique index {self.index} on "
                        f"{self.table} ({','.join(self.natural_key)}); "
                        "requires DdlTarget ADD_UNIQUE INPLACE LOCK=NONE and duplicate preflight"
                    ),
                    precondition=state,
                    apply=apply,
                    postcondition=state,
                ),
            ),
            verify=verify,
        )


def registered_auxiliary_unique_migrations() -> tuple[Migration, ...]:
    """Return the frozen state-UNIQUE migrations for normal production discovery.

    The import stays inside this factory so the inventory can import the restricted
    declaration module during bootstrap without a package-initialization cycle.  The
    normal version registry calls this function; constructing the returned immutable
    declarations never opens a connection or executes DDL.
    """
    from app.services.auxiliary.production import AUXILIARY_UNIQUE_MIGRATION_PLANS

    return tuple(
        plan.declaration.as_migration()
        for plan in sorted(AUXILIARY_UNIQUE_MIGRATION_PLANS.values(), key=lambda item: item.version)
    )


__all__ = [
    "AuxiliaryUniqueConstraintDeclaration",
    "FORBIDDEN_PER_ROW_UNIQUE_DATASETS",
    "registered_auxiliary_unique_migrations",
]
