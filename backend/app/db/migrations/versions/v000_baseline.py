"""Adopt a fully verified existing application schema without modifying business tables."""
from __future__ import annotations

from ..contract import (
    MigrationBaselineError,
    MigrationContext,
    MigrationOperation,
    OperationState,
    define_migration,
)
from ..schema_inventory import BASELINE_REQUIRED_COLUMNS, BASELINE_REQUIRED_INDEXES

# V000 is a strict adoption checkpoint.  The machine-readable inventory is
# shared with the audit/documentation layer so the baseline cannot silently
# drift away from the legacy init_db surface.


def _missing_baseline_requirements(context: MigrationContext) -> tuple[str, ...]:
    missing: list[str] = []
    for table, columns in BASELINE_REQUIRED_COLUMNS.items():
        if not context.has_table(table):
            missing.append(f"table:{table}")
            continue
        for column in columns:
            if not context.has_column(table, column):
                missing.append(f"column:{table}.{column}")
    if context.dialect_name != "mysql":
        return tuple(missing)
    for table, indexes in BASELINE_REQUIRED_INDEXES.items():
        if not context.has_table(table):
            continue
        for index in indexes:
            if not context.has_index(table, index):
                missing.append(f"index:{table}.{index}")
    return tuple(missing)


def _baseline_state(context: MigrationContext) -> OperationState:
    return OperationState.SATISFIED if not _missing_baseline_requirements(context) else OperationState.PENDING


def _reject_incomplete_baseline(context: MigrationContext) -> None:
    missing = _missing_baseline_requirements(context)
    if missing:
        preview = ", ".join(missing[:8])
        suffix = " ..." if len(missing) > 8 else ""
        raise MigrationBaselineError(
            f"cannot adopt incomplete existing schema baseline: {preview}{suffix}",
            version="V000",
        )


def _verify_baseline(context: MigrationContext) -> None:
    _reject_incomplete_baseline(context)


migration = define_migration(
    version="V000",
    description="Adopt verified existing application schema baseline",
    operations=(
        MigrationOperation(
            name="verify_existing_application_schema_inventory",
            checksum_material="requires every legacy init_db table, column, and index target; never creates or deletes business tables",
            precondition=_baseline_state,
            apply=_reject_incomplete_baseline,
            postcondition=_baseline_state,
        ),
    ),
    verify=_verify_baseline,
)
