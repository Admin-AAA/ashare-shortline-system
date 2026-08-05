"""Layer 1 tests for the task 0.8 migration preflight governance gates.

Every test uses a ``tmp_path`` governance store and fake preflight payloads:
no real MySQL connection and no write to the real governance store.
"""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.db.production_hardening_migration_plan import (
    EVIDENCE_BATCH,
    MIGRATION_BATCHES,
    manifest_payload,
    validate_manifest,
)
from app.db.production_hardening_preflight import assert_read_only_statement
from app.governance.envelope import GovernanceError
from app.governance.manifests import AdmissionVerdict, RESTORE_CAPACITY_KIND
from app.governance.migration_gates import (
    DEFAULT_MIGRATION_PLAN_KEY,
    REASON_FROZEN_AFTER_TRIGGER,
    REASON_MANIFEST_MISSING,
    RESTORE_CAPACITY_OBSERVATION_KIND,
    observation_readings_from_preflight,
    operation_impact_register,
    preflight_admission,
    record_restore_capacity_observation,
    restore_capacity_manifest_for,
)
from app.governance.store import GovernanceObjectStore


_NOW = datetime(2026, 8, 4, 2, 0, 0, tzinfo=timezone.utc)
_REV = "b" * 40
_PLAN_KEY = "migration-plan-test"


def _manifest_payload(*, plan_key: str, frozen_at: datetime) -> dict:
    return {
        "manifest_kind": RESTORE_CAPACITY_KIND,
        "stable_manifest_key": "restore-capacity-task-0.8",
        "registry_snapshot_id": "registry-snapshot-test",
        "frozen_at": frozen_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "applies_to_code_revision": _REV,
        "supersedes_manifest_id": None,
        "secret_handle_ids": [],
        "target_environment_key": "test-environment",
        "migration_plan_key": plan_key,
        "restore": {
            "restore_point_kind": "mysqldump_logical",
            "verification": "CHECKSUM_VERIFIED",
        },
        "capacity_rules": [
            {"metric": "DISK_FREE_BYTES", "comparator": ">=", "threshold": 536870912},
            {"metric": "METADATA_LOCK_WAIT_SECONDS", "comparator": "<=", "threshold": 0},
        ],
    }


def _freeze_manifest(store: GovernanceObjectStore, *, plan_key: str, frozen_at: datetime):
    return store.put(
        kind=RESTORE_CAPACITY_KIND,
        payload=_manifest_payload(plan_key=plan_key, frozen_at=frozen_at),
        created_at=frozen_at,
        created_by="test-actor",
        code_revision=_REV,
    )


def _fake_preflight_payload() -> dict:
    return {
        "manifest": manifest_payload(),
        "preflight": {
            "metadata_locks": {
                "status": "clear_at_capture_time",
                "pending_count": 0,
                "granted_exclusive_count": 0,
                "locks": [
                    {
                        "table": "recommendation_runs",
                        "lock_type": "SHARED_READ",
                        "lock_status": "GRANTED",
                        "count": 3,
                    },
                ],
            },
            "disk_headroom": {
                "status": "sufficient",
                "affected_table_bytes": 1024,
                "conservative_required_free_bytes": 536870912,
                "filesystem_free_bytes": 10 * 1024**3,
            },
            "table_scale": {
                "status": "observed",
                "affected_total_bytes": 1024,
                "tables": [
                    {
                        "table": "recommendation_runs",
                        "engine": "InnoDB",
                        "row_format": "Dynamic",
                        "estimated_rows": 42,
                        "data_bytes": 512,
                        "index_bytes": 512,
                        "allocated_free_bytes": 0,
                    },
                ],
            },
        },
    }


def _record_observation(store, manifest, *, captured_at=_NOW):
    payload = _fake_preflight_payload()
    return record_restore_capacity_observation(
        store,
        migration_plan_key=_PLAN_KEY,
        manifest_object_id=manifest.object_id,
        captured_at=captured_at,
        readings=observation_readings_from_preflight(payload),
        impact_register=operation_impact_register(payload),
        actor="tester",
        code_revision=_REV,
    )


