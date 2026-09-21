"""Immutable, versioned contracts shared by every recommendation-run entry point.

This module intentionally contains no ORM, queue, HTTP, or vendor dependency.  An API
handler, scheduler, CLI command, and a worker must all pass through
:func:`parse_run_request` before a request is persisted or executed.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from types import MappingProxyType
from typing import Any, ClassVar, Mapping, Protocol, TypeAlias, runtime_checkable

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    ValidationError,
    field_validator,
    model_validator,
)

from app.core.deployment_identity import (
    resolve_application_revision,
    resolve_application_version,
)


class RunContractError(ValueError):
    """A stable, caller-safe error raised for run-contract violations."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class RunMode(str, Enum):
    FORMAL = "formal"
    EXPERIMENT = "experiment"
    BACKTEST = "backtest"


class DataSourceId(str, Enum):
    AUTO = "auto"
    LOCAL_POOL = "local_pool"
    LOCAL_POOL_PREFERRED = "local_pool_preferred"
    LOCAL_POOL_REALTIME = "local_pool_realtime"
    REALTIME = "realtime"


RUN_REQUEST_SCHEMA_VERSION = 1

# formal 模式 score_line 硬地板（默认 72）。
# 实证（2026-07-21 T3）：matrix/手动曾把 score_line=55 写入 formal run
# （preset_formal_opening 07-16 14 票 WR 仅 39% 拖累近 10 日净盈亏比），
# experiment 可继续用更低线喂样本，formal 不允许低于此值污染 D 项口径。
FORMAL_SCORE_LINE_FLOOR = 72.0


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"


class RunPhase(str, Enum):
    PREPARE = "prepare"
    MARKET_DATA = "market_data"
    AUXILIARY_DATA = "auxiliary_data"
    FEATURES = "features"
    STRATEGY_EVALUATION = "strategy_evaluation"
    REVIEW = "review"
    AGGREGATION = "aggregation"
    PERSISTENCE = "persistence"
    COMPLETED = "completed"


RUN_PHASE_ORDER: tuple[RunPhase, ...] = (
    RunPhase.PREPARE,
    RunPhase.MARKET_DATA,
    RunPhase.AUXILIARY_DATA,
    RunPhase.FEATURES,
    RunPhase.STRATEGY_EVALUATION,
    RunPhase.REVIEW,
    RunPhase.AGGREGATION,
    RunPhase.PERSISTENCE,
)


@dataclass(frozen=True, slots=True)
class RunStageResult:
    """Stable result emitted after a stage has completed its own side effect."""

    phase: RunPhase
    side_effect_key: str

    def __post_init__(self) -> None:
        if self.phase not in RUN_PHASE_ORDER:
            raise ValueError("stage result must name an executable checkpoint phase")
        if not self.side_effect_key.strip():
            raise ValueError("stage result requires a side_effect_key")


class RunActor(str, Enum):
    API = "api"
    DISPATCHER = "dispatcher"
    WORKER = "worker"
    WATCHDOG = "watchdog"
    OPERATOR = "operator"


class RunTrigger(str, Enum):
    START = "start"
    SPAWN_FAILED = "spawn_failed"
    STAGE_FAILED = "stage_failed"
    COMPLETE = "complete"
    HEARTBEAT_TIMEOUT = "heartbeat_timeout"
    CANCEL = "cancel"
    RESUME = "resume"


