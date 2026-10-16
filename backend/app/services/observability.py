"""Pure S8 observability contracts and small process-lifecycle primitives."""
from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
from typing import Any

_CORRELATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")
_current_correlation_id: ContextVar[str | None] = ContextVar(
    "ashare_correlation_id", default=None
)
_ALLOWED_LOG_FIELDS = frozenset(
    {"correlation_id", "stage", "identity", "identity_hash", "reason_code"}
)


def normalize_correlation_id(value: str | None) -> str:
    candidate = str(value or "").strip()
    return candidate if _CORRELATION_RE.fullmatch(candidate) else uuid.uuid4().hex


def current_correlation_id() -> str:
    existing = _current_correlation_id.get()
    if existing:
        return existing
    generated = uuid.uuid4().hex
    _current_correlation_id.set(generated)
    return generated


@contextmanager
def correlation_scope(value: str | None):
    correlation_id = normalize_correlation_id(value)
    token: Token[str | None] = _current_correlation_id.set(correlation_id)
    try:
        yield correlation_id
    finally:
        _current_correlation_id.reset(token)


@dataclass(frozen=True, slots=True)
class CorrelationChain:
    correlation_id: str
    api_identity: str | None = None
    scheduler_identity: str | None = None
    prefect_identity: str | None = None
    run_identity: str | None = None
    provider_identity: str | None = None
    database_identity: str | None = None

    @classmethod
    def start(cls, correlation_id: str | None, *, api_identity: str) -> "CorrelationChain":
        return cls(normalize_correlation_id(correlation_id), api_identity=api_identity)

    def with_identity(self, stage: str, identity: str) -> "CorrelationChain":
        field_name = f"{stage}_identity"
        if field_name not in self.__dataclass_fields__:
            raise ValueError(f"unsupported correlation stage: {stage}")
        values = {
            name: getattr(self, name) for name in self.__dataclass_fields__
        }
        values[field_name] = str(identity)
        return CorrelationChain(**values)

    def audit_fields(self, *, stage: str, reason_code: str) -> dict[str, str]:
        identity = getattr(self, f"{stage}_identity", None)
        return allowlisted_log_fields(
            {
                "correlation_id": self.correlation_id,
                "stage": stage,
                "identity_hash": _identity_hash(identity),
                "reason_code": reason_code,
            }
        )


def allowlisted_log_fields(fields: Mapping[str, Any]) -> dict[str, str]:
    """Drop payloads, credentials and arbitrary metadata from audit logs."""
    output: dict[str, str] = {}
    for key in _ALLOWED_LOG_FIELDS:
        value = fields.get(key)
        if value is not None:
            output[key] = str(value)[:160]
    return output


def log_observation(
    logger: logging.Logger,
    *,
    stage: str,
    reason_code: str,
    identity: str | None = None,
    level: int = logging.INFO,
) -> None:
    fields = allowlisted_log_fields(
        {
            "correlation_id": current_correlation_id(),
            "stage": stage,
            "identity_hash": _identity_hash(identity),
            "reason_code": reason_code,
        }
    )
    logger.log(level, "observability=%s", json.dumps(fields, sort_keys=True))


class AlertKind(StrEnum):
    LATENCY = "latency"
    HEARTBEAT_STALE = "heartbeat_stale"
    QUEUE_DELAY = "queue_delay"
    FLOW_FAILURE = "flow_failure"
    QUALITY_BLOCKER = "quality_blocker"
    IDENTITY_MISMATCH = "identity_mismatch"
    RISK_HALT = "risk_halt"


@dataclass(frozen=True, slots=True)
class AlertCondition:
    kind: AlertKind
    threshold: float | None = None
    enabled: bool = True
    severity: str = "warning"