class TestManifestLookup:
    def test_missing_manifest_blocks_without_store_writes(self, tmp_path):
        store = GovernanceObjectStore(tmp_path)
        verdict = preflight_admission(store, _PLAN_KEY, _NOW)
        assert verdict.verdict is AdmissionVerdict.BLOCKED
        assert verdict.reasons == (REASON_MANIFEST_MISSING,)
        assert verdict.manifest is None
        # Admission is read-only: nothing was appended to the store.
        assert list(tmp_path.rglob("*.json")) == []

    def test_manifest_for_other_plan_key_is_missing(self, tmp_path):
        store = GovernanceObjectStore(tmp_path)
        _freeze_manifest(store, plan_key="plan-a", frozen_at=_NOW - timedelta(hours=1))
        verdict = preflight_admission(store, "plan-b", _NOW)
        assert verdict.verdict is AdmissionVerdict.BLOCKED
        assert verdict.reasons == (REASON_MANIFEST_MISSING,)

    def test_invalid_manifest_payload_fails_closed(self, tmp_path):
        store = GovernanceObjectStore(tmp_path)
        payload = _manifest_payload(plan_key=_PLAN_KEY, frozen_at=_NOW - timedelta(hours=1))
        payload["capacity_rules"] = [
            {"metric": "NOT_A_METRIC", "comparator": ">=", "threshold": 1}
        ]
        store.put(
            kind=RESTORE_CAPACITY_KIND,
            payload=payload,
            created_at=_NOW - timedelta(hours=1),
            created_by="test-actor",
            code_revision=_REV,
        )
        verdict = preflight_admission(store, _PLAN_KEY, _NOW)
        assert verdict.verdict is AdmissionVerdict.BLOCKED
        assert verdict.reasons[0].startswith("restore_capacity_manifest_invalid:")

    def test_latest_frozen_manifest_wins(self, tmp_path):
        store = GovernanceObjectStore(tmp_path)
        older = _freeze_manifest(store, plan_key=_PLAN_KEY, frozen_at=_NOW - timedelta(hours=3))
        newer = _freeze_manifest(store, plan_key=_PLAN_KEY, frozen_at=_NOW - timedelta(hours=1))
        found = restore_capacity_manifest_for(store, _PLAN_KEY)
        assert found is not None
        assert found.object_id == newer.object_id != older.object_id


class TestPreflightAdmission:
    def test_manifest_frozen_at_or_after_trigger_blocks(self, tmp_path):
        store = GovernanceObjectStore(tmp_path)
        _freeze_manifest(store, plan_key=_PLAN_KEY, frozen_at=_NOW + timedelta(hours=1))
        later = preflight_admission(store, _PLAN_KEY, _NOW)
        assert later.verdict is AdmissionVerdict.BLOCKED
        assert later.reasons == (REASON_FROZEN_AFTER_TRIGGER,)

        store_equal = GovernanceObjectStore(tmp_path / "equal")
        _freeze_manifest(store_equal, plan_key=_PLAN_KEY, frozen_at=_NOW)
        equal = preflight_admission(store_equal, _PLAN_KEY, _NOW)
        assert equal.verdict is AdmissionVerdict.BLOCKED
        assert equal.reasons == (REASON_FROZEN_AFTER_TRIGGER,)

    def test_admitted_when_manifest_frozen_before_trigger(self, tmp_path):
        store = GovernanceObjectStore(tmp_path)
        manifest = _freeze_manifest(store, plan_key=_PLAN_KEY, frozen_at=_NOW - timedelta(hours=1))
        verdict = preflight_admission(store, _PLAN_KEY, _NOW)
        assert verdict.verdict is AdmissionVerdict.ADMITTED
        assert verdict.reasons == ()
        assert verdict.manifest is not None
        assert verdict.manifest.object_id == manifest.object_id
        found = restore_capacity_manifest_for(store, _PLAN_KEY)
        assert found is not None and found.object_id == manifest.object_id