class RunErrorCode(str, Enum):
    INVALID_REQUEST_SCHEMA = "invalid_request_schema"
    UNSUPPORTED_REQUEST_SCHEMA = "unsupported_request_schema"
    CREDENTIAL_IN_REQUEST_FORBIDDEN = "credential_in_request_forbidden"
    INVALID_STATUS_TRANSITION = "invalid_status_transition"
    TRANSITION_ACTOR_NOT_ALLOWED = "transition_actor_not_allowed"
    RESUME_REQUIRES_VALID_CHECKPOINT = "resume_requires_valid_checkpoint"
    RESUME_REQUIRES_INTERRUPTED_STATUS = "resume_requires_interrupted_status"
    RESUME_REQUIRES_CANONICAL_REQUEST = "resume_requires_canonical_request"
    RESUME_REQUIRES_PERSISTENCE_CHECKPOINT = "resume_requires_persistence_checkpoint"
    RESUME_REQUIRES_VERIFIED_SEAL = "resume_requires_verified_seal"
    RESUME_SEAL_NOT_FOUND = "resume_seal_not_found"
    RESUME_SEAL_SCHEMA_UNSUPPORTED = "resume_seal_schema_unsupported"
    RESUME_SEAL_KEY_MISMATCH = "resume_seal_key_mismatch"
    RESUME_SEAL_ROW_COUNT_MISMATCH = "resume_seal_row_count_mismatch"
    RESUME_SEAL_DIGEST_MISMATCH = "resume_seal_digest_mismatch"
    RESUME_REQUIRES_NO_ACTIVE_LEASE = "resume_requires_no_active_lease"
    COMPLETION_REQUIRES_PERSISTENCE_CHECKPOINT = "completion_requires_persistence_checkpoint"
    CHECKPOINT_OUT_OF_ORDER = "checkpoint_out_of_order"
    CHECKPOINT_ALREADY_COMMITTED = "checkpoint_already_committed"
    WORKER_SPAWN_FAILED = "worker_spawn_failed"
    STAGE_EXECUTION_FAILED = "stage_execution_failed"
    HEARTBEAT_TIMEOUT = "heartbeat_timeout"
    RUN_CANCELLED = "run_cancelled"
    RUN_NOT_FOUND = "run_not_found"
    DUPLICATE_RUN_ID = "duplicate_run_id"
    COMPARE_AND_SET_CONFLICT = "compare_and_set_conflict"
    LEASE_LOST = "lease_lost"


@dataclass(frozen=True, slots=True)
class RunTransition:
    """A state-machine-approved mutation for a repository compare-and-set call.

    The metadata is deliberately carried with the transition instead of inferred by
    the repository from a status string.  This keeps actor, terminal cleanup and
    worker-lease requirements identical for every adapter.
    """

    expected_status: RunStatus
    target_status: RunStatus
    trigger: RunTrigger
    actor: RunActor
    target_phase: RunPhase | None = None
    stable_error_code: str | None = None
    error_message: str | None = None
    terminal: bool = False
    requires_active_lease: bool = False
    recovery_condition: str | None = None


