"""Declarative, forward-only schema plan for production hardening.

This module deliberately does not execute DDL.  It is the reviewed contract used by
later Alembic revisions: each operation has a machine-checkable precondition,
postcondition and no-op check, while task 0.3 only inventories and preflights the
changes against the current schema.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import re
from typing import Any, Iterable, Mapping

CURRENT_ALEMBIC_HEAD = "0003_backtest_holdout_claims"
MANIFEST_SCHEMA_VERSION = 1


class OperationKind(StrEnum):
    CREATE_TABLE = "create_table"
    ADD_COLUMN = "add_column"
    CREATE_INDEX = "create_index"


class OperationStatus(StrEnum):
    PENDING = "pending"
    SATISFIED_NO_OP = "satisfied_no_op"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    name: str
    type_family: str
    nullable: bool
    length: int | None = None
    precision: int | None = None
    scale: int | None = None
    fsp: int | None = None
    server_default: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type_family": self.type_family,
            "length": self.length,
            "precision": self.precision,
            "scale": self.scale,
            "fsp": self.fsp,
            "nullable": self.nullable,
            "server_default": self.server_default,
        }


@dataclass(frozen=True, slots=True)
class IndexSpec:
    name: str
    columns: tuple[str, ...]
    unique: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "columns": list(self.columns), "unique": self.unique}


@dataclass(frozen=True, slots=True)
class MigrationOperation:
    operation_id: str
    kind: OperationKind
    table: str
    column: ColumnSpec | None = None
    table_columns: tuple[ColumnSpec, ...] = ()
    table_primary_key: tuple[str, ...] = ()
    index: IndexSpec | None = None

    @property
    def object_name(self) -> str:
        if self.kind is OperationKind.ADD_COLUMN and self.column is not None:
            return f"{self.table}.{self.column.name}"
        if self.kind is OperationKind.CREATE_INDEX and self.index is not None:
            return self.index.name
        return self.table

    def contract(self) -> dict[str, str]:
        if self.kind is OperationKind.CREATE_TABLE:
            compatible = (
                f"table {self.table} exists with every declared column and primary key compatible"
            )
            absent = f"table {self.table} is absent"
        elif self.kind is OperationKind.ADD_COLUMN and self.column is not None:
            compatible = f"column {self.table}.{self.column.name} exists and is compatible"
            absent = f"column {self.table}.{self.column.name} is absent"
        elif self.kind is OperationKind.CREATE_INDEX and self.index is not None:
            compatible = f"index {self.index.name} exists with the declared ordered columns and uniqueness"
            absent = f"index {self.index.name} is absent"
        else:  # pragma: no cover - rejected by validate_manifest
            raise ValueError(f"invalid operation {self.operation_id}")
        return {
            "precondition": f"{absent}, or {compatible}",
            "postcondition": compatible,
            "no_op_verify": compatible,
            "failure_reentry": "reinspect this object and execute only when status is pending",
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "kind": self.kind.value,
            "table": self.table,
            "object_name": self.object_name,
            "column": self.column.as_dict() if self.column else None,
            "table_columns": [item.as_dict() for item in self.table_columns],
            "table_primary_key": list(self.table_primary_key),
            "index": self.index.as_dict() if self.index else None,
            **self.contract(),
        }


@dataclass(frozen=True, slots=True)
class UniqueKeyCheck:
    check_id: str
    table: str
    columns: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "table": self.table,
            "columns": list(self.columns),
            "null_policy": "exclude rows where any key component is null",
        }


@dataclass(frozen=True, slots=True)
class MigrationBatch:
    batch_id: str
    proposed_revision: str
    depends_on: str
    purpose: str
    implementation_tasks: tuple[str, ...]
    operations: tuple[MigrationOperation, ...]
    unique_key_checks: tuple[UniqueKeyCheck, ...] = ()
    classification_mode: str = "none"
    deferred_hardening: tuple[str, ...] = ()

    def content_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "proposed_revision": self.proposed_revision,
            "depends_on": self.depends_on,
            "purpose": self.purpose,
            "implementation_tasks": list(self.implementation_tasks),
            "execution_owner": "future_reviewed_alembic_revision",
            "operations": [operation.as_dict() for operation in self.operations],
            "unique_key_checks": [check.as_dict() for check in self.unique_key_checks],
            "classification_mode": self.classification_mode,
            "deferred_hardening": list(self.deferred_hardening),
        }

    @property
    def checksum(self) -> str:
        rendered = json.dumps(
            self.content_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(rendered.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return {**self.content_dict(), "manifest_checksum": self.checksum}


@dataclass(frozen=True, slots=True)
class ColumnState:
    type_family: str
    nullable: bool
    length: int | None = None
    precision: int | None = None
    scale: int | None = None
    fsp: int | None = None


@dataclass(frozen=True, slots=True)
class IndexState:
    columns: tuple[str, ...]
    unique: bool


@dataclass(frozen=True, slots=True)
class TableState:
    columns: Mapping[str, ColumnState]
    indexes: Mapping[str, IndexState]


@dataclass(frozen=True, slots=True)
class SchemaInventory:
    tables: Mapping[str, TableState]


@dataclass(frozen=True, slots=True)
class OperationEvaluation:
    operation_id: str
    object_name: str
    status: OperationStatus
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {
            "operation_id": self.operation_id,
            "object_name": self.object_name,
            "status": self.status.value,
            "reason": self.reason,
        }


def _col(
    name: str,
    type_family: str,
    *,
    nullable: bool,
    length: int | None = None,
    precision: int | None = None,
    scale: int | None = None,
    fsp: int | None = None,
    server_default: str | None = None,
) -> ColumnSpec:
    return ColumnSpec(
        name=name,
        type_family=type_family,
        nullable=nullable,
        length=length,
        precision=precision,
        scale=scale,
        fsp=fsp,
        server_default=server_default,
    )


def _add_columns(batch: str, table: str, columns: Iterable[ColumnSpec]) -> tuple[MigrationOperation, ...]:
    return tuple(
        MigrationOperation(
            operation_id=f"{batch}.column.{table}.{column.name}",
            kind=OperationKind.ADD_COLUMN,
            table=table,
            column=column,
        )
        for column in columns
    )


def _table(
    batch: str,
    name: str,
    columns: Iterable[ColumnSpec],
    *,
    primary_key: Iterable[str],
) -> MigrationOperation:
    return MigrationOperation(
        operation_id=f"{batch}.table.{name}",
        kind=OperationKind.CREATE_TABLE,
        table=name,
        table_columns=tuple(columns),
        table_primary_key=tuple(primary_key),
    )


def _index(
    batch: str,
    table: str,
    name: str,
    columns: Iterable[str],
    *,
    unique: bool = False,
) -> MigrationOperation:
    return MigrationOperation(
        operation_id=f"{batch}.index.{name}",
        kind=OperationKind.CREATE_INDEX,
        table=table,
        index=IndexSpec(name=name, columns=tuple(columns), unique=unique),
    )


_EVIDENCE_COLUMNS = (
    _col("evidence_class", "varchar", nullable=True, length=40),
    _col("actual_generated_at", "datetime", nullable=True, fsp=6),
    _col("evidence_identity_hash", "char", nullable=True, length=64),
    _col("strategy_policy_fingerprint", "char", nullable=True, length=64),
    _col("execution_policy_fingerprint", "char", nullable=True, length=64),
    _col("code_revision", "varchar", nullable=True, length=64),
    _col("reconstruction_batch_id", "varchar", nullable=True, length=80),
    _col("provenance_schema_version", "smallint", nullable=True),
    _col("classification_reason", "varchar", nullable=True, length=80),
    _col("legacy_scope_json", "longtext", nullable=True),
)

_CLASSIFICATION_AUDIT_COLUMNS = (
    _col("run_id", "varchar", nullable=False, length=60),
    _col("plan_hash", "char", nullable=False, length=64),
    _col("planned_evidence_class", "varchar", nullable=False, length=40),
    _col("classification_reason", "varchar", nullable=False, length=80),
    _col("legacy_evaluation_scope", "varchar", nullable=True, length=32),
    _col("legacy_started_at", "datetime", nullable=True, fsp=6),
    _col("legacy_trade_date", "varchar", nullable=True, length=10),
    _col("legacy_recommended_at_min", "datetime", nullable=True, fsp=6),
    _col("legacy_recommended_at_max", "datetime", nullable=True, fsp=6),
    _col("recommendation_count", "bigint", nullable=False, server_default="0"),
    _col("outcome_count", "bigint", nullable=False, server_default="0"),
    _col("planned_at", "datetime", nullable=False, fsp=6),
    _col("reviewed_at", "datetime", nullable=True, fsp=6),
    _col("applied_at", "datetime", nullable=True, fsp=6),
)

_OUTCOME_VERSION_COLUMNS = (
    _col("calculation_fingerprint", "char", nullable=True, length=64),
    _col("result_hash", "char", nullable=True, length=64),
    _col("recommendation_identity_hash", "char", nullable=True, length=64),
    _col("market_snapshot_id", "varchar", nullable=True, length=100),
    _col("market_artifact_hash", "char", nullable=True, length=64),
    _col("execution_policy_fingerprint", "char", nullable=True, length=64),
    _col("calculation_code_revision", "varchar", nullable=True, length=64),
    _col("label_policy_fingerprint", "char", nullable=True, length=64),
    _col("calculation_schema_version", "smallint", nullable=True),
    _col("validity_status", "varchar", nullable=True, length=24),
    _col("legacy_verification_status", "varchar", nullable=True, length=32),
)

_OUTCOME_PROJECTION_COLUMNS = (
    _col("current_calculation_fingerprint", "char", nullable=True, length=64),
    _col("projection_revision", "bigint", nullable=True),
    _col("projected_at", "datetime", nullable=True, fsp=6),
)

_HOLDOUT_COLUMNS = (
    _col("holdout_population_fingerprint", "char", nullable=True, length=64),
    _col("legacy_holdout_fingerprint", "char", nullable=True, length=64),
    _col("identity_schema_version", "smallint", nullable=True),
    _col("verification_status", "varchar", nullable=True, length=32),
    _col("snapshot_id", "varchar", nullable=True, length=80),
    _col("signal_dates_hash", "char", nullable=True, length=64),
    _col("actual_signal_dates_json", "longtext", nullable=True),
    _col("universe_hash", "char", nullable=True, length=64),
    _col("universe_json", "longtext", nullable=True),
    _col("label_policy_fingerprint", "char", nullable=True, length=64),
    _col("label_policy_json", "longtext", nullable=True),
    _col("execution_policy_fingerprint", "char", nullable=True, length=64),
    _col("execution_policy_json", "longtext", nullable=True),
    _col("hypothesis_id", "varchar", nullable=True, length=160),
    _col("research_family_fingerprint", "char", nullable=True, length=64),
    _col("claimed_by_principal_id", "varchar", nullable=True, length=160),
    _col("caller_token_hash", "char", nullable=True, length=64),
    _col("claim_revision", "bigint", nullable=True),
)

_RESEARCH_FAMILY_COLUMNS = (
    _col("research_family_id", "varchar", nullable=False, length=80),
    _col("research_family_fingerprint", "char", nullable=False, length=64),
    _col("holdout_population_fingerprint", "char", nullable=False, length=64),
    _col("baseline_policy_fingerprint", "char", nullable=False, length=64),
    _col("hypothesis_family", "varchar", nullable=False, length=160),
    _col("candidate_manifest_hash", "char", nullable=False, length=64),
    _col("metrics_json", "longtext", nullable=False),
    _col("thresholds_json", "longtext", nullable=False),
    _col("allowed_evidence_scopes_json", "longtext", nullable=False),
    _col("definition_json", "longtext", nullable=False),
    _col("freeze_revision", "bigint", nullable=False, server_default="1"),
    _col("status", "varchar", nullable=False, length=32),
    _col("attempt_count", "integer", nullable=False, server_default="0"),
    _col("frozen_at", "datetime", nullable=True, fsp=6),
    _col("unsealed_at", "datetime", nullable=True, fsp=6),
    _col("created_at", "datetime", nullable=False, fsp=6),
)

_RESEARCH_CANDIDATE_COLUMNS = (
    _col("research_family_id", "varchar", nullable=False, length=80),
    _col("candidate_fingerprint", "char", nullable=False, length=64),
    _col("registration_order", "integer", nullable=False),
    _col("registered_at", "datetime", nullable=False, fsp=6),
)

_RESEARCH_ATTEMPT_COLUMNS = (
    _col("attempt_id", "varchar", nullable=False, length=80),
    _col("research_family_id", "varchar", nullable=False, length=80),
    _col("holdout_population_fingerprint", "char", nullable=False, length=64),
    _col("attempt_no", "integer", nullable=False),
    _col("candidate_count", "integer", nullable=False),
    _col("statistical_method", "varchar", nullable=False, length=80),
    _col("evidence_scope", "varchar", nullable=False, length=32),
    _col("research_path_classification", "varchar", nullable=False, length=32),
    _col("production_eligibility", "tinyint", nullable=False, server_default="0"),
    _col("complete_provenance_id", "varchar", nullable=False, length=100),
    _col("evidence_package_id", "varchar", nullable=False, length=100),
    _col("decision_status", "varchar", nullable=False, length=40),
    _col("result_json", "longtext", nullable=False),
    _col("evaluated_at", "datetime", nullable=False, fsp=6),
)

_RELEASE_DECISION_COLUMNS = (
    _col("decision_id", "char", nullable=False, length=64),
    _col("research_family_id", "varchar", nullable=False, length=80),
    _col("evidence_package_id", "varchar", nullable=False, length=100),
    _col("evidence_scope", "varchar", nullable=False, length=32),
    _col("research_path_classification", "varchar", nullable=False, length=32),
    _col("production_eligibility", "tinyint", nullable=False),
    _col("complete_provenance_id", "varchar", nullable=False, length=100),
    _col("current_policy_id", "varchar", nullable=False, length=100),
    _col("current_state_id", "varchar", nullable=False, length=100),
    _col("current_deployment_id", "varchar", nullable=False, length=100),
    _col("preregistered_gate_manifest_id", "varchar", nullable=False, length=100),
    _col("eligible", "tinyint", nullable=False),
    _col("gate_results_json", "longtext", nullable=False),
    _col("reason_codes_json", "longtext", nullable=False),
    _col("input_json", "longtext", nullable=False),
    _col("decision_schema_version", "smallint", nullable=False),
    _col("decided_at", "datetime", nullable=False, fsp=6),
)

_RELEASE_AUTHORITY_COLUMNS = (
    _col("authority_decision_id", "char", nullable=False, length=64),
    _col("release_decision_id", "char", nullable=False, length=64),
    _col("principal_id", "varchar", nullable=False, length=160),
    _col("action", "varchar", nullable=False, length=32),
    _col("authorized", "tinyint", nullable=False),
    _col("roles_json", "longtext", nullable=False),
    _col("reason_codes_json", "longtext", nullable=False),
    _col("decided_at", "datetime", nullable=False, fsp=6),
)

_RISK_POLICY_COLUMNS = (
    _col("policy_fingerprint", "char", nullable=False, length=64),
    _col("policy_id", "varchar", nullable=False, length=80),
    _col("policy_version", "varchar", nullable=False, length=40),
    _col("schema_version", "smallint", nullable=False),
    _col("artifact_json", "longtext", nullable=False),
    _col("status", "varchar", nullable=False, length=24),
    _col("created_at", "datetime", nullable=False, fsp=6),
    _col("published_at", "datetime", nullable=True, fsp=6),
)

_RISK_STATE_COLUMNS = (
    _col("state_id", "varchar", nullable=False, length=80),
    _col("state_scope", "varchar", nullable=False, length=80),
    _col("policy_fingerprint", "char", nullable=False, length=64),
    _col("as_of", "datetime", nullable=False, fsp=6),
    _col("capital", "decimal", nullable=False, precision=24, scale=8),
    _col("cash", "decimal", nullable=False, precision=24, scale=8),
    _col("occupied_capital", "decimal", nullable=False, precision=24, scale=8),
    _col("gross_exposure_pct", "decimal", nullable=False, precision=12, scale=8),
    _col("net_exposure_pct", "decimal", nullable=False, precision=12, scale=8),
    _col("pending_exposure_pct", "decimal", nullable=False, precision=12, scale=8),
    _col("industry_exposure_json", "longtext", nullable=False),
    _col("theme_exposure_json", "longtext", nullable=False),
    _col("realized_pnl_today_pct", "decimal", nullable=False, precision=12, scale=8),
    _col("unrealized_pnl_today_pct", "decimal", nullable=False, precision=12, scale=8),
    _col("equity_peak", "decimal", nullable=False, precision=24, scale=8),
    _col("current_drawdown_pct", "decimal", nullable=False, precision=12, scale=8),
    _col("locked_positions_json", "longtext", nullable=False),
    _col("halt_status", "varchar", nullable=False, length=24),
    _col("version", "bigint", nullable=False),
    _col("updated_at", "datetime", nullable=False, fsp=6),
)

_RISK_DECISION_COLUMNS = (
    _col("decision_id", "varchar", nullable=False, length=80),
    _col("state_id", "varchar", nullable=False, length=80),
    _col("state_version", "bigint", nullable=False),
    _col("state_hash", "char", nullable=False, length=64),
    _col("policy_fingerprint", "char", nullable=False, length=64),
    _col("run_id", "varchar", nullable=True, length=60),
    _col("recommendation_id", "varchar", nullable=True, length=80),
    _col("decision", "varchar", nullable=False, length=24),
    _col("requested_size", "decimal", nullable=False, precision=12, scale=8),
    _col("approved_size", "decimal", nullable=False, precision=12, scale=8),
    _col("reason_codes_json", "longtext", nullable=False),
    _col("decided_at", "datetime", nullable=False, fsp=6),
)

_RISK_RESERVATION_COLUMNS = (
    _col("reservation_id", "varchar", nullable=False, length=80),
    _col("reservation_key", "char", nullable=False, length=64),
    _col("decision_id", "varchar", nullable=False, length=80),
    _col("state_id", "varchar", nullable=False, length=80),
    _col("approved_size", "decimal", nullable=False, precision=12, scale=8),
    _col("status", "varchar", nullable=False, length=24),
    _col("lease_token", "varchar", nullable=False, length=128),
    _col("expires_at", "datetime", nullable=True, fsp=6),
    _col("released_at", "datetime", nullable=True, fsp=6),
    _col("created_at", "datetime", nullable=False, fsp=6),
    _col("version", "bigint", nullable=False),
)

_SCHEDULED_TASK_COLUMNS = (
    _col("misfire_policy", "varchar", nullable=True, length=32),
    _col("misfire_grace_seconds", "integer", nullable=True),
    _col("max_catch_up", "integer", nullable=True),
)

_SCHEDULED_RUN_COLUMNS = (
    _col("occurrence_key", "char", nullable=True, length=64),
    _col("business_date", "varchar", nullable=True, length=10),
    _col("occurrence_scope", "varchar", nullable=True, length=120),
    _col("scheduled_for", "datetime", nullable=True, fsp=6),
    _col("correlation_id", "varchar", nullable=True, length=80),
)

_OCCURRENCE_COLUMNS = (
    _col("occurrence_key", "char", nullable=False, length=64),
    _col("task_type", "varchar", nullable=False, length=40),
    _col("business_date", "varchar", nullable=False, length=10),
    _col("occurrence_scope", "varchar", nullable=False, length=120),
    _col("scheduled_for", "datetime", nullable=False, fsp=6),
    _col("status", "varchar", nullable=False, length=32),
    _col("claim_owner_id", "varchar", nullable=True, length=160),
    _col("lease_token", "varchar", nullable=True, length=128),
    _col("lease_expires_at", "datetime", nullable=True, fsp=6),
    _col("lease_grace_seconds", "integer", nullable=False, server_default="0"),
    _col("heartbeat_at", "datetime", nullable=True, fsp=6),
    _col("attempt_count", "integer", nullable=False, server_default="0"),
    _col("task_run_id", "varchar", nullable=True, length=80),
    _col("correlation_id", "varchar", nullable=True, length=80),
    _col("stable_error_code", "varchar", nullable=True, length=80),
    _col("version", "bigint", nullable=False, server_default="0"),
    _col("created_at", "datetime", nullable=False, fsp=6),
    _col("updated_at", "datetime", nullable=False, fsp=6),
)

_CONTROL_PLANE_COLUMNS = (
    _col("metadata_db_fingerprint", "char", nullable=False, length=64),
    _col("owner_id", "varchar", nullable=False, length=160),
    _col("host_identity", "varchar", nullable=False, length=160),
    _col("process_id", "bigint", nullable=False),
    _col("port", "integer", nullable=False),
    _col("lease_token", "varchar", nullable=False, length=128),
    _col("heartbeat_at", "datetime", nullable=False, fsp=6),
    _col("lease_expires_at", "datetime", nullable=False, fsp=6),
    _col("lease_grace_seconds", "integer", nullable=False),
    _col("override_actor", "varchar", nullable=True, length=120),
    _col("override_reason", "varchar", nullable=True, length=255),
    _col("version", "bigint", nullable=False, server_default="0"),
    _col("updated_at", "datetime", nullable=False, fsp=6),
)


EVIDENCE_BATCH = MigrationBatch(
    batch_id="M-EVIDENCE",
    proposed_revision="0004_evidence_provenance",
    depends_on=CURRENT_ALEMBIC_HEAD,
    purpose="Add nullable provenance identity and classification audit storage without changing legacy rows.",
    implementation_tasks=("1.2", "1.4"),
    operations=(
        *_add_columns("M-EVIDENCE", "recommendation_runs", _EVIDENCE_COLUMNS),
        _table(
            "M-EVIDENCE",
            "recommendation_evidence_classification_audits",
            _CLASSIFICATION_AUDIT_COLUMNS,
            primary_key=("plan_hash", "run_id"),
        ),
        _index("M-EVIDENCE", "recommendation_runs", "ix_rec_runs_evidence_trade_generated", ("evidence_class", "trade_date", "actual_generated_at")),
        _index("M-EVIDENCE", "recommendation_runs", "ix_rec_runs_reconstruction_batch", ("reconstruction_batch_id",)),
        _index("M-EVIDENCE", "recommendation_runs", "ix_rec_runs_evidence_identity_hash", ("evidence_identity_hash",)),
        _index("M-EVIDENCE", "recommendation_evidence_classification_audits", "ix_rec_evidence_audit_plan_class", ("plan_hash", "planned_evidence_class")),
    ),
    classification_mode="read_only_plan_only",
    deferred_hardening=(
        "After reviewed classification backfill, require provenance_schema_version for new writes only.",
        "Never rewrite evaluation_scope, recommended_at, started_at, or delete legacy evidence.",
    ),
)

OUTCOME_BATCH = MigrationBatch(
    batch_id="M-OUTCOME",
    proposed_revision="0005_outcome_fingerprint",
    depends_on=EVIDENCE_BATCH.proposed_revision,
    purpose="Add append-only outcome calculation identity and CAS fields to the existing current projection.",
    implementation_tasks=("5.2", "5.3", "5.4"),
    operations=(
        *_add_columns("M-OUTCOME", "recommendation_outcome_versions", _OUTCOME_VERSION_COLUMNS),
        *_add_columns("M-OUTCOME", "recommendation_outcomes", _OUTCOME_PROJECTION_COLUMNS),
        _index("M-OUTCOME", "recommendation_outcome_versions", "uq_outcome_versions_calc_fingerprint", ("calculation_fingerprint",), unique=True),
        _index("M-OUTCOME", "recommendation_outcome_versions", "ix_outcome_versions_rec_computed", ("recommendation_id", "computed_at")),
        _index("M-OUTCOME", "recommendation_outcomes", "ix_outcomes_current_fingerprint", ("current_calculation_fingerprint",)),
    ),
    unique_key_checks=(
        UniqueKeyCheck("outcome_calculation_fingerprint", "recommendation_outcome_versions", ("calculation_fingerprint",)),
    ),
    deferred_hardening=(
        "Backfill fingerprints append-only; do not overwrite result_json or delete duplicate/orphan rows.",
        "Require projection_revision only after legacy current rows receive revision zero.",
    ),
)

HOLDOUT_BATCH = MigrationBatch(
    batch_id="M-HOLDOUT",
    proposed_revision="0006_holdout_identity",
    depends_on=OUTCOME_BATCH.proposed_revision,
    purpose="Preserve legacy claims while adding canonical population, frozen research-family and release decisions.",
    implementation_tasks=("4.1", "4.2", "4.3"),
    operations=(
        *_add_columns("M-HOLDOUT", "backtest_holdout_claims", _HOLDOUT_COLUMNS),
        _table(
            "M-HOLDOUT",
            "backtest_research_families",
            _RESEARCH_FAMILY_COLUMNS,
            primary_key=("research_family_id",),
        ),
        _table(
            "M-HOLDOUT",
            "backtest_research_family_candidates",
            _RESEARCH_CANDIDATE_COLUMNS,
            primary_key=("research_family_id", "candidate_fingerprint"),
        ),
        _table(
            "M-HOLDOUT",
            "backtest_research_family_attempts",
            _RESEARCH_ATTEMPT_COLUMNS,
            primary_key=("attempt_id",),
        ),
        _table(
            "M-HOLDOUT",
            "backtest_release_eligibility_decisions",
            _RELEASE_DECISION_COLUMNS,
            primary_key=("decision_id",),
        ),
        _table(
            "M-HOLDOUT",
            "backtest_release_authority_decisions",
            _RELEASE_AUTHORITY_COLUMNS,
            primary_key=("authority_decision_id",),
        ),
        _index("M-HOLDOUT", "backtest_holdout_claims", "uq_holdout_population_fingerprint", ("holdout_population_fingerprint",), unique=True),
        _index("M-HOLDOUT", "backtest_holdout_claims", "ix_holdout_research_family", ("research_family_fingerprint",)),
        _index("M-HOLDOUT", "backtest_research_families", "uq_research_family_fingerprint", ("research_family_fingerprint",), unique=True),
        _index("M-HOLDOUT", "backtest_research_families", "ix_research_family_population", ("holdout_population_fingerprint",)),
        _index("M-HOLDOUT", "backtest_research_family_candidates", "uq_research_family_candidate", ("research_family_id", "candidate_fingerprint"), unique=True),
        _index("M-HOLDOUT", "backtest_research_family_attempts", "uq_research_family_attempt_no", ("research_family_id", "attempt_no"), unique=True),
        _index("M-HOLDOUT", "backtest_research_family_attempts", "ix_research_attempt_classification", ("evidence_scope", "research_path_classification", "production_eligibility")),
        _index("M-HOLDOUT", "backtest_release_eligibility_decisions", "ix_release_decision_family_package", ("research_family_id", "evidence_package_id")),
        _index("M-HOLDOUT", "backtest_release_eligibility_decisions", "ix_release_decision_classification", ("evidence_scope", "research_path_classification", "eligible")),
        _index("M-HOLDOUT", "backtest_release_authority_decisions", "ix_release_authority_release", ("release_decision_id", "action", "authorized")),
    ),
    unique_key_checks=(
        UniqueKeyCheck("holdout_population_fingerprint", "backtest_holdout_claims", ("holdout_population_fingerprint",)),
        UniqueKeyCheck("research_family_fingerprint", "backtest_research_families", ("research_family_fingerprint",)),
        UniqueKeyCheck("research_family_candidate", "backtest_research_family_candidates", ("research_family_id", "candidate_fingerprint")),
        UniqueKeyCheck("research_family_attempt", "backtest_research_family_attempts", ("research_family_id", "attempt_no")),
        UniqueKeyCheck("release_eligibility_decision", "backtest_release_eligibility_decisions", ("decision_id",)),
        UniqueKeyCheck("release_authority_decision", "backtest_release_authority_decisions", ("authority_decision_id",)),
    ),
    deferred_hardening=(
        "Copy the existing primary key into legacy_holdout_fingerprint; never delete or replace an existing claim.",
        "Mark rows without reconstructable population components as legacy_unverifiable.",
        "New claims use the server-derived population fingerprint as both the legacy primary key and canonical unique key.",
    ),
)

RISK_BATCH = MigrationBatch(
    batch_id="M-RISK",
    proposed_revision="0007_portfolio_risk",
    depends_on=HOLDOUT_BATCH.proposed_revision,
    purpose="Create versioned risk policy/state and append-only decision/reservation ledgers.",
    implementation_tasks=("6.1", "6.2", "6.3"),
    operations=(
        _table(
            "M-RISK",
            "portfolio_risk_policies",
            _RISK_POLICY_COLUMNS,
            primary_key=("policy_fingerprint",),
        ),
        _table(
            "M-RISK",
            "portfolio_risk_states",
            _RISK_STATE_COLUMNS,
            primary_key=("state_id",),
        ),
        _table(
            "M-RISK",
            "portfolio_risk_decisions",
            _RISK_DECISION_COLUMNS,
            primary_key=("decision_id",),
        ),
        _table(
            "M-RISK",
            "portfolio_risk_reservations",
            _RISK_RESERVATION_COLUMNS,
            primary_key=("reservation_id",),
        ),
        _index("M-RISK", "portfolio_risk_policies", "uq_risk_policy_id_version", ("policy_id", "policy_version"), unique=True),
        _index("M-RISK", "portfolio_risk_states", "uq_risk_state_scope", ("state_scope",), unique=True),
        _index("M-RISK", "portfolio_risk_states", "ix_risk_state_policy_updated", ("policy_fingerprint", "updated_at")),
        _index("M-RISK", "portfolio_risk_decisions", "ix_risk_decision_state_time", ("state_id", "decided_at")),
        _index("M-RISK", "portfolio_risk_decisions", "ix_risk_decision_recommendation", ("recommendation_id",)),
        _index("M-RISK", "portfolio_risk_reservations", "uq_risk_reservation_key", ("reservation_key",), unique=True),
        _index("M-RISK", "portfolio_risk_reservations", "ix_risk_reservation_state_status", ("state_id", "status", "expires_at")),
    ),
    unique_key_checks=(
        UniqueKeyCheck("risk_policy_version", "portfolio_risk_policies", ("policy_id", "policy_version")),
        UniqueKeyCheck("risk_state_scope", "portfolio_risk_states", ("state_scope",)),
        UniqueKeyCheck("risk_reservation_key", "portfolio_risk_reservations", ("reservation_key",)),
    ),
)

SCHEDULER_BATCH = MigrationBatch(
    batch_id="M-SCHEDULER",
    proposed_revision="0008_scheduler_claim",
    depends_on=RISK_BATCH.proposed_revision,
    purpose="Add database occurrence claims and one-owner control-plane leases without changing existing task runs.",
    implementation_tasks=("7.1", "7.2", "7.3"),
    operations=(
        *_add_columns("M-SCHEDULER", "scheduled_tasks", _SCHEDULED_TASK_COLUMNS),
        *_add_columns("M-SCHEDULER", "scheduled_task_runs", _SCHEDULED_RUN_COLUMNS),
        _table(
            "M-SCHEDULER",
            "scheduled_occurrences",
            _OCCURRENCE_COLUMNS,
            primary_key=("occurrence_key",),
        ),
        _table(
            "M-SCHEDULER",
            "prefect_control_plane_owners",
            _CONTROL_PLANE_COLUMNS,
            primary_key=("metadata_db_fingerprint",),
        ),
        _index("M-SCHEDULER", "scheduled_task_runs", "ix_task_runs_occurrence_key", ("occurrence_key",)),
        _index("M-SCHEDULER", "scheduled_occurrences", "uq_scheduled_occurrence_identity", ("task_type", "business_date", "occurrence_scope", "scheduled_for"), unique=True),
        _index("M-SCHEDULER", "scheduled_occurrences", "ix_scheduled_occurrence_lease", ("status", "lease_expires_at")),
        _index("M-SCHEDULER", "prefect_control_plane_owners", "ix_control_plane_lease_expiry", ("lease_expires_at",)),
    ),
    unique_key_checks=(
        UniqueKeyCheck("scheduled_occurrence_key", "scheduled_occurrences", ("occurrence_key",)),
        UniqueKeyCheck("scheduled_occurrence_identity", "scheduled_occurrences", ("task_type", "business_date", "occurrence_scope", "scheduled_for")),
        UniqueKeyCheck("control_plane_database", "prefect_control_plane_owners", ("metadata_db_fingerprint",)),
    ),
)

_LLM_COLUMNS = (
    # prompt context 裁剪/指纹审计摘要（不含完整 prompt 原文）
    _col("context_audit_json", "longtext", nullable=True),
)

LLM_BATCH = MigrationBatch(
    batch_id="M-LLM",
    proposed_revision="0009_llm_context_audit",
    depends_on=SCHEDULER_BATCH.proposed_revision,
    purpose="Persist prompt-context projection/truncation audit summaries on LLM call logs without storing full prompts.",
    implementation_tasks=("R-LLM-4",),
    operations=_add_columns("M-LLM", "api_call_logs", _LLM_COLUMNS),
)

MIGRATION_BATCHES = (
    EVIDENCE_BATCH,
    OUTCOME_BATCH,
    HOLDOUT_BATCH,
    RISK_BATCH,
    SCHEDULER_BATCH,
    LLM_BATCH,
)

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TYPE_ALIASES = {
    "int": "integer",
    "integer": "integer",
    "tinyint": "tinyint",
    "smallint": "smallint",
    "bigint": "bigint",
    "numeric": "decimal",
    "decimal": "decimal",
    "string": "varchar",
    "varchar": "varchar",
    "char": "char",
    "datetime": "datetime",
    "date": "date",
    "text": "text",
    "longtext": "longtext",
}


def canonical_type_family(value: str) -> str:
    normalized = value.strip().lower().replace(" ", "")
    normalized = normalized.split("(", 1)[0]
    return _TYPE_ALIASES.get(normalized, normalized)


def column_is_compatible(expected: ColumnSpec, observed: ColumnState) -> bool:
    if canonical_type_family(expected.type_family) != canonical_type_family(observed.type_family):
        return False
    if expected.nullable != observed.nullable:
        return False
    for attribute in ("length", "precision", "scale", "fsp"):
        wanted = getattr(expected, attribute)
        actual = getattr(observed, attribute)
        if wanted is not None and wanted != actual:
            return False
    return True


def evaluate_operation(
    operation: MigrationOperation,
    inventory: SchemaInventory,
    *,
    planned_tables: frozenset[str] = frozenset(),
) -> OperationEvaluation:
    table = inventory.tables.get(operation.table)
    if operation.kind is OperationKind.CREATE_TABLE:
        if table is None:
            return OperationEvaluation(operation.operation_id, operation.object_name, OperationStatus.PENDING, "table is absent")
        missing = [column.name for column in operation.table_columns if column.name not in table.columns]
        incompatible = [
            column.name
            for column in operation.table_columns
            if column.name in table.columns and not column_is_compatible(column, table.columns[column.name])
        ]
        primary_key = table.indexes.get("PRIMARY")
        primary_key_conflict = (
            primary_key is None
            or primary_key.columns != operation.table_primary_key
            or not primary_key.unique
        )
        if missing or incompatible or primary_key_conflict:
            return OperationEvaluation(
                operation.operation_id,
                operation.object_name,
                OperationStatus.BLOCKED,
                (
                    "existing table conflicts; "
                    f"missing={missing}, incompatible={incompatible}, "
                    f"primary_key_conflict={primary_key_conflict}"
                ),
            )
        return OperationEvaluation(operation.operation_id, operation.object_name, OperationStatus.SATISFIED_NO_OP, "compatible table already exists")

    if table is None:
        if operation.table in planned_tables:
            return OperationEvaluation(operation.operation_id, operation.object_name, OperationStatus.PENDING, "awaiting planned table creation")
        return OperationEvaluation(operation.operation_id, operation.object_name, OperationStatus.BLOCKED, "required existing table is absent")

    if operation.kind is OperationKind.ADD_COLUMN and operation.column is not None:
        observed = table.columns.get(operation.column.name)
        if observed is None:
            return OperationEvaluation(operation.operation_id, operation.object_name, OperationStatus.PENDING, "column is absent")
        if not column_is_compatible(operation.column, observed):
            return OperationEvaluation(operation.operation_id, operation.object_name, OperationStatus.BLOCKED, "column exists with an incompatible definition")
        return OperationEvaluation(operation.operation_id, operation.object_name, OperationStatus.SATISFIED_NO_OP, "compatible column already exists")

    if operation.kind is OperationKind.CREATE_INDEX and operation.index is not None:
        observed_index = table.indexes.get(operation.index.name)
        if observed_index is None:
            return OperationEvaluation(operation.operation_id, operation.object_name, OperationStatus.PENDING, "index is absent")
        if observed_index.columns != operation.index.columns or observed_index.unique != operation.index.unique:
            return OperationEvaluation(operation.operation_id, operation.object_name, OperationStatus.BLOCKED, "index name exists with incompatible columns or uniqueness")
        return OperationEvaluation(operation.operation_id, operation.object_name, OperationStatus.SATISFIED_NO_OP, "compatible index already exists")

    return OperationEvaluation(operation.operation_id, operation.object_name, OperationStatus.BLOCKED, "invalid operation contract")


def evaluate_batches(inventory: SchemaInventory) -> list[dict[str, Any]]:
    planned_tables = frozenset(
        operation.table
        for batch in MIGRATION_BATCHES
        for operation in batch.operations
        if operation.kind is OperationKind.CREATE_TABLE
    )
    result: list[dict[str, Any]] = []
    for batch in MIGRATION_BATCHES:
        evaluations = [
            evaluate_operation(operation, inventory, planned_tables=planned_tables)
            for operation in batch.operations
        ]
        counts = {status.value: 0 for status in OperationStatus}
        for evaluation in evaluations:
            counts[evaluation.status.value] += 1
        result.append(
            {
                "batch_id": batch.batch_id,
                "proposed_revision": batch.proposed_revision,
                "manifest_checksum": batch.checksum,
                "status_counts": counts,
                "operations": [evaluation.as_dict() for evaluation in evaluations],
            }
        )
    return result


def target_tables() -> tuple[str, ...]:
    names = {
        operation.table
        for batch in MIGRATION_BATCHES
        for operation in batch.operations
    }
    names.update({
        "recommendations",
        "recommendation_outcomes",
        "recommendation_outcome_versions",
        "backtest_holdout_claims",
        "scheduled_tasks",
        "scheduled_task_runs",
        "alembic_version",
    })
    return tuple(sorted(names))


def manifest_payload() -> dict[str, Any]:
    batches = [batch.as_dict() for batch in MIGRATION_BATCHES]
    manifest_digest = hashlib.sha256(
        json.dumps(batches, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "current_alembic_head": CURRENT_ALEMBIC_HEAD,
        "migration_owner": "alembic",
        "execution_mode": "plan_and_read_only_preflight",
        "batch_order": [batch.batch_id for batch in MIGRATION_BATCHES],
        "manifest_checksum": manifest_digest,
        "batches": batches,
    }


def validate_manifest(batches: Iterable[MigrationBatch] = MIGRATION_BATCHES) -> None:
    batch_list = tuple(batches)
    batch_ids = [batch.batch_id for batch in batch_list]
    revisions = [batch.proposed_revision for batch in batch_list]
    if len(set(batch_ids)) != len(batch_ids) or len(set(revisions)) != len(revisions):
        raise ValueError("migration batch IDs and revisions must be unique")
    if any(len(revision) > 32 for revision in revisions):
        raise ValueError("Alembic revisions must fit the default VARCHAR(32) version column")
    operation_ids: set[str] = set()
    expected_dependency = CURRENT_ALEMBIC_HEAD
    for batch in batch_list:
        if batch.depends_on != expected_dependency:
            raise ValueError(f"non-linear migration dependency at {batch.batch_id}")
        expected_dependency = batch.proposed_revision
        if batch.classification_mode not in {"none", "read_only_plan_only"}:
            raise ValueError(f"invalid classification mode for {batch.batch_id}")
        for operation in batch.operations:
            if operation.operation_id in operation_ids:
                raise ValueError(f"duplicate operation ID {operation.operation_id}")
            operation_ids.add(operation.operation_id)
            if operation.kind not in set(OperationKind):
                raise ValueError(f"unsupported operation kind {operation.kind}")
            if not _IDENTIFIER.fullmatch(operation.table):
                raise ValueError(f"unsafe table identifier {operation.table}")
            if operation.kind is OperationKind.ADD_COLUMN:
                if (
                    operation.column is None
                    or operation.table_columns
                    or operation.table_primary_key
                    or operation.index
                ):
                    raise ValueError(f"invalid add-column operation {operation.operation_id}")
                if not _IDENTIFIER.fullmatch(operation.column.name):
                    raise ValueError(f"unsafe column identifier {operation.column.name}")
            elif operation.kind is OperationKind.CREATE_TABLE:
                if (
                    not operation.table_columns
                    or not operation.table_primary_key
                    or operation.column
                    or operation.index
                ):
                    raise ValueError(f"invalid create-table operation {operation.operation_id}")
                names = [column.name for column in operation.table_columns]
                if len(names) != len(set(names)):
                    raise ValueError(f"duplicate table column in {operation.operation_id}")
                if not set(operation.table_primary_key) <= set(names):
                    raise ValueError(f"primary key references an unknown column in {operation.operation_id}")
                if not all(_IDENTIFIER.fullmatch(item) for item in operation.table_primary_key):
                    raise ValueError(f"unsafe primary key in {operation.operation_id}")
            elif operation.kind is OperationKind.CREATE_INDEX:
                if (
                    operation.index is None
                    or operation.column
                    or operation.table_columns
                    or operation.table_primary_key
                ):
                    raise ValueError(f"invalid create-index operation {operation.operation_id}")
                if len(operation.index.name) > 64 or not _IDENTIFIER.fullmatch(operation.index.name):
                    raise ValueError(f"unsafe MySQL index name {operation.index.name}")
                if not operation.index.columns:
                    raise ValueError(f"empty index {operation.index.name}")
        for check in batch.unique_key_checks:
            if not _IDENTIFIER.fullmatch(check.table) or not all(_IDENTIFIER.fullmatch(item) for item in check.columns):
                raise ValueError(f"unsafe duplicate check {check.check_id}")
    if EVIDENCE_BATCH.classification_mode != "read_only_plan_only":
        raise ValueError("legacy evidence classification must remain read-only in task 0.3")


validate_manifest()