class TestObservation:
    def test_observation_is_append_only_and_never_rewrites_manifest(self, tmp_path):
        store = GovernanceObjectStore(tmp_path)
        manifest = _freeze_manifest(store, plan_key=_PLAN_KEY, frozen_at=_NOW - timedelta(hours=2))
        manifest_before = store.get(manifest.object_id).to_document()

        first = _record_observation(store, manifest)
        assert first.kind == RESTORE_CAPACITY_OBSERVATION_KIND
        assert first.referenced_object_ids == (manifest.object_id,)
        assert first.payload["restore_capacity_manifest_id"] == manifest.object_id
        assert first.payload["threshold_writeback"] == "forbidden"

        # Identical re-capture is an idempotent no-op; a later capture appends
        # a brand-new object and the first stays resolvable.
        again = _record_observation(store, manifest)
        assert again.object_id == first.object_id
        second = _record_observation(
            store, manifest, captured_at=_NOW + timedelta(seconds=1)
        )
        assert second.object_id != first.object_id
        assert store.get(first.object_id).to_document() == first.to_document()
        assert len(list(store.iter_kind(RESTORE_CAPACITY_OBSERVATION_KIND))) == 2

        # The frozen manifest (including its capacity thresholds) is untouched.
        reloaded = store.get(manifest.object_id)
        assert reloaded.to_document() == manifest_before
        assert (
            reloaded.payload["capacity_rules"]
            == _manifest_payload(plan_key=_PLAN_KEY, frozen_at=_NOW - timedelta(hours=2))[
                "capacity_rules"
            ]
        )

    def test_observation_rejects_credential_shaped_values(self, tmp_path):
        store = GovernanceObjectStore(tmp_path)
        manifest = _freeze_manifest(store, plan_key=_PLAN_KEY, frozen_at=_NOW - timedelta(hours=1))
        with pytest.raises(GovernanceError) as caught:
            record_restore_capacity_observation(
                store,
                migration_plan_key=_PLAN_KEY,
                manifest_object_id=manifest.object_id,
                captured_at=_NOW,
                readings={"metadata_locks": {"status": "password=hunter2"}},
                actor="tester",
                code_revision=_REV,
            )
        assert caught.value.code == "secret_value_in_observation"
        assert list(store.iter_kind(RESTORE_CAPACITY_OBSERVATION_KIND)) == []

    def test_readings_and_register_use_allowlisted_fields(self, tmp_path):
        payload = _fake_preflight_payload()
        payload["preflight"]["metadata_locks"]["raw_debug"] = "must-not-propagate"
        readings = observation_readings_from_preflight(payload)
        assert "raw_debug" not in json.dumps(readings, ensure_ascii=False)
        assert readings["plan_hash"] == payload["manifest"]["manifest_checksum"]
        assert set(readings) == {"plan_hash", "metadata_locks", "disk_headroom", "table_scale"}

        register = operation_impact_register(payload)
        expected_operations = sum(len(batch.operations) for batch in MIGRATION_BATCHES)
        assert len(register["operations"]) == expected_operations
        assert {operation["kind"] for operation in register["operations"]} <= {
            "create_table",
            "add_column",
            "create_index",
        }
        entry = next(
            operation
            for operation in register["operations"]
            if operation["table"] == "recommendation_runs" and operation["kind"] == "add_column"
        )
        assert entry["estimated_rows"] == 42
        assert entry["affected_bytes"] == 1024
        assert entry["lock_pending_count"] == 0
        assert entry["precondition"] and entry["postcondition"]
        assert entry["batch_checksum"] and entry["plan_hash"] == register["plan_hash"]


class TestPlanContract:
    def test_plan_hash_is_stable(self):
        first = manifest_payload()
        second = manifest_payload()
        assert first["manifest_checksum"] == second["manifest_checksum"]
        register = operation_impact_register(_fake_preflight_payload())
        assert register["plan_hash"] == first["manifest_checksum"]
        assert all(
            operation["plan_hash"] == first["manifest_checksum"]
            for operation in register["operations"]
        )

    def test_operation_kinds_outside_additive_allowlist_rejected(self):
        tampered_operation = replace(EVIDENCE_BATCH.operations[0], kind="drop_table")
        tampered_batch = replace(EVIDENCE_BATCH, operations=(tampered_operation,))
        with pytest.raises(ValueError, match="unsupported operation kind"):
            validate_manifest((tampered_batch,))

    @pytest.mark.parametrize(
        "statement",
        [
            "DROP TABLE recommendation_runs",
            "DELETE FROM recommendation_runs",
            "ALTER TABLE recommendation_runs DROP COLUMN evidence_class",
            "TRUNCATE TABLE recommendation_outcomes",
            "UPDATE alembic_version SET version_num = '0002_legacy'",
        ],
    )
    def test_destructive_or_downgrade_statements_rejected(self, statement):
        with pytest.raises(ValueError, match="read-only"):
            assert_read_only_statement(statement)


