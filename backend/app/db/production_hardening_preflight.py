"""Read-only MySQL preflight for the production-hardening migration plan."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Any, Mapping, Sequence

from sqlalchemy import bindparam, inspect, text
from sqlalchemy.engine import Connection

from app.db.production_hardening_migration_plan import (
    MIGRATION_BATCHES,
    ColumnState,
    IndexState,
    OperationStatus,
    SchemaInventory,
    TableState,
    evaluate_batches,
    manifest_payload,
    target_tables,
)

PREFLIGHT_SCHEMA_VERSION = 1
CLASSIFICATION_RULE_VERSION = "legacy-evidence-read-only/v1"
_FORBIDDEN_SQL = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|ALTER|CREATE|DROP|TRUNCATE|REPLACE|RENAME|GRANT|REVOKE|CALL|LOAD|UNLOCK)\b",
    re.IGNORECASE,
)
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def assert_read_only_statement(statement: str) -> None:
    """Reject anything outside the small statement set used by this preflight."""
    normalized = statement.strip().rstrip(";").strip()
    if not normalized:
        raise ValueError("empty SQL statement")
    if ";" in normalized:
        raise ValueError("multiple SQL statements are forbidden")
    upper = normalized.upper()
    allowed = upper.startswith(("SELECT ", "SELECT\n", "WITH ", "WITH\n", "SHOW ", "EXPLAIN "))
    allowed = allowed or upper == "SET TRANSACTION READ ONLY"
    if not allowed or _FORBIDDEN_SQL.search(normalized):
        raise ValueError("preflight SQL must be read-only")


def _safe_error(error: Exception) -> dict[str, str]:
    return {"status": "unavailable", "error_code": type(error).__name__}


def _type_attribute(value: Any, name: str) -> int | None:
    raw = getattr(value, name, None)
    return int(raw) if isinstance(raw, int) else None


def collect_schema_inventory(connection: Connection) -> SchemaInventory:
    inspector = inspect(connection)
    existing = set(inspector.get_table_names())
    tables: dict[str, TableState] = {}
    for table_name in target_tables():
        if table_name not in existing:
            continue
        columns: dict[str, ColumnState] = {}
        for column in inspector.get_columns(table_name):
            sql_type = column["type"]
            columns[str(column["name"])] = ColumnState(
                type_family=sql_type.__class__.__name__.lower(),
                nullable=bool(column.get("nullable", True)),
                length=_type_attribute(sql_type, "length"),
                precision=_type_attribute(sql_type, "precision"),
                scale=_type_attribute(sql_type, "scale"),
                fsp=_type_attribute(sql_type, "fsp"),
            )
        indexes: dict[str, IndexState] = {}
        for index in inspector.get_indexes(table_name):
            name = index.get("name")
            columns_value = index.get("column_names")
            if name and columns_value:
                indexes[str(name)] = IndexState(
                    columns=tuple(str(item) for item in columns_value),
                    unique=bool(index.get("unique", False)),
                )
        for constraint in inspector.get_unique_constraints(table_name):
            name = constraint.get("name")
            columns_value = constraint.get("column_names")
            if name and columns_value:
                indexes.setdefault(
                    str(name),
                    IndexState(
                        columns=tuple(str(item) for item in columns_value),
                        unique=True,
                    ),
                )
        primary_key = inspector.get_pk_constraint(table_name)
        primary_columns = primary_key.get("constrained_columns")
        if primary_columns:
            indexes["PRIMARY"] = IndexState(
                columns=tuple(str(item) for item in primary_columns),
                unique=True,
            )
        tables[table_name] = TableState(columns=columns, indexes=indexes)
    return SchemaInventory(tables=tables)


def schema_inventory_report(inventory: SchemaInventory) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for table_name in target_tables():
        table = inventory.tables.get(table_name)
        if table is None:
            report[table_name] = {"exists": False}
            continue
        report[table_name] = {
            "exists": True,
            "columns": {
                name: {
                    "type_family": value.type_family,
                    "length": value.length,
                    "precision": value.precision,
                    "scale": value.scale,
                    "fsp": value.fsp,
                    "nullable": value.nullable,
                }
                for name, value in sorted(table.columns.items())
            },
            "indexes": {
                name: {"columns": list(value.columns), "unique": value.unique}
                for name, value in sorted(table.indexes.items())
            },
        }
    return report


def collect_table_scale(connection: Connection) -> dict[str, Any]:
    if connection.dialect.name != "mysql":
        return {"status": "unsupported_dialect", "tables": []}
    statement = """
        SELECT table_name AS target_table_name,
               engine AS storage_engine,
               row_format AS storage_row_format,
               table_rows AS estimated_table_rows,
               data_length AS table_data_bytes,
               index_length AS table_index_bytes,
               data_free AS allocated_table_free_bytes
        FROM information_schema.tables
        WHERE table_schema = DATABASE() AND table_name IN :table_names
        ORDER BY table_name
    """
    assert_read_only_statement(statement)
    query = text(statement).bindparams(bindparam("table_names", expanding=True))
    rows = connection.execute(query, {"table_names": list(target_tables())}).mappings().all()
    projected = [
        {
            "table": str(row["target_table_name"]),
            "engine": str(row["storage_engine"] or "unknown"),
            "row_format": str(row["storage_row_format"] or "unknown"),
            "estimated_rows": int(row["estimated_table_rows"] or 0),
            "data_bytes": int(row["table_data_bytes"] or 0),
            "index_bytes": int(row["table_index_bytes"] or 0),
            "allocated_free_bytes": int(row["allocated_table_free_bytes"] or 0),
        }
        for row in rows
    ]
    return {
        "status": "observed",
        "row_count_semantics": "information_schema estimate; exact counts are intentionally not forced on large tables",
        "tables": projected,
        "affected_total_bytes": sum(item["data_bytes"] + item["index_bytes"] for item in projected),
    }


def _current_alembic_revision(connection: Connection, inventory: SchemaInventory) -> str | None:
    if "alembic_version" not in inventory.tables:
        return None
    statement = "SELECT version_num FROM alembic_version"
    assert_read_only_statement(statement)
    return connection.execute(text(statement)).scalar_one_or_none()


def collect_compatibility(
    connection: Connection,
    inventory: SchemaInventory,
    table_scale: Mapping[str, Any],
) -> dict[str, Any]:
    version = tuple(int(item) for item in (connection.dialect.server_version_info or ()))
    version_text = ".".join(str(item) for item in version[:3]) or "unknown"
    result: dict[str, Any] = {
        "dialect": connection.dialect.name,
        "server_version": version_text,
        "mysql_8_or_newer": connection.dialect.name == "mysql" and version >= (8, 0),
        "current_alembic_revision": _current_alembic_revision(connection, inventory),
        "expected_alembic_revision": manifest_payload()["current_alembic_head"],
    }
    if connection.dialect.name != "mysql":
        result["status"] = "unsupported_dialect"
        return result
    variables_statement = """
        SELECT @@transaction_isolation AS isolation_value,
               @@lower_case_table_names AS lower_case_names_value,
               @@character_set_database AS database_charset_value,
               @@collation_database AS database_collation_value,
               @@innodb_page_size AS innodb_page_size_value
    """
    assert_read_only_statement(variables_statement)
    try:
        variables = connection.execute(text(variables_statement)).mappings().one()
        result["variables"] = {
            "transaction_isolation": str(variables["isolation_value"]),
            "lower_case_table_names": int(variables["lower_case_names_value"]),
            "character_set_database": str(variables["database_charset_value"]),
            "collation_database": str(variables["database_collation_value"]),
            "innodb_page_size": int(variables["innodb_page_size_value"]),
        }
    except Exception as error:  # pragma: no cover - depends on server grants/version
        result["variables"] = _safe_error(error)
    non_innodb = [
        item["table"]
        for item in table_scale.get("tables", [])
        if str(item.get("engine", "")).lower() != "innodb"
    ]
    result["non_innodb_target_tables"] = non_innodb
    result["status"] = (
        "compatible"
        if result["mysql_8_or_newer"]
        and result["current_alembic_revision"] == result["expected_alembic_revision"]
        and not non_innodb
        else "blocked"
    )
    return result


def collect_metadata_locks(connection: Connection) -> dict[str, Any]:
    if connection.dialect.name != "mysql":
        return {"status": "unsupported_dialect", "locks": []}
    statement = """
        SELECT object_name AS locked_object_name,
               lock_type AS observed_lock_type,
               lock_status AS observed_lock_status,
               COUNT(*) AS observed_lock_count
        FROM performance_schema.metadata_locks
        WHERE object_schema = DATABASE() AND object_name IN :table_names
        GROUP BY object_name, lock_type, lock_status
        ORDER BY object_name, lock_type, lock_status
    """
    assert_read_only_statement(statement)
    query = text(statement).bindparams(bindparam("table_names", expanding=True))
    try:
        rows = connection.execute(query, {"table_names": list(target_tables())}).mappings().all()
    except Exception as error:  # pragma: no cover - depends on performance_schema grants
        return _safe_error(error)
    locks = [
        {
            "table": str(row["locked_object_name"]),
            "lock_type": str(row["observed_lock_type"]),
            "lock_status": str(row["observed_lock_status"]),
            "count": int(row["observed_lock_count"]),
        }
        for row in rows
    ]
    pending = sum(item["count"] for item in locks if item["lock_status"].upper() == "PENDING")
    exclusive = sum(
        item["count"]
        for item in locks
        if item["lock_status"].upper() == "GRANTED" and "EXCLUSIVE" in item["lock_type"].upper()
    )
    return {
        "status": "blocked" if pending or exclusive else "clear_at_capture_time",
        "pending_count": pending,
        "granted_exclusive_count": exclusive,
        "locks": locks,
        "note": "point-in-time aggregate only; recheck immediately before every future Alembic apply",
    }


def collect_disk_headroom(connection: Connection, table_scale: Mapping[str, Any]) -> dict[str, Any]:
    affected_bytes = int(table_scale.get("affected_total_bytes", 0))
    conservative_required = max(affected_bytes * 2, 512 * 1024 * 1024)
    result: dict[str, Any] = {
        "affected_table_bytes": affected_bytes,
        "conservative_required_free_bytes": conservative_required,
        "basis": "two times affected table+index bytes, with a 512 MiB floor for online-DDL temporary space",
    }
    if connection.dialect.name != "mysql":
        return {**result, "status": "unsupported_dialect"}
    statement = "SELECT @@datadir AS mysql_data_directory"
    assert_read_only_statement(statement)
    try:
        data_directory = connection.execute(text(statement)).scalar_one()
        path = Path(str(data_directory))
        if not path.exists():
            return {**result, "status": "manual_verification_required", "source": "database_host_filesystem_not_local"}
        usage = shutil.disk_usage(path)
        result.update(
            {
                "status": "sufficient" if usage.free >= conservative_required else "blocked_insufficient",
                "source": "local_mysql_data_directory_filesystem",
                "filesystem_total_bytes": int(usage.total),
                "filesystem_used_bytes": int(usage.used),
                "filesystem_free_bytes": int(usage.free),
            }
        )
        return result
    except Exception as error:  # pragma: no cover - depends on variable grants/local topology
        return {**result, **_safe_error(error), "status": "manual_verification_required"}


def _quoted_identifier(connection: Connection, identifier: str) -> str:
    if not _IDENTIFIER.fullmatch(identifier):
        raise ValueError(f"unsafe SQL identifier: {identifier}")
    return connection.dialect.identifier_preparer.quote(identifier)


def _duplicate_count(
    connection: Connection,
    *,
    table: str,
    columns: Sequence[str],
) -> dict[str, int]:
    quoted_table = _quoted_identifier(connection, table)
    quoted_columns = [_quoted_identifier(connection, item) for item in columns]
    not_null = " AND ".join(f"{item} IS NOT NULL" for item in quoted_columns)
    grouped = ", ".join(quoted_columns)
    statement = f"""
        SELECT COUNT(*) AS duplicate_groups,
               COALESCE(SUM(group_size - 1), 0) AS duplicate_rows
        FROM (
            SELECT COUNT(*) AS group_size
            FROM {quoted_table}
            WHERE {not_null}
            GROUP BY {grouped}
            HAVING COUNT(*) > 1
        ) AS duplicate_keys
    """
    assert_read_only_statement(statement)
    row = connection.execute(text(statement)).mappings().one()
    return {
        "duplicate_groups": int(row["duplicate_groups"] or 0),
        "duplicate_rows": int(row["duplicate_rows"] or 0),
    }


def _legacy_outcome_duplicates(connection: Connection, inventory: SchemaInventory) -> dict[str, Any]:
    table = inventory.tables.get("recommendation_outcome_versions")
    required = {
        "recommendation_id",
        "calculation_version",
        "entry_policy_id",
        "horizon_days",
        "label_source",
        "result_json",
    }
    if connection.dialect.name != "mysql" or table is None or not required <= set(table.columns):
        return {"status": "not_applicable_missing_schema"}
    statement = """
        SELECT COUNT(*) AS duplicate_groups,
               COALESCE(SUM(group_size - 1), 0) AS duplicate_rows
        FROM (
            SELECT COUNT(*) AS group_size
            FROM recommendation_outcome_versions
            GROUP BY recommendation_id, calculation_version, entry_policy_id,
                     horizon_days, label_source, SHA2(result_json, 256)
            HAVING COUNT(*) > 1
        ) AS duplicate_versions
    """
    assert_read_only_statement(statement)
    row = connection.execute(text(statement)).mappings().one()
    return {
        "status": "duplicates_observed" if int(row["duplicate_groups"] or 0) else "clear",
        "duplicate_groups": int(row["duplicate_groups"] or 0),
        "duplicate_rows": int(row["duplicate_rows"] or 0),
        "action": "audit only; never auto-delete or rewrite legacy outcome versions",
    }


def collect_duplicate_checks(connection: Connection, inventory: SchemaInventory) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    for batch in MIGRATION_BATCHES:
        for check in batch.unique_key_checks:
            table = inventory.tables.get(check.table)
            missing = [] if table is None else [column for column in check.columns if column not in table.columns]
            if table is None or missing:
                checks.append(
                    {
                        "batch_id": batch.batch_id,
                        "check_id": check.check_id,
                        "table": check.table,
                        "columns": list(check.columns),
                        "status": "not_applicable_missing_schema",
                        "missing_columns": missing,
                    }
                )
                continue
            counts = _duplicate_count(connection, table=check.table, columns=check.columns)
            checks.append(
                {
                    "batch_id": batch.batch_id,
                    "check_id": check.check_id,
                    "table": check.table,
                    "columns": list(check.columns),
                    "status": "blocked_duplicates" if counts["duplicate_groups"] else "clear",
                    **counts,
                }
            )
    return {
        "planned_unique_keys": checks,
        "legacy_outcome_semantic_candidates": _legacy_outcome_duplicates(connection, inventory),
    }


@dataclass(frozen=True, slots=True)
class LegacyRunFacts:
    run_id: str
    run_mode: str | None
    evaluation_scope: str | None
    run_name: str | None
    trade_date: str | None
    started_at: datetime | None


def _date_value(value: str | date | datetime | None) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def classify_legacy_run(facts: LegacyRunFacts) -> tuple[str, str]:
    """Conservatively classify legacy rows; this plan never grants LIVE identity."""
    mode = (facts.run_mode or "").strip().lower()
    scope = (facts.evaluation_scope or "").strip().lower()
    name = (facts.run_name or "").strip().lower()
    if mode in {"backtest", "research", "research_backtest"}:
        return "research_backtest", "legacy_research_run_mode"
    if mode in {"experiment", "experimental"}:
        return "experiment", "legacy_experiment_run_mode"
    if scope in {"historical_replay", "historical_reconstruction"}:
        return "historical_reconstruction", "legacy_historical_scope"
    if "replay" in name or "重放" in name or "回放" in name:
        return "historical_reconstruction", "known_replay_source"
    started_date = _date_value(facts.started_at)
    signal_date = _date_value(facts.trade_date)
    if started_date is not None and signal_date is not None and started_date > signal_date:
        return "historical_reconstruction", "generated_after_signal_date"
    return "unverified", "insufficient_immutable_provenance"


def _table_count(connection: Connection, table: str) -> int:
    quoted_table = _quoted_identifier(connection, table)
    statement = f"SELECT COUNT(*) FROM {quoted_table}"
    assert_read_only_statement(statement)
    return int(connection.execute(text(statement)).scalar_one())


def _group_counts(connection: Connection, table: str, key: str) -> dict[str, int]:
    quoted_table = _quoted_identifier(connection, table)
    quoted_key = _quoted_identifier(connection, key)
    statement = f"SELECT {quoted_key} AS group_key, COUNT(*) AS row_count FROM {quoted_table} GROUP BY {quoted_key}"
    assert_read_only_statement(statement)
    return {
        str(row["group_key"]): int(row["row_count"])
        for row in connection.execute(text(statement)).mappings()
        if row["group_key"] is not None
    }


def collect_classification_plan(connection: Connection, inventory: SchemaInventory) -> dict[str, Any]:
    required_tables = {"recommendation_runs", "recommendations", "recommendation_outcomes"}
    if not required_tables <= set(inventory.tables):
        return {
            "status": "unavailable_missing_schema",
            "missing_tables": sorted(required_tables - set(inventory.tables)),
            "write_authorized": False,
            "write_operations_executed": 0,
        }
    statement = """
        SELECT run_id, run_mode, evaluation_scope, run_name, trade_date, started_at
        FROM recommendation_runs
        ORDER BY run_id
    """
    assert_read_only_statement(statement)
    rows = connection.execute(text(statement)).mappings().all()
    recommendation_counts = _group_counts(connection, "recommendations", "run_id")
    outcome_statement = """
        SELECT recommendations.run_id AS group_key, COUNT(*) AS row_count
        FROM recommendations
        INNER JOIN recommendation_outcomes
            ON recommendation_outcomes.recommendation_id = recommendations.recommendation_id
        GROUP BY recommendations.run_id
    """
    assert_read_only_statement(outcome_statement)
    outcome_counts = {
        str(row["group_key"]): int(row["row_count"])
        for row in connection.execute(text(outcome_statement)).mappings()
    }

    groups: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"run_count": 0, "recommendation_count": 0, "outcome_count": 0, "sample_run_ids": []}
    )
    assignments: list[dict[str, Any]] = []
    classified_recommendations = 0
    classified_outcomes = 0
    for row in rows:
        run_id = str(row["run_id"])
        evidence_class, reason = classify_legacy_run(
            LegacyRunFacts(
                run_id=run_id,
                run_mode=row["run_mode"],
                evaluation_scope=row["evaluation_scope"],
                run_name=row["run_name"],
                trade_date=row["trade_date"],
                started_at=row["started_at"],
            )
        )
        recommendation_count = recommendation_counts.get(run_id, 0)
        outcome_count = outcome_counts.get(run_id, 0)
        classified_recommendations += recommendation_count
        classified_outcomes += outcome_count
        group = groups[(evidence_class, reason)]
        group["run_count"] += 1
        group["recommendation_count"] += recommendation_count
        group["outcome_count"] += outcome_count
        if len(group["sample_run_ids"]) < 3:
            group["sample_run_ids"].append(run_id)
        assignments.append(
            {
                "run_id": run_id,
                "evidence_class": evidence_class,
                "reason": reason,
                "recommendation_count": recommendation_count,
                "outcome_count": outcome_count,
            }
        )

    total_recommendations = _table_count(connection, "recommendations")
    total_outcomes = _table_count(connection, "recommendation_outcomes")
    canonical = json.dumps(
        {"rule_version": CLASSIFICATION_RULE_VERSION, "assignments": assignments},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    projected_groups = [
        {
            "evidence_class": evidence_class,
            "classification_reason": reason,
            **value,
        }
        for (evidence_class, reason), value in sorted(groups.items())
    ]
    return {
        "status": "read_only_plan_generated",
        "rule_version": CLASSIFICATION_RULE_VERSION,
        "plan_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "write_authorized": False,
        "write_operations_executed": 0,
        "live_forward_assignments": 0,
        "groups": projected_groups,
        "totals": {
            "runs": len(rows),
            "recommendations_linked_to_known_runs": classified_recommendations,
            "outcomes_linked_to_known_runs": classified_outcomes,
            "recommendations": total_recommendations,
            "outcomes": total_outcomes,
            "orphan_recommendations": total_recommendations - classified_recommendations,
            "orphan_outcomes": total_outcomes - classified_outcomes,
        },
        "invariants": {
            "grouped_runs_match_total": sum(item["run_count"] for item in groups.values()) == len(rows),
            "no_legacy_row_promoted_to_live": all(item["evidence_class"] != "live_forward" for item in assignments),
            "source_rows_not_modified": True,
        },
        "next_step": "Task 1.4 must review this exact plan hash before any idempotent provenance backfill.",
    }


def _blocking_findings(
    compatibility: Mapping[str, Any],
    locks: Mapping[str, Any],
    disk: Mapping[str, Any],
    batch_evaluation: Sequence[Mapping[str, Any]],
    duplicate_checks: Mapping[str, Any],
) -> list[str]:
    blockers: list[str] = []
    if compatibility.get("status") != "compatible":
        blockers.append("mysql_or_alembic_compatibility")
    if locks.get("status") not in {"clear_at_capture_time"}:
        blockers.append("metadata_lock_state")
    if disk.get("status") != "sufficient":
        blockers.append("disk_headroom")
    if any(item["status_counts"][OperationStatus.BLOCKED.value] for item in batch_evaluation):
        blockers.append("schema_conflict")
    if any(
        item.get("status") == "blocked_duplicates"
        for item in duplicate_checks.get("planned_unique_keys", [])
    ):
        blockers.append("planned_unique_key_duplicates")
    return blockers


def collect_preflight(connection: Connection) -> dict[str, Any]:
    inventory = collect_schema_inventory(connection)
    table_scale = collect_table_scale(connection)
    compatibility = collect_compatibility(connection, inventory, table_scale)
    metadata_locks = collect_metadata_locks(connection)
    disk = collect_disk_headroom(connection, table_scale)
    batch_evaluation = evaluate_batches(inventory)
    duplicates = collect_duplicate_checks(connection, inventory)
    classification = collect_classification_plan(connection, inventory)
    blockers = _blocking_findings(
        compatibility,
        metadata_locks,
        disk,
        batch_evaluation,
        duplicates,
    )
    return {
        "schema_version": PREFLIGHT_SCHEMA_VERSION,
        "task": "0.3 设计并预检 forward-only migrations",
        "spec": "production-evidence-risk-hardening",
        "requirements": ["9.1", "9.2", "9.3"],
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "execution_semantics": {
            "database_access": "single read-only transaction rolled back on exit",
            "ddl_executed": False,
            "dml_executed": False,
            "classification_write_authorized": False,
            "migration_owner": "alembic",
            "future_down_migrations": "raise; application flags are the rollback mechanism",
        },
        "manifest": manifest_payload(),
        "preflight": {
            "compatibility": compatibility,
            "table_scale": table_scale,
            "metadata_locks": metadata_locks,
            "disk_headroom": disk,
            "schema_inventory": schema_inventory_report(inventory),
            "batch_evaluation": batch_evaluation,
            "duplicate_keys": duplicates,
            "classification_plan": classification,
        },
        "apply_gate": {
            "status": "plan_only_not_authorized",
            "blocking_findings": blockers,
            "required_before_each_future_apply": [
                "rerun this read-only preflight immediately before the reviewed Alembic revision",
                "require zero schema conflicts and zero planned unique-key duplicates",
                "require clear metadata locks and sufficient database-host disk headroom",
                "run upgrade twice and verify the second pass is a schema no-op",
                "reconcile run/recommendation/outcome totals before and after any classification backfill",
            ],
        },
    }
