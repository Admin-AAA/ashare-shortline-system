"""Preregistered acceptance manifests and their admission validator.

The five manifest kinds — Shadow_Threshold, Restore_Capacity_Criteria,
Layer_2_Evidence, Real_Environment_Identity, Acceptance_Command — share one
immutable header and one admission pipeline.  A manifest must be frozen before
its trigger point (sample / preflight / acceptance); any drift in environment,
command, revision, URI, schema, hash, assertion, or attempt identity keeps the
verdict BLOCKED or records FAILED.  Nothing in this module can produce an
ACCEPTED environment verdict from Layer 1 inputs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Mapping

from app.governance.envelope import GovernanceError, ImmutableEnvelope
from app.governance.store import EVIDENCE_URI_SCHEME, GovernanceObjectStore


SHADOW_THRESHOLD_KIND = "shadow_threshold_manifest"
RESTORE_CAPACITY_KIND = "restore_capacity_criteria_manifest"
LAYER_2_EVIDENCE_KIND = "layer_2_evidence_manifest"
REAL_ENVIRONMENT_KIND = "real_environment_identity_manifest"
ACCEPTANCE_COMMAND_KIND = "acceptance_command_manifest"

PREREGISTERED_MANIFEST_KINDS: tuple[str, ...] = (
    SHADOW_THRESHOLD_KIND,
    RESTORE_CAPACITY_KIND,
    LAYER_2_EVIDENCE_KIND,
    REAL_ENVIRONMENT_KIND,
    ACCEPTANCE_COMMAND_KIND,
)

_SAFE_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+-]*$")
_SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+\-/]+=*"),
    re.compile(r"(?i)\b(?:mysql|postgres(?:ql)?|mariadb)\+?[^:]*://[^\s/:@]+:[^\s@]+@"),
    re.compile(r"(?i)\b(?:password|passwd|api[_-]?key|access[_-]?token)\s*[=:]\s*\S+"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
)


class ShadowFailureAction(StrEnum):
    BLOCK_CUTOVER = "BLOCK_CUTOVER"
    ROLLBACK_READ_PATH = "ROLLBACK_READ_PATH"
    ALERT_ONLY = "ALERT_ONLY"


class CapacityMetric(StrEnum):
    DISK_FREE_BYTES = "DISK_FREE_BYTES"
    DISK_FREE_RATIO = "DISK_FREE_RATIO"
    METADATA_LOCK_WAIT_SECONDS = "METADATA_LOCK_WAIT_SECONDS"
    ACTIVE_CONNECTION_RATIO = "ACTIVE_CONNECTION_RATIO"
    TABLE_ROWS_ESTIMATE = "TABLE_ROWS_ESTIMATE"
    REPLICATION_LAG_SECONDS = "REPLICATION_LAG_SECONDS"
    INNODB_BUFFER_FREE_RATIO = "INNODB_BUFFER_FREE_RATIO"


class Comparator(StrEnum):
    LT = "<"
    LTE = "<="
    EQ = "=="
    GTE = ">="
    GT = ">"


class AdmissionVerdict(StrEnum):
    ADMITTED = "ADMITTED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


def _aware(value: object, *, field_name: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise GovernanceError(
                "invalid_timestamp",
                f"{field_name} must be an ISO timestamp",
            ) from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise GovernanceError(
            "invalid_timestamp",
            f"{field_name} must be a timezone-aware datetime",
        )
    return value.astimezone(timezone.utc)


def _require_key(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not _SAFE_KEY_PATTERN.fullmatch(value):
        raise GovernanceError(
            "invalid_manifest_key",
            f"{field_name} must be a safe stable key",
        )
    return value


def _reject_secret_values(payload: Any, *, path: str = "") -> None:
    """Secret-handle-only serialization: raw secret values are refused."""

    if isinstance(payload, str):
        for pattern in _SECRET_VALUE_PATTERNS:
            if pattern.search(payload):
                raise GovernanceError(
                    "secret_value_in_manifest",
                    f"manifest field {path or '<root>'} carries a raw secret value",
                )
    elif isinstance(payload, Mapping):
        for key, item in payload.items():
            _reject_secret_values(item, path=f"{path}.{key}" if path else str(key))
    elif isinstance(payload, (list, tuple)):
        for index, item in enumerate(payload):
            _reject_secret_values(item, path=f"{path}[{index}]")


def validate_manifest_header(payload: Mapping[str, Any], *, expected_kind: str) -> None:
    """Validate the shared immutable header of any preregistered manifest."""

    if payload.get("manifest_kind") != expected_kind:
        raise GovernanceError(
            "manifest_kind_mismatch",
            f"manifest_kind must be {expected_kind}",
        )
    _require_key(payload.get("stable_manifest_key"), field_name="stable_manifest_key")
    _require_key(payload.get("registry_snapshot_id"), field_name="registry_snapshot_id")
    _aware(payload.get("frozen_at"), field_name="frozen_at")
    code_revision = payload.get("applies_to_code_revision")
    if not isinstance(code_revision, str) or not code_revision.strip():
        raise GovernanceError(
            "invalid_code_revision",
            "applies_to_code_revision must be non-empty",
        )
    supersedes = payload.get("supersedes_manifest_id")
    if supersedes is not None and (not isinstance(supersedes, str) or ":sha256:" not in supersedes):
        raise GovernanceError(
            "invalid_supersedes",
            "supersedes_manifest_id must reference a stored manifest object",
        )
    handles = payload.get("secret_handle_ids") or []
    if not isinstance(handles, list):
        raise GovernanceError("invalid_secret_handles", "secret_handle_ids must be a list")
    for handle in handles:
        _require_key(handle, field_name="secret_handle_ids[]")
    _reject_secret_values(payload)


def _require_list(payload: Mapping[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list) or not value:
        raise GovernanceError(
            "invalid_manifest_payload",
            f"{key} must be a non-empty list",
        )
    return value


def _validate_comparator(value: object, *, field_name: str) -> None:
    try:
        Comparator(value)
    except ValueError as exc:
        raise GovernanceError(
            "invalid_comparator",
            f"{field_name} must be one of {[item.value for item in Comparator]}",
        ) from exc


def validate_shadow_threshold_payload(payload: Mapping[str, Any]) -> None:
    validate_manifest_header(payload, expected_kind=SHADOW_THRESHOLD_KIND)
    _require_key(payload.get("target_operation_id"), field_name="target_operation_id")
    _require_key(payload.get("source_projection_id"), field_name="source_projection_id")
    _require_key(payload.get("candidate_projection_id"), field_name="candidate_projection_id")
    for rule in _require_list(payload, "rules"):
        if not isinstance(rule, Mapping):
            raise GovernanceError("invalid_shadow_rule", "each rule must be an object")
        _require_key(rule.get("metric_id"), field_name="rules[].metric_id")
        _validate_comparator(rule.get("comparator"), field_name="rules[].comparator")
        threshold = rule.get("threshold_decimal")
        if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
            raise GovernanceError("invalid_shadow_rule", "threshold_decimal must be numeric")
        min_sample = rule.get("minimum_sample")
        if not isinstance(min_sample, int) or isinstance(min_sample, bool) or min_sample < 1:
            raise GovernanceError("invalid_shadow_rule", "minimum_sample must be a positive integer")
        try:
            ShadowFailureAction(rule.get("failure_action"))
        except ValueError as exc:
            raise GovernanceError(
                "invalid_shadow_rule",
                "failure_action must be BLOCK_CUTOVER/ROLLBACK_READ_PATH/ALERT_ONLY",
            ) from exc
        dimensions = rule.get("identity_dimensions")
        if not isinstance(dimensions, list):
            raise GovernanceError("invalid_shadow_rule", "identity_dimensions must be a list")


def validate_restore_capacity_payload(payload: Mapping[str, Any]) -> None:
    validate_manifest_header(payload, expected_kind=RESTORE_CAPACITY_KIND)
    _require_key(payload.get("target_environment_key"), field_name="target_environment_key")
    _require_key(payload.get("migration_plan_key"), field_name="migration_plan_key")
    restore = payload.get("restore")
    if not isinstance(restore, Mapping):
        raise GovernanceError("invalid_restore_criteria", "restore must be an object")
    _require_key(restore.get("restore_point_kind"), field_name="restore.restore_point_kind")
    verification = restore.get("verification")
    if verification not in ("REHEARSAL_PASSED", "CHECKSUM_VERIFIED", "SNAPSHOT_CONFIRMED"):
        raise GovernanceError(
            "invalid_restore_criteria",
            "restore.verification must be REHEARSAL_PASSED/CHECKSUM_VERIFIED/SNAPSHOT_CONFIRMED",
        )
    for rule in _require_list(payload, "capacity_rules"):
        if not isinstance(rule, Mapping):
            raise GovernanceError("invalid_capacity_rule", "each rule must be an object")
        try:
            CapacityMetric(rule.get("metric"))
        except ValueError as exc:
            raise GovernanceError(
                "invalid_capacity_rule",
                f"metric must be one of {[item.value for item in CapacityMetric]}",
            ) from exc
        _validate_comparator(rule.get("comparator"), field_name="capacity_rules[].comparator")
        threshold = rule.get("threshold")
        if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
            raise GovernanceError("invalid_capacity_rule", "threshold must be numeric")


def validate_real_environment_payload(payload: Mapping[str, Any]) -> None:
    validate_manifest_header(payload, expected_kind=REAL_ENVIRONMENT_KIND)
    _require_key(payload.get("environment_id"), field_name="environment_id")
    mysql = payload.get("mysql")
    if not isinstance(mysql, Mapping):
        raise GovernanceError("invalid_environment_manifest", "mysql must be an object")
    for field_name in ("server_uuid_hash", "endpoint_alias", "database_name", "engine_version"):
        _require_key(mysql.get(field_name), field_name=f"mysql.{field_name}")
    port = mysql.get("port")
    if not isinstance(port, int) or isinstance(port, bool) or not (0 < port < 65536):
        raise GovernanceError("invalid_environment_manifest", "mysql.port must be a valid TCP port")
    prefect = payload.get("prefect")
    if not isinstance(prefect, Mapping):
        raise GovernanceError("invalid_environment_manifest", "prefect must be an object")
    for field_name in ("api_identity_hash", "server_version", "metadata_database_hash"):
        _require_key(prefect.get(field_name), field_name=f"prefect.{field_name}")
    for field_name in ("work_pool", "work_queue"):
        _require_key(prefect.get(field_name), field_name=f"prefect.{field_name}")
    targets = payload.get("sanitized_network_target_ids")
    if not isinstance(targets, list):
        raise GovernanceError(
            "invalid_environment_manifest",
            "sanitized_network_target_ids must be a list",
        )


def validate_acceptance_command_payload(payload: Mapping[str, Any]) -> None:
    validate_manifest_header(payload, expected_kind=ACCEPTANCE_COMMAND_KIND)
    for command in _require_list(payload, "commands"):
        if not isinstance(command, Mapping):
            raise GovernanceError("invalid_acceptance_command", "each command must be an object")
        _require_key(command.get("command_id"), field_name="commands[].command_id")
        _require_key(command.get("executable_entrypoint"), field_name="commands[].executable_entrypoint")
        argv = command.get("argument_vector")
        if not isinstance(argv, list) or any(not isinstance(item, str) for item in argv):
            raise GovernanceError(
                "invalid_acceptance_command",
                "argument_vector must be a tokenized list of strings (shell strings forbidden)",
            )
        exit_codes = command.get("expected_exit_codes")
        if not isinstance(exit_codes, list) or any(
            not isinstance(code, int) or isinstance(code, bool) for code in exit_codes
        ):
            raise GovernanceError(
                "invalid_acceptance_command",
                "expected_exit_codes must be integers",
            )
        timeout = command.get("timeout_seconds")
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise GovernanceError("invalid_acceptance_command", "timeout_seconds must be positive")
        _require_key(command.get("code_checksum"), field_name="commands[].code_checksum")
        assertions = command.get("assertion_ids")
        if not isinstance(assertions, list):
            raise GovernanceError("invalid_acceptance_command", "assertion_ids must be a list")


def validate_layer_2_evidence_payload(payload: Mapping[str, Any]) -> None:
    validate_manifest_header(payload, expected_kind=LAYER_2_EVIDENCE_KIND)
    _require_key(payload.get("layer_2_evidence_set_id"), field_name="layer_2_evidence_set_id")
    _require_key(payload.get("real_environment_key"), field_name="real_environment_key")
    command_ids = payload.get("acceptance_command_ids")
    if not isinstance(command_ids, list) or not command_ids:
        raise GovernanceError("invalid_layer_2_manifest", "acceptance_command_ids must be non-empty")
    for command_id in command_ids:
        _require_key(command_id, field_name="acceptance_command_ids[]")
    for contract in _require_list(payload, "artifact_contracts"):
        if not isinstance(contract, Mapping):
            raise GovernanceError("invalid_artifact_contract", "each contract must be an object")
        _require_key(contract.get("contract_id"), field_name="artifact_contracts[].contract_id")
        _require_key(contract.get("schema_uri"), field_name="artifact_contracts[].schema_uri")
        uri_allowlist = contract.get("evidence_uri_allowlist")
        if not isinstance(uri_allowlist, list) or not uri_allowlist:
            raise GovernanceError(
                "invalid_artifact_contract",
                "evidence_uri_allowlist must name immutable URI prefixes",
            )
        for prefix in uri_allowlist:
            if not isinstance(prefix, str) or not prefix.startswith(EVIDENCE_URI_SCHEME):
                raise GovernanceError(
                    "invalid_artifact_contract",
                    "evidence_uri_allowlist entries must start with evidence://sha256/",
                )
        required_fields = contract.get("required_fields")
        if not isinstance(required_fields, list) or not required_fields:
            raise GovernanceError(
                "invalid_artifact_contract",
                "required_fields must be a non-empty list",
            )
    for assertion in _require_list(payload, "assertions"):
        if not isinstance(assertion, Mapping):
            raise GovernanceError("invalid_assertion", "each assertion must be an object")
        _require_key(assertion.get("assertion_id"), field_name="assertions[].assertion_id")
        if assertion.get("required") is not True:
            raise GovernanceError(
                "invalid_assertion",
                "every registered assertion must be required (pass_rule=ALL_REQUIRED_ASSERTIONS_PASS)",
            )
        _require_key(assertion.get("predicate"), field_name="assertions[].predicate")
    if payload.get("pass_rule") != "ALL_REQUIRED_ASSERTIONS_PASS":
        raise GovernanceError(
            "invalid_pass_rule",
            "pass_rule must be ALL_REQUIRED_ASSERTIONS_PASS",
        )


_VALIDATORS = {
    SHADOW_THRESHOLD_KIND: validate_shadow_threshold_payload,
    RESTORE_CAPACITY_KIND: validate_restore_capacity_payload,
    LAYER_2_EVIDENCE_KIND: validate_layer_2_evidence_payload,
    REAL_ENVIRONMENT_KIND: validate_real_environment_payload,
    ACCEPTANCE_COMMAND_KIND: validate_acceptance_command_payload,
}


def validate_manifest_payload(kind: str, payload: Mapping[str, Any]) -> None:
    validator = _VALIDATORS.get(kind)
    if validator is None:
        raise GovernanceError(
            "unknown_manifest_kind",
            f"manifest kind {kind!r} is not one of the five preregistered kinds",
        )
    validator(payload)


@dataclass(frozen=True, slots=True)
class Layer2EvidenceClaim:
    """Identity-only claim for one artifact bound by a Layer 2 manifest.

    The bytes remain in the immutable governance store.  This projection binds
    those bytes to the preregistered contract, environment, revision, command,
    attempt, and complete required-assertion result set.
    """

    contract_id: str
    evidence_uri: str
    schema_uri: str
    content_sha256: str
    environment_key: str
    code_revision: str
    command_id: str
    attempt_identity: str
    assertion_results: tuple[tuple[str, bool], ...]


@dataclass(frozen=True, slots=True)
class AdmissionContext:
    """The facts an acceptance attempt claims; each must match the manifest."""

    trigger_point_at: datetime
    applies_to_code_revision: str
    environment_key: str | None = None
    command_id: str | None = None
    command_argv_sha256: str | None = None
    attempt_identity: str | None = None
    evidence_uris: tuple[str, ...] = field(default_factory=tuple)
    artifact_claims: tuple[Layer2EvidenceClaim, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class AdmissionResult:
    verdict: AdmissionVerdict
    mismatches: tuple[str, ...] = field(default_factory=tuple)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "mismatches": list(self.mismatches),
            "verdict": self.verdict.value,
        }


def admit_acceptance_attempt(
    *,
    manifest: ImmutableEnvelope,
    context: AdmissionContext,
    store: GovernanceObjectStore,
    pre_execution: bool,
) -> AdmissionResult:
    """Run the manifest admission pipeline for one acceptance attempt.

    ``pre_execution=True`` keeps drift at BLOCKED (nothing ran yet); after
    execution the same drift records FAILED.  No path here returns ACCEPTED —
    acceptance itself is recorded separately only after Layer 2 assertions
    pass against immutable evidence.
    """

    validate_manifest_payload(manifest.kind, manifest.payload)
    mismatches: list[str] = []

    frozen_at = _aware(manifest.payload.get("frozen_at"), field_name="frozen_at")
    if frozen_at >= _aware(context.trigger_point_at, field_name="trigger_point_at"):
        mismatches.append("manifest_frozen_after_trigger_point")

    if manifest.payload.get("applies_to_code_revision") != context.applies_to_code_revision:
        mismatches.append("code_revision_mismatch")

    if manifest.kind == LAYER_2_EVIDENCE_KIND:
        if context.environment_key is not None and (
            manifest.payload.get("real_environment_key") != context.environment_key
        ):
            mismatches.append("environment_key_mismatch")
        command_ids = set(manifest.payload.get("acceptance_command_ids") or [])
        if context.command_id is not None and context.command_id not in command_ids:
            mismatches.append("command_not_registered")

    resolved_by_uri: dict[str, ImmutableEnvelope] = {}
    for uri in context.evidence_uris:
        if not isinstance(uri, str) or not uri.startswith(EVIDENCE_URI_SCHEME):
            mismatches.append(f"mutable_evidence_uri:{uri!r}")
            continue
        try:
            resolved_by_uri[uri] = store.resolve_evidence_uri(uri)
        except GovernanceError as exc:
            mismatches.append(f"evidence_uri_rejected:{exc.code}")

    if manifest.kind == LAYER_2_EVIDENCE_KIND and context.artifact_claims:
        contracts = {
            str(contract["contract_id"]): contract
            for contract in manifest.payload.get("artifact_contracts") or ()
        }
        claims_by_contract: dict[str, Layer2EvidenceClaim] = {}
        for claim in context.artifact_claims:
            if claim.contract_id in claims_by_contract:
                mismatches.append(f"duplicate_artifact_contract:{claim.contract_id}")
            claims_by_contract[claim.contract_id] = claim
        for contract_id in sorted(set(contracts) - set(claims_by_contract)):
            mismatches.append(f"artifact_contract_missing:{contract_id}")
        for contract_id in sorted(set(claims_by_contract) - set(contracts)):
            mismatches.append(f"artifact_contract_unregistered:{contract_id}")

        required_assertions = {
            str(assertion["assertion_id"])
            for assertion in manifest.payload.get("assertions") or ()
            if assertion.get("required") is True
        }
        command_ids = set(manifest.payload.get("acceptance_command_ids") or ())
        for contract_id in sorted(set(contracts) & set(claims_by_contract)):
            contract = contracts[contract_id]
            claim = claims_by_contract[contract_id]
            try:
                evidence = resolved_by_uri.get(claim.evidence_uri)
                if evidence is None:
                    evidence = store.resolve_evidence_uri(claim.evidence_uri)
                    resolved_by_uri[claim.evidence_uri] = evidence
            except GovernanceError as exc:
                mismatches.append(f"artifact_evidence_uri_rejected:{contract_id}:{exc.code}")
                continue
            if claim.schema_uri != contract.get("schema_uri"):
                mismatches.append(f"artifact_schema_mismatch:{contract_id}")
            if claim.content_sha256 != evidence.payload_sha256:
                mismatches.append(f"artifact_content_hash_mismatch:{contract_id}")
            missing_fields = sorted(set(contract.get("required_fields") or ()) - set(evidence.payload))
            if missing_fields:
                mismatches.append(f"artifact_required_fields_missing:{contract_id}")
            if claim.environment_key != manifest.payload.get("real_environment_key"):
                mismatches.append(f"artifact_environment_mismatch:{contract_id}")
            if claim.code_revision != manifest.payload.get("applies_to_code_revision"):
                mismatches.append(f"artifact_code_revision_mismatch:{contract_id}")
            if claim.command_id not in command_ids or claim.command_id != context.command_id:
                mismatches.append(f"artifact_command_mismatch:{contract_id}")
            if not claim.attempt_identity or claim.attempt_identity != context.attempt_identity:
                mismatches.append(f"artifact_attempt_mismatch:{contract_id}")
            assertion_results = dict(claim.assertion_results)
            if (
                len(assertion_results) != len(claim.assertion_results)
                or set(assertion_results) != required_assertions
                or not all(assertion_results.values())
            ):
                mismatches.append(f"artifact_assertions_mismatch:{contract_id}")

    verdict = (
        AdmissionVerdict.ADMITTED
        if not mismatches
        else (AdmissionVerdict.BLOCKED if pre_execution else AdmissionVerdict.FAILED)
    )
    return AdmissionResult(verdict=verdict, mismatches=tuple(mismatches))


__all__ = [
    "ACCEPTANCE_COMMAND_KIND",
    "AdmissionContext",
    "AdmissionResult",
    "AdmissionVerdict",
    "CapacityMetric",
    "Comparator",
    "LAYER_2_EVIDENCE_KIND",
    "Layer2EvidenceClaim",
    "PREREGISTERED_MANIFEST_KINDS",
    "REAL_ENVIRONMENT_KIND",
    "RESTORE_CAPACITY_KIND",
    "SHADOW_THRESHOLD_KIND",
    "ShadowFailureAction",
    "admit_acceptance_attempt",
    "validate_manifest_header",
    "validate_manifest_payload",
]
