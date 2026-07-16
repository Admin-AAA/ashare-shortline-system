"""Agent Research Gateway HTTP adapter.

This module deliberately owns only request validation, dependency wiring, response
projection, and stable HTTP error mapping.  Backtest execution, persistence, and
experiment invariants remain in the typed runner and research repository layers.
The router is intentionally not registered here; application composition must
opt in with ``app.include_router(backtests.router)``.
"""
from __future__ import annotations

import inspect
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, is_dataclass
from datetime import date
from enum import Enum
from importlib import import_module
from typing import Annotated, Any, Literal, Protocol, TypeAlias, runtime_checkable
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.engine.backtest.contracts import (
    BacktestDataPolicy,
    BacktestDomainError,
    BacktestRequest,
    DateRange,
    ExecutionPolicy,
    SingleDate,
)
from app.services.backtest.research_repository import (
    BacktestStateConflict,
    ImmutableRecordConflict,
    RepositoryConfigurationError,
    RequestClaimConflict,
    ResearchBacktestRepository,
    ResearchRecordNotFound,
    ResearchRepositoryError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")
backtests_router = APIRouter(prefix="/backtests", tags=["backtests"])
experiment_router = APIRouter(prefix="/backtest-experiments", tags=["backtest-experiments"])

# Publicly named aliases make it straightforward for tests and application
# composition to override these dependencies without touching implementation
# internals or a global singleton.
ResearchRepository: TypeAlias = ResearchBacktestRepository


@runtime_checkable
class ResearchBacktestRunner(Protocol):
    """Application boundary consumed by the HTTP adapter.

    The concrete runner is intentionally resolved lazily: the P0 domain and
    migration may be deployed before its concrete implementation.  Tests can
    override :func:`get_research_backtest_runner` with a fake satisfying this
    small, explicit protocol.
    """

    def preflight(self, request: BacktestRequest) -> Any:
        ...

    def run(self, request: BacktestRequest) -> Any:
        ...

    def replay(self, backtest_id: str, *, force_rerun: bool = True) -> Any:
        ...


class _StrictModel(BaseModel):
    """Common Pydantic v2 contract for all Agent-facing request bodies."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )


StrategyId = Annotated[str, Field(min_length=1, max_length=80)]
BacktestId = Annotated[
    str,
    Path(
        min_length=3,
        max_length=80,
        pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.:-]*$",
        description="The immutable research backtest attempt identifier.",
    ),
]
ExperimentId = Annotated[
    str,
    Path(
        min_length=3,
        max_length=80,
        pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.:-]*$",
        description="The immutable research experiment identifier.",
    ),
]
Sha256 = Annotated[str, Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")]
StockCode = Annotated[str, Field(min_length=6, max_length=6, pattern=r"^\d{6}$")]
IsoDate = Annotated[str, Field(min_length=10, max_length=10)]


def _validate_single_strategy_id(value: str) -> str:
    """Reject the usual accidental multi-strategy and ambiguous spellings early."""

    if not value:
        raise ValueError("strategy_id must be non-empty")
    if any(token in value for token in (",", "\n", "\r", "\t")):
        raise ValueError("strategy_id must name exactly one strategy")
    return value


def _validate_iso_date(value: str, field_name: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO date (YYYY-MM-DD)") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{field_name} must be an ISO date (YYYY-MM-DD)")
    return value


class ExecutionPolicyRequest(_StrictModel):
    """Explicit, bounded execution economics passed to the immutable domain DTO."""

    schema_version: Literal[1] = 1
    entry_lookahead_days: int = Field(default=1, ge=1, le=10)
    horizon_days: int = Field(default=5, ge=1, le=60)
    commission_rate: float = Field(default=0.0001, ge=0, le=0.01)
    stamp_tax_rate: float = Field(default=0.0005, ge=0, le=0.01)
    entry_slippage_bps: float = Field(default=10.0, ge=0, le=500)
    exit_slippage_bps: float = Field(default=10.0, ge=0, le=500)
    position_size: float = Field(default=1.0, gt=0, le=1)
    stop_loss_pct: float | None = Field(default=5.0, gt=0, lt=100)
    take_profit_pct: float | None = Field(default=10.0, gt=0, lt=100)
    max_entry_gap_pct: float | None = Field(default=None, ge=0, lt=100)
    skip_untradable_entry_bars: bool = True
    intraday_exit_priority: Literal["stop_loss", "take_profit"] = "stop_loss"

    def to_domain(self) -> ExecutionPolicy:
        return ExecutionPolicy(**self.model_dump())


class BacktestDataPolicyRequest(_StrictModel):
    """Research Gateway can never widen the local, read-only data boundary."""

    local_only: Literal[True] = True
    allow_market_data_write: Literal[False] = False
    allow_auxiliary_data_write: Literal[False] = False

    def to_domain(self) -> BacktestDataPolicy:
        return BacktestDataPolicy(**self.model_dump())


class BacktestRequestBody(_StrictModel):
    """Strict HTTP representation of a single immutable ``BacktestRequest``.

    Date selection has no tagged-union wrapper at the wire level so clients can
    send either ``signal_date`` or the inclusive ``start_date``/``end_date``
    pair.  The model validator enforces the exclusive union before the request
    reaches application code.
    """

    schema_version: Literal[1] = 1
    strategy_id: StrategyId
    signal_date: IsoDate | None = None
    start_date: IsoDate | None = None
    end_date: IsoDate | None = None
    execution_policy: ExecutionPolicyRequest = Field(default_factory=ExecutionPolicyRequest)
    data_policy: BacktestDataPolicyRequest = Field(default_factory=BacktestDataPolicyRequest)
    target_codes: list[StockCode] = Field(default_factory=list, max_length=50)
    parameter_set: dict[str, Any] = Field(default_factory=dict)
    force_rerun: bool = False

    @field_validator("strategy_id")
    @classmethod
    def validate_strategy_id(cls, value: str) -> str:
        return _validate_single_strategy_id(value)

    @field_validator("signal_date", "start_date", "end_date")
    @classmethod
    def validate_date_values(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        return _validate_iso_date(value, info.field_name)

    @field_validator("target_codes")
    @classmethod
    def validate_target_codes(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("target_codes must not contain duplicates")
        return value

    @model_validator(mode="after")
    def validate_exclusive_date_selector(self) -> "BacktestRequestBody":
        has_signal_date = self.signal_date is not None
        has_start_date = self.start_date is not None
        has_end_date = self.end_date is not None
        has_range = has_start_date or has_end_date
        if has_signal_date == has_range:
            raise ValueError("provide exactly one of signal_date or start_date/end_date")
        if has_range and not (has_start_date and has_end_date):
            raise ValueError("start_date and end_date must be provided together")
        if self.start_date is not None and self.end_date is not None and self.start_date > self.end_date:
            raise ValueError("start_date must not be later than end_date")
        return self

    def to_domain(self) -> BacktestRequest:
        selector = (
            SingleDate(self.signal_date)
            if self.signal_date is not None
            else DateRange(self.start_date or "", self.end_date or "")
        )
        return BacktestRequest(
            schema_version=self.schema_version,
            strategy_id=self.strategy_id,
            date_selector=selector,
            execution_policy=self.execution_policy.to_domain(),
            target_codes=tuple(self.target_codes),
            data_policy=self.data_policy.to_domain(),
            parameter_set=self.parameter_set,
            force_rerun=self.force_rerun,
        )


class PreflightBacktestRequest(BacktestRequestBody):
    """Preflight cannot claim a forced execution attempt."""

    force_rerun: Literal[False] = False


class ReplayBacktestRequest(_StrictModel):
    """Replay control body: no snapshot, request, or data-policy override exists."""

    force_rerun: Literal[True] = True


class ExperimentSplitsRequest(_StrictModel):
    """Canonical, bounded, chronologically disjoint train/validation/OOS splits."""

    train: list[IsoDate] = Field(min_length=1, max_length=366)
    validation: list[IsoDate] = Field(min_length=1, max_length=366)
    out_of_sample: list[IsoDate] = Field(min_length=1, max_length=366)

    @field_validator("train", "validation", "out_of_sample")
    @classmethod
    def validate_split_dates(cls, values: list[str], info: Any) -> list[str]:
        normalized = [_validate_iso_date(value, info.field_name) for value in values]
        if normalized != sorted(normalized):
            raise ValueError(f"{info.field_name} dates must be sorted ascending")
        if len(normalized) != len(set(normalized)):
            raise ValueError(f"{info.field_name} dates must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_split_order_and_size(self) -> "ExperimentSplitsRequest":
        all_dates = [*self.train, *self.validation, *self.out_of_sample]
        if len(all_dates) > 750:
            raise ValueError("experiment split may contain at most 750 dates")
        if len(all_dates) != len(set(all_dates)):
            raise ValueError("train, validation, and out_of_sample dates must not overlap")
        if not (max(self.train) < min(self.validation) < min(self.out_of_sample)):
            raise ValueError("experiment splits must satisfy train < validation < out_of_sample")
        return self

    def to_repository_payload(self) -> dict[str, list[str]]:
        return self.model_dump()


class ExperimentAcceptanceRequest(_StrictModel):
    """Predeclared acceptance gates; metrics cannot be cherry-picked after OOS."""

    min_trade_count: int = Field(ge=1, le=100_000)
    min_win_rate_pct: float = Field(ge=0, le=100)
    min_profit_loss_ratio: float = Field(ge=0, le=1000)
    min_average_net_return_pct: float = Field(ge=-100, le=1000)
    max_drawdown_pct: float = Field(ge=0, le=100)

    def to_repository_payload(self) -> dict[str, Any]:
        return self.model_dump()


class ExperimentCreateRequest(_StrictModel):
    """Agent-safe experiment plan input.

    Snapshot and execution policy identities are deliberately absent: they are
    derived from the completed baseline, which prevents a client from pairing a
    candidate with a different dataset or cost policy.
    """

    hypothesis: str = Field(min_length=1, max_length=2_000)
    baseline_backtest_id: Annotated[str, Field(min_length=3, max_length=80)]
    candidate_strategy_id: StrategyId
    candidate_strategy_version: Annotated[str, Field(min_length=1, max_length=160)]
    frozen_candidate_fingerprint: Sha256
    splits: ExperimentSplitsRequest
    acceptance: ExperimentAcceptanceRequest

    @field_validator("candidate_strategy_id")
    @classmethod
    def validate_candidate_strategy_id(cls, value: str) -> str:
        return _validate_single_strategy_id(value)


class GatewayDependencyUnavailable(RuntimeError):
    """Raised only when application composition has not installed the P0 runner."""

    code = "research_backtest_runner_unavailable"


# The concrete runner is an application concern which may be delivered after
# domain contracts/migrations.  These are the sanctioned locations; no dynamic
# private-method probing or service fallback is permitted.
_RUNNER_IMPORT_CANDIDATES = (
    ("app.services.backtest.research_runner", "ResearchBacktestRunner"),
)


def get_research_repository(db: Session = Depends(get_db)) -> ResearchRepository:
    """Construct the dedicated research repository for one request session."""

    try:
        return ResearchBacktestRepository(db)
    except Exception as exc:  # noqa: BLE001 - convert repository configuration to stable HTTP errors
        _raise_gateway_error(exc)


def _construct_research_backtest_runner(
    repository: ResearchRepository = Depends(get_research_repository),
) -> ResearchBacktestRunner:
    """Resolve the concrete application runner through an overridable boundary."""

    runner_type: type[Any] | None = None
    for module_name, symbol_name in _RUNNER_IMPORT_CANDIDATES:
        try:
            module = import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name == module_name:
                continue
            raise GatewayDependencyUnavailable(
                "ResearchBacktestRunner dependencies are not installed."
            ) from exc
        candidate = getattr(module, symbol_name, None)
        if candidate is not None:
            runner_type = candidate
            break
    if runner_type is None:
        raise GatewayDependencyUnavailable(
            "ResearchBacktestRunner is unavailable; install the P0 runner before registering this router."
        )

    try:
        signature = inspect.signature(runner_type)
        accepts_repository = "repository" in signature.parameters or any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
        instance = runner_type(repository=repository) if accepts_repository else runner_type(repository)
    except (TypeError, ValueError) as exc:
        raise GatewayDependencyUnavailable(
            "ResearchBacktestRunner must accept the injected research repository."
        ) from exc
    if not isinstance(instance, ResearchBacktestRunner):
        raise GatewayDependencyUnavailable(
            "ResearchBacktestRunner does not expose preflight, run, and replay operations."
        )
    return instance


def get_research_backtest_runner(
    repository: ResearchRepository = Depends(get_research_repository),
) -> ResearchBacktestRunner:
    """Resolve the runner and translate dependency setup failures at the HTTP boundary."""

    try:
        return _construct_research_backtest_runner(repository)
    except Exception as exc:  # noqa: BLE001 - dependency errors require the same stable envelope
        _raise_gateway_error(exc)


_NOT_FOUND_CODES = frozenset(
    {
        "research_record_not_found",
        "backtest_not_found",
        "experiment_not_found",
        "strategy_not_found",
        "strategy_disabled",
        "snapshot_not_found",
    }
)
_CONFLICT_CODES = frozenset(
    {
        "research_immutable_record_conflict",
        "backtest_request_claim_conflict",
        "backtest_state_conflict",
        "snapshot_integrity_error",
        "snapshot_artifact_integrity_error",
        "snapshot_artifact_unavailable",
        "research_repository_configuration_error",
    }
)


def _stable_error_code(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, Enum):
        code = code.value
    if isinstance(code, str) and code:
        return code
    if isinstance(exc, ValueError):
        return "invalid_backtest_request"
    return "research_gateway_error"


def _error_details(exc: Exception) -> dict[str, Any]:
    details = getattr(exc, "details", {})
    return _json_safe_value(details) if isinstance(details, Mapping) else {}


def _raise_gateway_error(exc: Exception) -> None:
    """Map known domain/repository failures to documented stable HTTP statuses."""

    if isinstance(exc, GatewayDependencyUnavailable):
        raise HTTPException(
            status_code=503,
            detail={"code": exc.code, "message": str(exc), "details": {}},
        ) from exc

    known_domain_error = isinstance(exc, (BacktestDomainError, ResearchRepositoryError, ValueError))
    if not known_domain_error:
        logger.exception("Unexpected Agent Research Gateway failure")
        raise HTTPException(
            status_code=500,
            detail={"code": "research_gateway_internal_error", "message": "Internal server error", "details": {}},
        ) from exc

    code = _stable_error_code(exc)
    if isinstance(exc, ResearchRecordNotFound) or code in _NOT_FOUND_CODES:
        status_code = 404
    elif isinstance(
        exc,
        (
            ImmutableRecordConflict,
            RequestClaimConflict,
            BacktestStateConflict,
            RepositoryConfigurationError,
        ),
    ) or code in _CONFLICT_CODES:
        status_code = 409
    else:
        status_code = 400
    raise HTTPException(
        status_code=status_code,
        detail={"code": code, "message": str(exc), "details": _error_details(exc)},
    ) from exc


def _invoke(operation: Callable[[], Any]) -> Any:
    try:
        return operation()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - this is the HTTP domain boundary
        _raise_gateway_error(exc)


def _json_safe_value(value: Any) -> Any:
    """Project DTOs, SQLAlchemy rows, and fakes without exposing private state."""

    if isinstance(value, BaseModel):
        return _json_safe_value(value.model_dump(mode="json"))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (date,)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, Mapping):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if is_dataclass(value) and not isinstance(value, type):
        return _json_safe_value(asdict(value))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_safe_value(item) for item in value]
    if hasattr(value, "canonical_payload") and callable(value.canonical_payload):
        return _json_safe_value(value.canonical_payload())
    if hasattr(value, "__dict__"):
        result: dict[str, Any] = {}
        for key, item in vars(value).items():
            if key.startswith("_"):
                continue
            if key.endswith("_json") and isinstance(item, str):
                try:
                    result[key[:-5]] = _json_safe_value(json.loads(item))
                    continue
                except json.JSONDecodeError:
                    pass
            result[key] = _json_safe_value(item)
        return result
    return str(value)


def _response_payload(value: Any) -> dict[str, Any]:
    projected = _json_safe_value(value)
    return projected if isinstance(projected, dict) else {"result": projected}


def _required_record_value(record: Any, field_name: str) -> str:
    value = record.get(field_name) if isinstance(record, Mapping) else getattr(record, field_name, None)
    if not isinstance(value, str) or not value:
        raise ValueError(f"baseline backtest is missing {field_name}")
    return value


@backtests_router.post("/preflight")
def preflight_backtest(
    payload: PreflightBacktestRequest,
    runner: ResearchBacktestRunner = Depends(get_research_backtest_runner),
) -> dict[str, Any]:
    """Validate an immutable request and return per-date data-readiness coverage."""

    return _response_payload(_invoke(lambda: runner.preflight(payload.to_domain())))


@backtests_router.post("")
def create_backtest(
    payload: BacktestRequestBody,
    runner: ResearchBacktestRunner = Depends(get_research_backtest_runner),
) -> dict[str, Any]:
    """Create or reuse one single-strategy research backtest attempt."""

    return _response_payload(_invoke(lambda: runner.run(payload.to_domain())))


@backtests_router.post("/{backtest_id}/replay")
def replay_backtest(
    backtest_id: BacktestId,
    payload: ReplayBacktestRequest,
    runner: ResearchBacktestRunner = Depends(get_research_backtest_runner),
) -> dict[str, Any]:
    """Create a forced replay attempt using only the original immutable snapshot."""

    return _response_payload(
        _invoke(lambda: runner.replay(backtest_id, force_rerun=payload.force_rerun))
    )


@backtests_router.get("/{backtest_id}")
def get_backtest_detail(
    backtest_id: BacktestId,
    repository: ResearchRepository = Depends(get_research_repository),
) -> dict[str, Any]:
    """Return a single isolated attempt, its versions, snapshot, and metrics."""

    return _response_payload(_invoke(lambda: repository.get_run(backtest_id)))


@backtests_router.get("/{backtest_id}/candidates")
def get_backtest_candidates(
    backtest_id: BacktestId,
    repository: ResearchRepository = Depends(get_research_repository),
) -> dict[str, Any]:
    """Return signal/no-signal candidate decisions and filtering audit rows."""

    items = _invoke(lambda: repository.list_candidates(backtest_id))
    return {"backtest_id": backtest_id, "items": _json_safe_value(items)}


@backtests_router.get("/{backtest_id}/trades")
def get_backtest_trades(
    backtest_id: BacktestId,
    repository: ResearchRepository = Depends(get_research_repository),
) -> dict[str, Any]:
    """Return isolated simulated trade rows for one backtest attempt."""

    items = _invoke(lambda: repository.list_trades(backtest_id))
    return {"backtest_id": backtest_id, "items": _json_safe_value(items)}


@backtests_router.get("/{backtest_id}/attributions")
def get_backtest_attributions(
    backtest_id: BacktestId,
    reason_code: str | None = Query(default=None, min_length=1, max_length=48),
    repository: ResearchRepository = Depends(get_research_repository),
) -> dict[str, Any]:
    """Return structured attribution detail plus aggregates for a single attempt."""

    items = _invoke(lambda: repository.list_attributions(backtest_id, reason_code=reason_code))
    aggregates = _invoke(lambda: repository.aggregate_attributions(backtest_id))
    return {
        "backtest_id": backtest_id,
        "items": _json_safe_value(items),
        "aggregates": _json_safe_value(aggregates),
    }


@experiment_router.post("")
def create_backtest_experiment(
    payload: ExperimentCreateRequest,
    repository: ResearchRepository = Depends(get_research_repository),
) -> dict[str, Any]:
    """Create an immutable experiment plan from a completed baseline attempt."""

    def create() -> tuple[str, Any]:
        session = getattr(repository, "session", None)
        try:
            baseline = repository.get_run(payload.baseline_backtest_id)
            experiment_id = f"btexp_{uuid4().hex}"
            experiment = repository.create_experiment(
                experiment_id=experiment_id,
                hypothesis=payload.hypothesis,
                baseline_backtest_id=payload.baseline_backtest_id,
                snapshot_id=_required_record_value(baseline, "snapshot_id"),
                execution_policy_fingerprint=_required_record_value(
                    baseline, "execution_policy_fingerprint"
                ),
                split_json=payload.splits.to_repository_payload(),
                split_hash=None,
                candidate_strategy_id=payload.candidate_strategy_id,
                candidate_strategy_version=payload.candidate_strategy_version,
                frozen_candidate_fingerprint=payload.frozen_candidate_fingerprint,
                acceptance=payload.acceptance.to_repository_payload(),
            )
            if session is not None:
                session.commit()
            return experiment_id, experiment
        except Exception:
            if session is not None:
                session.rollback()
            raise

    experiment_id, experiment = _invoke(create)
    response = _response_payload(experiment)
    response.setdefault("experiment_id", experiment_id)
    return response


@experiment_router.get("/{experiment_id}")
def get_backtest_experiment(
    experiment_id: ExperimentId,
    repository: ResearchRepository = Depends(get_research_repository),
) -> dict[str, Any]:
    """Return frozen experiment plan, comparison state, and promotion conclusion."""

    return _response_payload(_invoke(lambda: repository.get_experiment(experiment_id)))


# Application composition needs only ``router``; it owns both public URL prefixes.
router.include_router(backtests_router)
router.include_router(experiment_router)


__all__ = [
    "BacktestDataPolicyRequest",
    "BacktestRequestBody",
    "ExecutionPolicyRequest",
    "ExperimentAcceptanceRequest",
    "ExperimentCreateRequest",
    "ExperimentSplitsRequest",
    "GatewayDependencyUnavailable",
    "PreflightBacktestRequest",
    "ReplayBacktestRequest",
    "ResearchBacktestRunner",
    "backtests_router",
    "experiment_router",
    "get_research_backtest_runner",
    "get_research_repository",
    "router",
]