class TestRunnerGate:
    def _argv(self, tmp_path, *extra):
        return [
            "preflight_production_hardening_migrations",
            "--governance-store",
            str(tmp_path),
            "--output",
            str(tmp_path / "out.json"),
            *extra,
        ]

    def test_runner_blocks_without_manifest_and_never_runs_preflight(
        self, tmp_path, monkeypatch, capsys
    ):
        from scripts import preflight_production_hardening_migrations as runner

        def _forbidden_call(output):  # pragma: no cover - must never run
            raise AssertionError("preflight must not run when admission is BLOCKED")

        monkeypatch.setattr(runner, "write_preflight", _forbidden_call)
        monkeypatch.setattr(sys, "argv", self._argv(tmp_path))

        exit_code = runner.main()

        assert exit_code != 0
        summary = json.loads(capsys.readouterr().out)
        assert summary["governance_gate"] == "BLOCKED"
        assert summary["reasons"] == [REASON_MANIFEST_MISSING]
        assert summary["db_connection"] == "not_attempted"
        assert summary["ddl_executed"] is False
        assert not (tmp_path / "out.json").exists()
        assert list(tmp_path.rglob("*.json")) == []

    def _run_admitted(self, tmp_path, monkeypatch, capsys, *extra):
        from scripts import preflight_production_hardening_migrations as runner

        store = GovernanceObjectStore(tmp_path)
        manifest = _freeze_manifest(
            store,
            plan_key=DEFAULT_MIGRATION_PLAN_KEY,
            frozen_at=_NOW - timedelta(hours=1),
        )
        fake_payload = _fake_preflight_payload()
        fake_payload["preflight"]["classification_plan"] = {
            "plan_hash": "c" * 64,
            "write_operations_executed": 0,
        }
        fake_payload["apply_gate"] = {
            "status": "plan_only_not_authorized",
            "blocking_findings": [],
        }
        monkeypatch.setattr(runner, "write_preflight", lambda output: fake_payload)
        monkeypatch.setattr(runner, "_current_code_revision", lambda: _REV)
        monkeypatch.setattr(sys, "argv", self._argv(tmp_path, *extra))
        exit_code = runner.main()
        summary = json.loads(capsys.readouterr().out)
        return store, manifest, exit_code, summary

    def test_runner_admitted_records_observation_only_when_requested(
        self, tmp_path, monkeypatch, capsys
    ):
        store, manifest, exit_code, summary = self._run_admitted(
            tmp_path, monkeypatch, capsys, "--record-observation"
        )
        assert exit_code == 0
        assert summary["governance_gate"] == "ADMITTED"
        assert summary["restore_capacity_manifest_id"] == manifest.object_id
        observations = list(store.iter_kind(RESTORE_CAPACITY_OBSERVATION_KIND))
        assert len(observations) == 1
        assert observations[0].object_id == summary["observation_object_id"]
        assert observations[0].payload["restore_capacity_manifest_id"] == manifest.object_id
        assert observations[0].payload["threshold_writeback"] == "forbidden"
        # Manifest bytes are untouched by the observation append.
        assert store.get(manifest.object_id).to_document() == manifest.to_document()

    def test_runner_default_is_pure_read_only_and_writes_no_observation(
        self, tmp_path, monkeypatch, capsys
    ):
        store, manifest, exit_code, summary = self._run_admitted(
            tmp_path, monkeypatch, capsys
        )
        assert exit_code == 0
        assert summary["observation_object_id"] is None
        assert list(store.iter_kind(RESTORE_CAPACITY_OBSERVATION_KIND)) == []
        stored_kinds = {path.parent.name for path in tmp_path.rglob("*.json")}
        assert stored_kinds == {RESTORE_CAPACITY_KIND}
        assert store.get(manifest.object_id).to_document() == manifest.to_document()