class SkillSelection(BaseModel):
    """An immutable selected strategy and its request-level weight."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    skill_id: str = Field(min_length=1, max_length=160)
    weight: StrictFloat = Field(default=1.0, gt=0, le=100)
    sort_order: StrictInt = Field(default=0, ge=0, le=100_000)

    @field_validator("skill_id")
    @classmethod
    def _require_nonblank_skill_id(cls, value: str) -> str:
        if not value:
            raise ValueError("skill_id 不能为空")
        return value


class RunRequest(BaseModel):
    """The sole durable input for a recommendation-pipeline run.

    ``frozen=True`` makes request identity safe across API, queue and subprocess
    boundaries.  ``to_canonical_json`` emits every default and sorts keys, so it is
    suitable for persistence, hashing and equality checks.  Secrets are rejected
    before model validation by :func:`parse_run_request`.
    """

    CURRENT_SCHEMA_VERSION: ClassVar[int] = RUN_REQUEST_SCHEMA_VERSION

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
        allow_inf_nan=False,
    )

    schema_version: StrictInt = Field(default=CURRENT_SCHEMA_VERSION)
    run_mode: RunMode = RunMode.FORMAL
    run_name: str = Field(default="临时运行", min_length=1, max_length=120)
    data_source: DataSourceId = DataSourceId.LOCAL_POOL_REALTIME
    preset_id: str | None = Field(default=None, max_length=80)
    skills: tuple[SkillSelection, ...] = ()
    score_line: StrictFloat = Field(default=72.0, ge=0, le=100)
    risk_template: str = Field(default="balanced", min_length=1, max_length=40)
    output_count: StrictInt = Field(default=6, ge=1, le=1_000)
    skip_aux_refresh: StrictBool = False
    disable_agent_review: StrictBool = False
    trade_date_override: str | None = Field(default=None, max_length=10)
    market_scope: str = Field(default="all", min_length=1, max_length=30)
    market_scope_value: str | None = Field(default=None, max_length=120)
    agent_review_limit: StrictInt = Field(default=0, ge=0, le=100_000)
    agent_review_concurrency: StrictInt = Field(default=0, ge=0, le=100)
    hide_recent_repeats: StrictBool = True
    dedup_same_day: StrictBool = True
    matrix_batch_id: str | None = Field(default=None, max_length=120)
    matrix_batch_name: str | None = Field(default=None, max_length=120)
    matrix_run: StrictBool = False

    @field_validator(
        "run_name",
        "preset_id",
        "risk_template",
        "market_scope",
        "market_scope_value",
        "matrix_batch_id",
        "matrix_batch_name",
    )
    @classmethod
    def _strip_and_reject_blank_optional_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value:
            raise ValueError("字符串字段不能为空白")
        return value

    @field_validator("trade_date_override")
    @classmethod
    def _validate_trade_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError as exc:
            raise ValueError("trade_date_override 必须是 YYYY-MM-DD") from exc

    @field_validator("schema_version")
    @classmethod
    def _require_current_schema_version(cls, value: int) -> int:
        if value != cls.CURRENT_SCHEMA_VERSION:
            raise RunContractError(
                RunErrorCode.INVALID_REQUEST_SCHEMA.value,
                f"RunRequest 仅接受 schema_version={cls.CURRENT_SCHEMA_VERSION}",
            )
        return value

    @model_validator(mode="after")
    def _normalise_and_validate_skills(self) -> "RunRequest":
        skill_ids = [item.skill_id for item in self.skills]
        if len(skill_ids) != len(set(skill_ids)):
            raise ValueError("skills 不能包含重复的 skill_id")
        ordered = tuple(sorted(self.skills, key=lambda item: (item.sort_order, item.skill_id)))
        updates: dict[str, Any] = {}
        if ordered != self.skills:
            updates["skills"] = ordered
        # formal 硬地板：低于 FORMAL_SCORE_LINE_FLOOR 一律抬到地板（不 raise，避免
        # matrix/旧前端 payload 整批失败；experiment/backtest 不受限）。
        if (
            self.run_mode is RunMode.FORMAL
            and float(self.score_line) < FORMAL_SCORE_LINE_FLOOR
        ):
            updates["score_line"] = float(FORMAL_SCORE_LINE_FLOOR)
        if not updates:
            return self
        return self.model_copy(update=updates)

    @classmethod
    def from_json(cls, raw: str | bytes | bytearray) -> "RunRequest":
        """Parse, upgrade and validate a durable request JSON document."""
        return parse_run_request(raw)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | "RunRequest") -> "RunRequest":
        """Parse API/scheduler/CLI payloads through the shared validation path."""
        return parse_run_request(payload)

    def to_canonical_json(self) -> str:
        """Return deterministic JSON suitable for ``request_json`` persistence."""
        payload = self.model_dump(mode="json", exclude_none=False)
        # Keep this invariant at the serialization boundary as well as at parse time.
        # It protects callers that constructed a model through a low-level Pydantic
        # escape hatch (for example ``model_construct``).
        _assert_no_credentials(payload)
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    def to_persistence_fields(self) -> dict[str, Any]:
        """Return the three request columns expected by a repository adapter."""
        return {
            "request_schema_version": self.schema_version,
            "request_json": self.to_canonical_json(),
            "run_mode": self.run_mode.value,
        }


EXECUTION_VERSION_SCHEMA_VERSION = 1
_EXECUTION_VERSION_PREFIX = f"execution-v{EXECUTION_VERSION_SCHEMA_VERSION}"
_SENSITIVE_VALUE_MARKERS = frozenset(
    {
        "credential",
        "password",
        "passwd",
        "secret",
        "token",
        "apikey",
        "authorization",
        "privatekey",
        "accesskey",
        "databaseurl",
        "dburl",
        "connectionstring",
    }
)


class ExecutionVersionContractError(ValueError):
    """Execution-version inputs are incomplete, unstable, or unsafe for audit storage."""


@dataclass(frozen=True, slots=True)
class ApplicationIdentity:
    """The deployable application identity recorded with an execution.

    ``version`` and ``revision`` are deliberately separate.  A release can retain a
    semantic version while its build revision changes, and either fact must result in
    a distinct audit identity.  They are identifiers rather than free-form metadata,
    so connection URLs and credential-shaped material are rejected.
    """

    version: str
    revision: str

    def __post_init__(self) -> None:
        _require_safe_execution_text(self.version, field_name="application.version")
        _require_safe_execution_text(self.revision, field_name="application.revision")

    def to_canonical_payload(self) -> dict[str, str]:
        return {"revision": self.revision, "version": self.version}


@dataclass(frozen=True, slots=True)
class StrategyExecutionFingerprint:
    """A strategy fingerprint contribution, normally projected from ``StrategyDescriptor``.

    This contract intentionally depends only on immutable scalar values, not the
    strategy registry or its implementation classes.  The builder can therefore be
    used before StrategyDescriptor bootstrap is wired into a recommendation run;
    S2 injects descriptors through :meth:`from_descriptor` without changing the
    version format.
    """

    strategy_id: str
    effective_version: str
    manifest_fingerprint: str | None = None
    implementation_fingerprint: str | None = None

    def __post_init__(self) -> None:
        _require_safe_execution_text(self.strategy_id, field_name="strategy_id")
        _require_safe_execution_text(self.effective_version, field_name="effective_version")
        for field_name, value in (
            ("manifest_fingerprint", self.manifest_fingerprint),
            ("implementation_fingerprint", self.implementation_fingerprint),
        ):
            if value is not None:
                _require_safe_execution_text(value, field_name=field_name)

    @classmethod
    def from_descriptor(cls, descriptor: object) -> "StrategyExecutionFingerprint":
        """Project a frozen StrategyDescriptor without importing the strategy engine.

        Keeping this structural avoids a run-contract → engine dependency while
        still making the exact StrategyDescriptor fields the official extension
        point.  An incompatible descriptor fails immediately rather than silently
        producing a partial fingerprint.
        """
        try:
            return cls(
                strategy_id=str(getattr(descriptor, "strategy_id")),
                effective_version=str(getattr(descriptor, "effective_version")),
                manifest_fingerprint=str(getattr(descriptor, "manifest_hash")),
                implementation_fingerprint=str(getattr(descriptor, "implementation_hash")),
            )
        except (AttributeError, TypeError) as exc:
            raise ExecutionVersionContractError(
                "StrategyDescriptor 必须提供 strategy_id、effective_version、manifest_hash 和 implementation_hash"
            ) from exc

    def to_canonical_payload(self) -> dict[str, str | None]:
        return {
            "effective_version": self.effective_version,
            "implementation_fingerprint": self.implementation_fingerprint,
            "manifest_fingerprint": self.manifest_fingerprint,
            "strategy_id": self.strategy_id,
        }


@dataclass(frozen=True, slots=True)
class ExecutionPolicyIdentity:
    """The selected execution policy and its safe, canonical policy parameters."""

    policy_id: str
    policy_version: str
    settings: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_safe_execution_text(self.policy_id, field_name="execution_policy.policy_id")
        _require_safe_execution_text(self.policy_version, field_name="execution_policy.policy_version")
        if not isinstance(self.settings, Mapping):
            raise ExecutionVersionContractError("execution_policy.settings 必须是对象")
        canonical_settings = _canonical_execution_json_value(
            self.settings,
            path="$.execution_policy.settings",
        )
        if not isinstance(canonical_settings, dict):  # defensive type guard
            raise ExecutionVersionContractError("execution_policy.settings 必须规范化为对象")
        object.__setattr__(
            self,
            "settings",
            MappingProxyType(_freeze_execution_value(canonical_settings)),
        )

    def to_canonical_payload(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "settings": dict(self.settings),
        }

    @property
    def fingerprint(self) -> str:
        canonical = _canonical_execution_json(self.to_canonical_payload())
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ExecutionVersionInput:
    """All immutable facts that contribute to one recommendation-run audit version.

    This deliberately contains no request payload, ORM object, secret or mutable
    registry.  ``strategy_fingerprints`` and ``factor_execution_plan_fingerprint``
    are explicit extension points: callers may first provide the frozen Factor plan
    and add StrategyDescriptor projections when the strategy bootstrap is available.
    """

    application: ApplicationIdentity
    request_schema_version: int
    execution_policy: ExecutionPolicyIdentity
    strategy_fingerprints: tuple[StrategyExecutionFingerprint, ...] = ()
    factor_execution_plan_fingerprint: str | None = None
    schema_version: int = EXECUTION_VERSION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != EXECUTION_VERSION_SCHEMA_VERSION:
            raise ExecutionVersionContractError(
                f"execution version schema 必须为 {EXECUTION_VERSION_SCHEMA_VERSION}"
            )
        if isinstance(self.request_schema_version, bool) or not isinstance(self.request_schema_version, int):
            raise ExecutionVersionContractError("request_schema_version 必须是正整数")
        if self.request_schema_version < 1:
            raise ExecutionVersionContractError("request_schema_version 必须是正整数")
        if self.factor_execution_plan_fingerprint is not None:
            _require_safe_execution_text(
                self.factor_execution_plan_fingerprint,
                field_name="factor_execution_plan_fingerprint",
            )

        fingerprints = tuple(self.strategy_fingerprints)
        if not all(isinstance(item, StrategyExecutionFingerprint) for item in fingerprints):
            raise ExecutionVersionContractError("strategy_fingerprints 必须是 StrategyExecutionFingerprint 元组")
        ordered = tuple(
            sorted(
                fingerprints,
                key=lambda item: (
                    item.strategy_id,
                    item.effective_version,
                    item.manifest_fingerprint or "",
                    item.implementation_fingerprint or "",
                ),
            )
        )
        ids = tuple(item.strategy_id for item in ordered)
        if len(ids) != len(set(ids)):
            raise ExecutionVersionContractError("strategy_fingerprints 不能包含重复 strategy_id")
        object.__setattr__(self, "strategy_fingerprints", ordered)

    def to_canonical_payload(self) -> dict[str, Any]:
        return {
            "application": self.application.to_canonical_payload(),
            "execution_policy": self.execution_policy.to_canonical_payload(),
            "factor_execution_plan_fingerprint": self.factor_execution_plan_fingerprint,
            "request_schema_version": self.request_schema_version,
            "schema_version": self.schema_version,
            "strategy_fingerprints": [item.to_canonical_payload() for item in self.strategy_fingerprints],
        }

    def to_canonical_json(self) -> str:
        """Return the exact, secret-free document used to hash the audit identity."""
        return _canonical_execution_json(self.to_canonical_payload())


@runtime_checkable
class ExecutionVersionBuilder(Protocol):
    """Port for composing audit identity without affecting selection behavior."""

    def build(self, value: ExecutionVersionInput) -> str:
        """Return a non-empty deterministic execution-version string."""


class CanonicalExecutionVersionBuilder:
    """Build ``execution-v1:<sha256>`` from a canonical, secret-free input document."""

    def canonical_json(self, value: ExecutionVersionInput) -> str:
        if not isinstance(value, ExecutionVersionInput):
            raise ExecutionVersionContractError("ExecutionVersionBuilder 仅接受 ExecutionVersionInput")
        return value.to_canonical_json()

    def build(self, value: ExecutionVersionInput) -> str:
        digest = hashlib.sha256(self.canonical_json(value).encode("utf-8")).hexdigest()
        return f"{_EXECUTION_VERSION_PREFIX}:{digest}"


def application_identity_from_environment(
    environ: Mapping[str, str] | None = None,
) -> ApplicationIdentity:
    """Return the deployment identity without reading broad, potentially secret config.

    Build/deploy tooling may set only ``ASHARE_APPLICATION_VERSION`` and
    ``ASHARE_APPLICATION_REVISION``. When the revision is unset it resolves to
    cached ``git rev-parse HEAD`` through the shared resolver so that the API
    enqueue side and the Prefect worker execution side always hash the same
    identity from one checkout. Hostnames, database URLs, tokens, and paths
    stay out of the identity.
    """
    return ApplicationIdentity(
        version=resolve_application_version(environ),
        revision=resolve_application_revision(environ),
    )


def selected_recommendation_execution_policy(request: RunRequest) -> ExecutionPolicyIdentity:
    """Describe the selected recommendation execution policy without changing it.

    These are the request-owned runtime switches that affect which adapters/stages
    execute.  They are recorded only in the version hash and are never fed back into
    candidate selection or scoring.
    """
    return ExecutionPolicyIdentity(
        policy_id="recommendation-run",
        policy_version="v1",
        settings={
            "data_source": request.data_source.value,
            "disable_agent_review": request.disable_agent_review,
            "risk_template": request.risk_template,
            "skip_aux_refresh": request.skip_aux_refresh,
        },
    )


RunRequestInput: TypeAlias = RunRequest | Mapping[str, Any] | str | bytes | bytearray

# Values in a request may legitimately contain arbitrary text, but credential-bearing
# *keys* are never part of the RunRequest schema.  Normalising separators also catches
# common variants such as ``api-token`` and ``database.url``.
_FORBIDDEN_CREDENTIAL_KEY_PARTS = frozenset(
    {
        "credential",
        "password",
        "passwd",
        "secret",
        "token",
        "apikey",
        "authorization",
        "privatekey",
        "accesskey",
        "databaseurl",
        "dburl",
        "connectionstring",
    }
)
_NORMALISE_KEY_RE = re.compile(r"[^a-z0-9]+")


def _assert_no_credentials(value: Any, *, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            key_text = str(key)
            normalised_key = _NORMALISE_KEY_RE.sub("", key_text.lower())
            if any(part in normalised_key for part in _FORBIDDEN_CREDENTIAL_KEY_PARTS):
                raise RunContractError(
                    RunErrorCode.CREDENTIAL_IN_REQUEST_FORBIDDEN.value,
                    f"RunRequest 不允许包含凭据字段：{path}.{key_text}",
                )
            _assert_no_credentials(nested_value, path=f"{path}.{key_text}")
    elif isinstance(value, (list, tuple)):
        for index, nested_value in enumerate(value):
            _assert_no_credentials(nested_value, path=f"{path}[{index}]")


def _require_safe_execution_text(value: str, *, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ExecutionVersionContractError(f"{field_name} 必须是非空字符串")
    if len(value) > 256:
        raise ExecutionVersionContractError(f"{field_name} 长度不能超过 256")
    normalized = _NORMALISE_KEY_RE.sub("", value.lower())
    if "://" in value or any(marker in normalized for marker in _SENSITIVE_VALUE_MARKERS):
        raise RunContractError(
            RunErrorCode.CREDENTIAL_IN_REQUEST_FORBIDDEN.value,
            f"execution version 不允许包含凭据或连接信息：{field_name}",
        )


def _canonical_execution_json_value(value: Any, *, path: str) -> Any:
    """Normalize supported JSON values while rejecting secrets and unstable objects."""
    if isinstance(value, Mapping):
        _assert_no_credentials(value, path=path)
        normalized: dict[str, Any] = {}
        for key in sorted(value, key=lambda item: str(item)):
            if not isinstance(key, str):
                raise ExecutionVersionContractError(f"{path} 的 key 必须是字符串")
            normalized[key] = _canonical_execution_json_value(value[key], path=f"{path}.{key}")
        return normalized
    if isinstance(value, (list, tuple)):
        return [
            _canonical_execution_json_value(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ExecutionVersionContractError(f"{path} 不允许 NaN 或 infinity")
        return value
    if isinstance(value, str):
        _require_safe_execution_text(value, field_name=path)
        return value
    raise ExecutionVersionContractError(
        f"{path} 只允许 JSON 标量、数组或对象，实际为 {type(value).__name__}"
    )


def _freeze_execution_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_execution_value(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_execution_value(item) for item in value)
    return value


def _canonical_execution_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        _canonical_execution_json_value(payload, path="$"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _decode_request_document(raw: str | bytes | bytearray) -> dict[str, Any]:
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RunContractError(
            RunErrorCode.INVALID_REQUEST_SCHEMA.value,
            "request_json 必须是合法 JSON 对象",
        ) from exc
    if not isinstance(decoded, dict):
        raise RunContractError(
            RunErrorCode.INVALID_REQUEST_SCHEMA.value,
            "request_json 顶层必须是对象",
        )
    return decoded


def _move_alias(document: dict[str, Any], *, alias: str, canonical: str) -> None:
    if alias not in document:
        return
    alias_value = document.pop(alias)
    if canonical in document and document[canonical] != alias_value:
        raise RunContractError(
            RunErrorCode.INVALID_REQUEST_SCHEMA.value,
            f"旧字段 {alias} 与 {canonical} 的值冲突",
        )
    document.setdefault(canonical, alias_value)


def _legacy_skill_rows(value: Any, *, field_name: str) -> list[dict[str, Any]]:
    """Project historical skill selectors into the V1 selection shape.

    V0 payloads sometimes stored only IDs and sometimes stored a selected-skill
    projection containing display/version fields.  Only the request-owned identity,
    weight and order survive the upgrade; unsupported fields must not silently become
    part of the V1 contract.
    """
    if not isinstance(value, (list, tuple)):
        raise RunContractError(
            RunErrorCode.INVALID_REQUEST_SCHEMA.value,
            f"旧字段 {field_name} 必须是 skill 列表",
        )
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if isinstance(item, Mapping):
            skill_id = item.get("skill_id", item.get("id"))
            if skill_id is None:
                raise RunContractError(
                    RunErrorCode.INVALID_REQUEST_SCHEMA.value,
                    f"旧字段 {field_name}[{index}] 缺少 skill_id",
                )
            rows.append(
                {
                    "skill_id": skill_id,
                    "weight": item.get("weight", 1.0),
                    "sort_order": item.get("sort_order", 0),
                }
            )
        elif isinstance(item, str):
            rows.append({"skill_id": item, "weight": 1.0, "sort_order": 0})
        else:
            raise RunContractError(
                RunErrorCode.INVALID_REQUEST_SCHEMA.value,
                f"旧字段 {field_name}[{index}] 必须是字符串或对象",
            )
    return rows


def _set_legacy_alias(document: dict[str, Any], *, alias: str, canonical: str, value: Any) -> None:
    if canonical in document and document[canonical] != value:
        raise RunContractError(
            RunErrorCode.INVALID_REQUEST_SCHEMA.value,
            f"旧字段 {alias} 与 {canonical} 的值冲突",
        )
    document.setdefault(canonical, value)


def _upgrade_v0_to_v1(document: Mapping[str, Any]) -> dict[str, Any]:
    """Upgrade the unversioned workbench payload shape to V1 explicitly.

    V0 is the shape found in historical ``params_json`` and old scheduler/API
    payloads.  Every compatibility mapping is explicit; fields not mapped here are
    intentionally left for V1's ``extra=forbid`` validation instead of being dropped.
    """
    upgraded = dict(document)
    upgraded.pop("schema_version", None)
    for alias, canonical in (
        ("mode", "run_mode"),
        ("name", "run_name"),
        ("data_source_mode", "data_source"),
        ("risk", "risk_template"),
        ("trade_date", "trade_date_override"),
    ):
        _move_alias(upgraded, alias=alias, canonical=canonical)

    # Old clients used ``preset`` as either a string ID or a tiny serialized preset.
    if "preset" in upgraded:
        legacy_preset = upgraded.pop("preset")
        if isinstance(legacy_preset, Mapping):
            legacy_preset = legacy_preset.get("preset_id")
        if legacy_preset is None:
            raise RunContractError(
                RunErrorCode.INVALID_REQUEST_SCHEMA.value,
                "旧字段 preset 必须包含 preset_id",
            )
        _set_legacy_alias(upgraded, alias="preset", canonical="preset_id", value=legacy_preset)

    # ``review``/``refresh`` were positive flags; V1 stores the explicit disable/skip
    # flags so the canonical JSON never relies on a caller-side default.
    if "review" in upgraded:
        review = upgraded.pop("review")
        if not isinstance(review, bool):
            raise RunContractError(
                RunErrorCode.INVALID_REQUEST_SCHEMA.value,
                "旧字段 review 必须是布尔值",
            )
        _set_legacy_alias(
            upgraded,
            alias="review",
            canonical="disable_agent_review",
            value=not review,
        )
    if "refresh" in upgraded:
        refresh = upgraded.pop("refresh")
        if not isinstance(refresh, bool):
            raise RunContractError(
                RunErrorCode.INVALID_REQUEST_SCHEMA.value,
                "旧字段 refresh 必须是布尔值",
            )
        _set_legacy_alias(
            upgraded,
            alias="refresh",
            canonical="skip_aux_refresh",
            value=not refresh,
        )

    # Both list forms existed in old request producers.  The selected_skills form
    # may contain display-only fields, so it is projected rather than copied raw.
    legacy_skill_rows: list[dict[str, Any]] | None = None
    for field_name in ("selected_skills", "selected_skill_ids"):
        if field_name not in upgraded:
            continue
        rows = _legacy_skill_rows(upgraded.pop(field_name), field_name=field_name)
        if legacy_skill_rows is not None and legacy_skill_rows != rows:
            raise RunContractError(
                RunErrorCode.INVALID_REQUEST_SCHEMA.value,
                "selected_skills 与 selected_skill_ids 的值冲突",
            )
        legacy_skill_rows = rows
    if legacy_skill_rows is not None:
        _set_legacy_alias(upgraded, alias="selected_skills", canonical="skills", value=legacy_skill_rows)

    single_skill_id = upgraded.pop("single_skill_id", None)
    legacy_skill_id = upgraded.pop("skill_id", None)
    if single_skill_id is not None and legacy_skill_id is not None and single_skill_id != legacy_skill_id:
        raise RunContractError(
            RunErrorCode.INVALID_REQUEST_SCHEMA.value,
            "single_skill_id 与 skill_id 的值冲突",
        )
    if single_skill_id is None:
        single_skill_id = legacy_skill_id
    single_skill_weight = upgraded.pop("single_skill_weight", 1.0)
    if single_skill_id is not None:
        if upgraded.get("skills"):
            raise RunContractError(
                RunErrorCode.INVALID_REQUEST_SCHEMA.value,
                "single_skill_id 与 skills 不能同时指定",
            )
        upgraded["skills"] = [
            {"skill_id": str(single_skill_id), "weight": single_skill_weight, "sort_order": 0}
        ]

    # Historical recommendation backtests carried only a marker in params_json.
    # Preserve that identity, but do not let the marker remain as an unknown V1 key.
    if "backtest" in upgraded:
        backtest = upgraded.pop("backtest")
        if not isinstance(backtest, bool):
            raise RunContractError(
                RunErrorCode.INVALID_REQUEST_SCHEMA.value,
                "旧字段 backtest 必须是布尔值",
            )
        if backtest:
            _set_legacy_alias(upgraded, alias="backtest", canonical="run_mode", value=RunMode.BACKTEST.value)

    upgraded["schema_version"] = RunRequest.CURRENT_SCHEMA_VERSION
    return upgraded


def upgrade_run_request_document(document: Mapping[str, Any]) -> dict[str, Any]:
    """Upgrade a request document to the current schema without validating it yet."""
    if not isinstance(document, Mapping):
        raise RunContractError(
            RunErrorCode.INVALID_REQUEST_SCHEMA.value,
            "RunRequest 输入必须是对象",
        )
    raw_document = dict(document)
    _assert_no_credentials(raw_document)

    version = raw_document.get("schema_version", 0)
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise RunContractError(
            RunErrorCode.INVALID_REQUEST_SCHEMA.value,
            "schema_version 必须是非负整数",
        )
    if version > RunRequest.CURRENT_SCHEMA_VERSION:
        raise RunContractError(
            RunErrorCode.UNSUPPORTED_REQUEST_SCHEMA.value,
            f"不支持未来 RunRequest schema_version={version}",
        )
    if version == 0:
        raw_document = _upgrade_v0_to_v1(raw_document)
        version = RunRequest.CURRENT_SCHEMA_VERSION
    if version != RunRequest.CURRENT_SCHEMA_VERSION:
        raise RunContractError(
            RunErrorCode.UNSUPPORTED_REQUEST_SCHEMA.value,
            f"没有可用的 RunRequest schema upgrader：{version}",
        )
    return raw_document


def validate_run_request_no_credentials(payload: Mapping[str, Any]) -> None:
    """Reject credential-shaped keys before an entry point drops legacy fields."""
    _assert_no_credentials(payload)


def parse_run_request(payload: RunRequestInput) -> RunRequest:
    """Return an immutable V1 request from an API, scheduler, CLI or worker input."""
    if isinstance(payload, RunRequest):
        return payload
    document = _decode_request_document(payload) if isinstance(payload, (str, bytes, bytearray)) else payload
    upgraded = upgrade_run_request_document(document)
    try:
        return RunRequest.model_validate(upgraded)
    except ValidationError as exc:
        # All entry points receive one stable exception type.  The original Pydantic
        # error remains chained for logs/debugging, while callers can branch on code.
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise RunContractError(
            RunErrorCode.INVALID_REQUEST_SCHEMA.value,
            f"RunRequest 校验失败: {details}",
        ) from exc


def canonical_request_json(payload: RunRequestInput) -> str:
    """Parse any supported input and return the one canonical persistence encoding."""
    return parse_run_request(payload).to_canonical_json()


__all__ = [
    "ApplicationIdentity",
    "CanonicalExecutionVersionBuilder",
    "DataSourceId",
    "ExecutionPolicyIdentity",
    "ExecutionVersionBuilder",
    "ExecutionVersionContractError",
    "ExecutionVersionInput",
    "EXECUTION_VERSION_SCHEMA_VERSION",
    "RunActor",
    "RunContractError",
    "RunErrorCode",
    "RunMode",
    "RunPhase",
    "RunRequest",
    "RunRequestInput",
    "RUN_REQUEST_SCHEMA_VERSION",
    "RunStatus",
    "RunTransition",
    "RunTrigger",
    "RUN_PHASE_ORDER",
    "SkillSelection",
    "StrategyExecutionFingerprint",
    "application_identity_from_environment",
    "canonical_request_json",
    "parse_run_request",
    "selected_recommendation_execution_policy",
    "upgrade_run_request_document",
    "validate_run_request_no_credentials",
]
