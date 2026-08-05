"""Destructively clear only user-authorized local-demo recommendation state.

Market data, strategies, immutable research backtests, migrations, scheduler
state, and the content-addressed governance store are outside the mutation
allowlist. ``api_call_logs`` is scoped strictly to run IDs being deleted.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import bindparam, text

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


INITIAL_CLEANUP_OBJECT_ID = (
    "demo_canonical_only_cleanup:sha256:"
    "113d7c42ccac03bd25d7571bafe57b6955fc3fd725f0312821fa3ae270bc81ab"
)
SCOPED_AUDIT_OBJECT_ID = (
    "demo_canonical_only_cleanup:sha256:"
    "00357dfea4e50748e9a318d42b829947eedbf19f83682a06a37276fcae69d9f5"
)
from app.db.session import engine
from app.governance.store import GovernanceObjectStore

FULL_CLEAR_TABLES = (
    "recommendation_evidence_classification_audits",
    "recommendation_execution_states",
    "recommendation_outcome_versions",
    "recommendation_outcomes",
    "recommendations",
    "recommendation_runs",
    "llm_review_cache",
)
SCOPED_DELETE_POLICIES = {
    "api_call_logs": "run_id IN deleted recommendation_runs",
}
MUTATION_ALLOWLIST = {
    **{table: "all rows" for table in FULL_CLEAR_TABLES},
    **SCOPED_DELETE_POLICIES,
}
PRESERVE_TABLES = (
    "daily_kbars",
    "stock_quotes",
    "market_snapshots",
    "backtest_runs_v2",
    "backtest_input_artifacts",
    "backtest_trades",
    "alembic_version",
)


def _counts(connection, tables: tuple[str, ...]) -> dict[str, int]:
    return {
        table: int(connection.scalar(text(f"SELECT COUNT(*) FROM `{table}`")) or 0)
        for table in tables
    }


def _related_api_log_count(connection, run_ids: tuple[str, ...]) -> int:
    if not run_ids:
        return 0
    statement = text(
        "SELECT COUNT(*) FROM api_call_logs WHERE run_id IN :run_ids"
    ).bindparams(bindparam("run_ids", expanding=True))
    return int(connection.scalar(statement, {"run_ids": run_ids}) or 0)


def _delete_related_api_logs(connection, run_ids: tuple[str, ...]) -> None:
    if not run_ids:
        return
    statement = text(
        "DELETE FROM api_call_logs WHERE run_id IN :run_ids"
    ).bindparams(bindparam("run_ids", expanding=True))
    connection.execute(statement, {"run_ids": run_ids})


def main() -> None:
    store = GovernanceObjectStore()
    now = datetime.now(timezone.utc)
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=BACKEND_ROOT.parent,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()

    with engine.begin() as connection:
        existing = {row[0] for row in connection.execute(text("SHOW TABLES"))}
        required = set(MUTATION_ALLOWLIST)
        missing = sorted(required - existing)
        if missing:
            raise RuntimeError(f"cleanup table missing: {missing}")

        mutation_tables = tuple(MUTATION_ALLOWLIST)
        before = _counts(connection, mutation_tables)
        preserved_existing = tuple(
            table for table in PRESERVE_TABLES if table in existing
        )
        preserved_before = _counts(connection, preserved_existing)
        run_ids = tuple(
            str(row[0])
            for row in connection.execute(
                text("SELECT run_id FROM recommendation_runs")
            ).all()
        )
        scoped_before = _related_api_log_count(connection, run_ids)

        _delete_related_api_logs(connection, run_ids)
        scoped_after = _related_api_log_count(connection, run_ids)
        for table in FULL_CLEAR_TABLES:
            connection.execute(text(f"DELETE FROM `{table}`"))

        after = _counts(connection, mutation_tables)
        preserved_after = _counts(connection, preserved_existing)
        expected_api_log_count = before["api_call_logs"] - scoped_before
        invariant_failed = (
            any(after[table] for table in FULL_CLEAR_TABLES)
            or scoped_after != 0
            or after["api_call_logs"] != expected_api_log_count
            or preserved_before != preserved_after
        )
        if invariant_failed:
            raise RuntimeError(
                {
                    "after": after,
                    "expected_api_log_count": expected_api_log_count,
                    "scoped_after": scoped_after,
                    "preserved_before": preserved_before,
                    "preserved_after": preserved_after,
                }
            )

    payload = {
        "decision": "DEMO_CANONICAL_ONLY_CLEANUP",
        "mutation_allowlist": MUTATION_ALLOWLIST,
        "preserved_tables": PRESERVE_TABLES,
        "before": before,
        "after": after,
        "scoped_deletion_counts": {
            "api_call_logs": {"before": scoped_before, "after": scoped_after},
        },
        "preserved_before": preserved_before,
        "preserved_after": preserved_after,
        "destructive_demo_cleanup": True,
        "production_release_claim": False,
        "governance_evidence_preserved": True,
    }
    evidence = store.put(
        kind="demo_canonical_only_cleanup",
        payload=payload,
        created_at=now,
        created_by="user:2026-08-05-canonical-only",
        code_revision=revision,
        supersedes_id=INITIAL_CLEANUP_OBJECT_ID,
        referenced_object_ids=(SCOPED_AUDIT_OBJECT_ID,),
    )
    print(
        json.dumps(
            {
                "result": "ACCEPTED",
                "evidence_object_id": evidence.object_id,
                "evidence_uri": store.evidence_uri_for(evidence.object_id),
                "before": before,
                "after": after,
                "scoped_deletion_counts": payload["scoped_deletion_counts"],
                "preserved_after": preserved_after,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
