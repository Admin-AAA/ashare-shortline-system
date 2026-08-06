"""Provider-agnostic data source request trace contract (R-SOURCE-2).

权威契约：Tushare router 与 Baostock 分钟 K 主备路径都投影到同一 trace，
持久化到 MySQL（append-only），data_source_health 从统一 trace 聚合。
不含 credential、完整请求 token 或上游原始敏感错误正文。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.engine.canonical import CanonicalValue, canonical_fingerprint


TRACE_CONTRACT_ID = "data_source_request_trace"
TRACE_CONTRACT_VERSION = 1


class DataSourceAttemptOutcome(StrEnum):
    SUCCESS = "success"
    VALID_EMPTY = "valid_empty"
    FAILURE = "failure"
    SKIPPED_BREAKER = "skipped_breaker"


class FinalOutcome(StrEnum):
    SUCCESS = "success"
    VALID_EMPTY = "valid_empty"
    FAILURE = "failure"


@dataclass(frozen=True, slots=True)
class DataContractValidation(CanonicalValue):
    """provider 成功后对 operation 数据契约（字段/日期/单位/复权）的校验结果。"""

    contract_id: str
    contract_version: int
    passed: bool
    field_fingerprint: str | None = None
    checks: tuple[str, ...] = ()
    violation: str | None = None


@dataclass(frozen=True, slots=True)
class DataSourceAttempt(CanonicalValue):
    ordinal: int
    provider: str
    outcome: DataSourceAttemptOutcome
    duration_ms: int
    failure_category: str | None = None
    retryable: bool | None = None
    fallback_allowed: bool | None = None
    fallback_from: str | None = None
    fallback_to: str | None = None
    field_fingerprint: str | None = None
    contract_validation: DataContractValidation | None = None

    def __post_init__(self) -> None:
        if self.ordinal < 1:
            raise ValueError("attempt ordinal must be >= 1")
        if not self.provider.strip():
            raise ValueError("attempt provider must not be blank")
        if self.duration_ms < 0:
            raise ValueError("attempt duration_ms must be >= 0")
        if self.outcome is DataSourceAttemptOutcome.FAILURE and not self.failure_category:
            raise ValueError("failure attempt requires a failure_category")


@dataclass(frozen=True, slots=True)
class DataSourceRequestTrace(CanonicalValue):
    trace_id: str
    correlation_id: str
    operation_name: str
    request_identity_hash: str
    contract_id: str
    contract_version: int
    attempts: tuple[DataSourceAttempt, ...]
    selected_provider: str | None
    final_outcome: FinalOutcome
    # telemetry_status 由持久化层在写入后回填（ok/failed）；不计入业务 identity。
    telemetry_status: str = "pending"

    def __post_init__(self) -> None:
        if not self.trace_id.strip() or not self.correlation_id.strip():
            raise ValueError("trace_id and correlation_id must not be blank")
        if not self.operation_name.strip():
            raise ValueError("operation_name must not be blank")
        if not self.attempts:
            raise ValueError("trace requires at least one attempt")
        ordinals = [attempt.ordinal for attempt in self.attempts]
        if ordinals != list(range(1, len(self.attempts) + 1)):
            raise ValueError("attempt ordinals must be contiguous starting at 1")
        if self.final_outcome is FinalOutcome.SUCCESS and not self.selected_provider:
            raise ValueError("successful trace requires selected_provider")

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)

    @property
    def fallback_count(self) -> int:
        return sum(1 for attempt in self.attempts if attempt.fallback_to)


def request_identity_hash(
    operation_name: str,
    *,
    code: str | None = None,
    params: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """请求 identity hash（不含 credential）。用于幂等键与关联查询。"""
    return canonical_fingerprint(
        {
            "operation_name": operation_name,
            "code": code or "",
            "params": params or {},
            "extra": extra or {},
        }
    )


__all__ = [
    "TRACE_CONTRACT_ID",
    "TRACE_CONTRACT_VERSION",
    "DataContractValidation",
    "DataSourceAttempt",
    "DataSourceAttemptOutcome",
    "DataSourceRequestTrace",
    "FinalOutcome",
    "request_identity_hash",
]