@dataclass(frozen=True, slots=True)
class AlertRegistry:
    conditions: tuple[AlertCondition, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        kinds = tuple(item.kind for item in self.conditions)
        if len(kinds) != len(set(kinds)):
            raise ValueError("alert kinds must be unique")
        if set(kinds) != set(AlertKind):
            raise ValueError("all seven alert kinds must be registered exactly once")

    @classmethod
    def defaults(cls) -> "AlertRegistry":
        return cls(
            (
                AlertCondition(AlertKind.LATENCY, 3000),
                AlertCondition(AlertKind.HEARTBEAT_STALE, 1200),
                AlertCondition(AlertKind.QUEUE_DELAY, 300),
                AlertCondition(AlertKind.FLOW_FAILURE),
                AlertCondition(AlertKind.QUALITY_BLOCKER),
                AlertCondition(AlertKind.IDENTITY_MISMATCH),
                AlertCondition(AlertKind.RISK_HALT, severity="critical"),
            )
        )

    def evaluate(self, facts: Mapping[str, Any]) -> list[dict[str, str]]:
        active: list[dict[str, str]] = []
        for condition in self.conditions:
            if not condition.enabled:
                continue
            observed = _alert_observed(condition, facts)
            if observed:
                active.append(
                    {
                        "kind": condition.kind.value,
                        "severity": condition.severity,
                        "reason_code": f"S8_{condition.kind.value.upper()}",
                    }
                )
        return active

    def to_dict(self) -> dict[str, Any]:
        return {
            "registered": [item.kind.value for item in self.conditions],
            "conditions": [
                {
                    "kind": item.kind.value,
                    "threshold": item.threshold,
                    "enabled": item.enabled,
                    "severity": item.severity,
                }
                for item in self.conditions
            ],
        }


class ApplicationLifespan:
    """Synchronous lifecycle coordinator with failure cleanup and idempotent stop."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cleanups: list[tuple[str, Callable[[], None]]] = []
        self._started = False
        self._shutdown = False

    def add_cleanup(self, name: str, cleanup: Callable[[], None]) -> None:
        with self._lock:
            if self._started:
                raise RuntimeError("cleanup registration is closed after startup")
            self._cleanups.append((str(name), cleanup))

    def startup(self, initialize: Callable[[], None]) -> None:
        with self._lock:
            if self._started and not self._shutdown:
                return
            self._started = True
            self._shutdown = False
        try:
            initialize()
        except BaseException:
            self.shutdown()
            raise

    def shutdown(self) -> tuple[str, ...]:
        with self._lock:
            if self._shutdown:
                return ()
            self._shutdown = True
            cleanups = tuple(reversed(self._cleanups))
        failures: list[str] = []
        for name, cleanup in cleanups:
            try:
                cleanup()
            except Exception:  # noqa: BLE001 - best-effort fallback, now debug-logged
                failures.append(name)
        return tuple(failures)

    @property
    def is_shutdown(self) -> bool:
        with self._lock:
            return self._shutdown


def _alert_observed(condition: AlertCondition, facts: Mapping[str, Any]) -> bool:
    kind = condition.kind
    if kind is AlertKind.LATENCY:
        return _number(facts.get("latency_ms")) > float(condition.threshold or 0)
    if kind is AlertKind.HEARTBEAT_STALE:
        return _number(facts.get("heartbeat_age_seconds")) > float(condition.threshold or 0)
    if kind is AlertKind.QUEUE_DELAY:
        return _number(facts.get("queue_delay_seconds")) > float(condition.threshold or 0)
    flags = {
        AlertKind.FLOW_FAILURE: "flow_failed",
        AlertKind.QUALITY_BLOCKER: "quality_blocked",
        AlertKind.IDENTITY_MISMATCH: "identity_mismatch",
        AlertKind.RISK_HALT: "risk_halted",
    }
    return bool(facts.get(flags[kind]))


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _identity_hash(identity: str | None) -> str:
    return sha256(str(identity or "unknown").encode("utf-8")).hexdigest()


__all__ = [
    "AlertCondition",
    "AlertKind",
    "AlertRegistry",
    "ApplicationLifespan",
    "CorrelationChain",
    "allowlisted_log_fields",
    "correlation_scope",
    "current_correlation_id",
    "log_observation",
    "normalize_correlation_id",
]
