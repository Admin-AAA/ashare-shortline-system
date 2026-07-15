"""Generic, typed orchestration for frozen Auxiliary Dataset descriptors."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime, timezone
from typing import Any

from app.services.auxiliary.contracts import (
    AuxiliaryAuditEvent,
    AuxiliaryAuditOutcome,
    AuxiliaryDatasetDescriptor,
    AuxiliaryDatasetRequest,
    AuxiliaryDatasetResult,
    AuxiliaryError,
    AuxiliaryErrorCode,
    AuxiliaryInstrument,
    AuxiliaryOrchestratorResult,
    AuxiliaryRepositorySnapshot,
    AuxiliaryStage,
    CoverageResult,
    RefreshDecision,
    RefreshReason,
)
from app.services.auxiliary.registry import (
    AuxiliaryDatasetNotFoundError,
    AuxiliaryDatasetRegistry,
    AuxiliaryRegistryFrozenError,
)


Clock = Callable[[], datetime]


class AuxiliaryDataOrchestrator:
    """Execute ordinary descriptors or explicit custom handlers through one result model."""

    def __init__(self, registry: AuxiliaryDatasetRegistry, *, clock: Clock | None = None) -> None:
        self._registry = registry
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def prepare(
        self,
        *,
        datasets: Sequence[str],
        instruments: Sequence[AuxiliaryInstrument],
        trade_date: date,
        refresh: bool,
    ) -> AuxiliaryOrchestratorResult:
        """Prepare requested datasets and prerequisites without dataset-specific branches."""
        requested_at = self._now()
        try:
            descriptors = self._registry.resolve_with_dependencies(datasets)
        except AuxiliaryRegistryFrozenError as exc:
            return AuxiliaryOrchestratorResult(
                results=(),
                errors=(
                    AuxiliaryError(
                        AuxiliaryErrorCode.REGISTRY_NOT_FROZEN,
                        AuxiliaryStage.REGISTRY,
                        str(exc),
                    ),
                ),
            )
        except AuxiliaryDatasetNotFoundError as exc:
            return AuxiliaryOrchestratorResult(
                results=(),
                errors=(
                    AuxiliaryError(
                        AuxiliaryErrorCode.DATASET_NOT_REGISTERED,
                        AuxiliaryStage.REGISTRY,
                        str(exc),
                    ),
                ),
            )

        results: list[AuxiliaryDatasetResult[Any]] = []
        completed: dict[str, AuxiliaryDatasetResult[Any]] = {}
        immutable_instruments = tuple(instruments)
        for descriptor in descriptors:
            request = AuxiliaryDatasetRequest(
                dataset_key=descriptor.key,
                instruments=immutable_instruments,
                trade_date=trade_date,
                refresh_enabled=refresh,
                requested_at=requested_at,
            )
            unavailable = next(
                (
                    dependency
                    for dependency in descriptor.dependencies
                    if not completed[dependency].is_usable
                ),
                None,
            )
            if unavailable is not None:
                result = self._dependency_unavailable(descriptor, request, unavailable)
            elif descriptor.is_custom:
                result = self._run_custom(descriptor, request)
            else:
                result = self._run_standard(descriptor, request)
            results.append(result)
            completed[descriptor.key] = result
        return AuxiliaryOrchestratorResult(results=tuple(results))

    def _run_custom(
        self,
        descriptor: AuxiliaryDatasetDescriptor[Any, Any, Any],
        request: AuxiliaryDatasetRequest,
    ) -> AuxiliaryDatasetResult[Any]:
        assert descriptor.custom_handler is not None
        dispatched = self._audit(
            AuxiliaryStage.CUSTOM,
            AuxiliaryAuditOutcome.SUCCEEDED,
            {"handler": type(descriptor.custom_handler).__name__, "event": "dispatched"},
        )
        try:
            result = descriptor.custom_handler.prepare(request)
        except Exception as exc:  # noqa: BLE001 - translated to stable shared error model
            return self._failed_result(
                descriptor,
                request,
                stage=AuxiliaryStage.CUSTOM,
                code=AuxiliaryErrorCode.CUSTOM_HANDLER_FAILED,
                message=f"custom handler 执行失败：{exc}",
                audit=(dispatched,),
                exception=exc,
            )
        if not isinstance(result, AuxiliaryDatasetResult):
            return self._failed_result(
                descriptor,
                request,
                stage=AuxiliaryStage.CUSTOM,
                code=AuxiliaryErrorCode.CUSTOM_HANDLER_RESULT_INVALID,
                message="custom handler 必须返回 AuxiliaryDatasetResult",
                audit=(dispatched,),
            )
        if result.dataset_key != descriptor.key or result.view_key != descriptor.view_key:
            return self._failed_result(
                descriptor,
                request,
                stage=AuxiliaryStage.CUSTOM,
                code=AuxiliaryErrorCode.CUSTOM_HANDLER_RESULT_INVALID,
                message="custom handler 返回的 dataset_key 或 view_key 与 descriptor 不一致",
                audit=(dispatched,),
            )
        return AuxiliaryDatasetResult(
            dataset_key=result.dataset_key,
            view_key=result.view_key,
            view=result.view,
            coverage=result.coverage,
            refresh=result.refresh,
            audit=(dispatched, *result.audit),
            errors=result.errors,
            persistence=result.persistence,
        )

    def _run_standard(
        self,
        descriptor: AuxiliaryDatasetDescriptor[Any, Any, Any],
        request: AuxiliaryDatasetRequest,
    ) -> AuxiliaryDatasetResult[Any]:
        assert descriptor.repository is not None
        assert descriptor.fetcher is not None
        assert descriptor.normalizer is not None
        assert descriptor.loader is not None

        audit: list[AuxiliaryAuditEvent] = []
        try:
            snapshot = descriptor.repository.inspect(request)
        except Exception as exc:  # noqa: BLE001 - adapter failures become stable result evidence
            return self._failed_result(
                descriptor,
                request,
                stage=AuxiliaryStage.INSPECT,
                code=AuxiliaryErrorCode.REPOSITORY_INSPECTION_FAILED,
                message=f"repository inspect 失败：{exc}",
                exception=exc,
            )
        cached_coverage = descriptor.coverage_policy.evaluate_snapshot(
            record_count=snapshot.record_count,
            covered_instruments=snapshot.covered_instruments,
            requested_instruments=request.instruments,
            source="repository",
        )
        audit.append(
            self._audit(
                AuxiliaryStage.INSPECT,
                AuxiliaryAuditOutcome.SUCCEEDED,
                {"records": snapshot.record_count, "coverage_sufficient": cached_coverage.sufficient},
            )
        )
        decision = descriptor.refresh_policy.decide(request, snapshot, cached_coverage)
        audit.append(
            self._audit(
                AuxiliaryStage.REFRESH,
                AuxiliaryAuditOutcome.SUCCEEDED if decision.required else AuxiliaryAuditOutcome.SKIPPED,
                {"required": decision.required, "reason": decision.reason.value},
            )
        )

        coverage = cached_coverage
        persistence = None
        if decision.required:
            try:
                raw_records = tuple(descriptor.fetcher.fetch(request))
                audit.append(
                    self._audit(AuxiliaryStage.FETCH, AuxiliaryAuditOutcome.SUCCEEDED, {"records": len(raw_records)})
                )
            except Exception as exc:  # noqa: BLE001
                return self._failed_result(
                    descriptor,
                    request,
                    stage=AuxiliaryStage.FETCH,
                    code=AuxiliaryErrorCode.FETCH_FAILED,
                    message=f"dataset fetch 失败：{exc}",
                    audit=tuple(audit),
                    exception=exc,
                    coverage=coverage,
                    refresh=decision,
                )
            normalized: list[Any] = []
            try:
                for raw in raw_records:
                    record = descriptor.normalizer.normalize(raw, request)
                    if record is not None:
                        normalized.append(record)
                audit.append(
                    self._audit(
                        AuxiliaryStage.NORMALIZE,
                        AuxiliaryAuditOutcome.SUCCEEDED,
                        {"input_records": len(raw_records), "output_records": len(normalized)},
                    )
                )
            except Exception as exc:  # noqa: BLE001
                return self._failed_result(
                    descriptor,
                    request,
                    stage=AuxiliaryStage.NORMALIZE,
                    code=AuxiliaryErrorCode.NORMALIZATION_FAILED,
                    message=f"dataset normalize 失败：{exc}",
                    audit=tuple(audit),
                    exception=exc,
                    coverage=coverage,
                    refresh=decision,
                )
            coverage = descriptor.coverage_policy.evaluate_records(
                normalized,
                request.instruments,
                source="refreshed",
            )
            try:
                persistence = descriptor.repository.upsert(tuple(normalized), request)
                audit.append(
                    self._audit(
                        AuxiliaryStage.PERSIST,
                        AuxiliaryAuditOutcome.SUCCEEDED,
                        {
                            "attempted_rows": persistence.attempted_rows,
                            "affected_rows": persistence.affected_rows,
                            "execute_count": persistence.execute_count,
                        },
                    )
                )
            except Exception as exc:  # noqa: BLE001
                return self._failed_result(
                    descriptor,
                    request,
                    stage=AuxiliaryStage.PERSIST,
                    code=AuxiliaryErrorCode.PERSIST_FAILED,
                    message=f"dataset persist 失败：{exc}",
                    audit=tuple(audit),
                    exception=exc,
                    coverage=coverage,
                    refresh=decision,
                )

        try:
            loaded = descriptor.loader.load(request)
        except Exception as exc:  # noqa: BLE001
            return self._failed_result(
                descriptor,
                request,
                stage=AuxiliaryStage.LOAD,
                code=AuxiliaryErrorCode.LOAD_FAILED,
                message=f"dataset load 失败：{exc}",
                audit=tuple(audit),
                exception=exc,
                coverage=coverage,
                refresh=decision,
                persistence=persistence,
            )
        if loaded.key != descriptor.view_key:
            return self._failed_result(
                descriptor,
                request,
                stage=AuxiliaryStage.LOAD,
                code=AuxiliaryErrorCode.LOAD_FAILED,
                message=f"loader output key '{loaded.key}' 与声明 '{descriptor.view_key}' 不一致",
                audit=tuple(audit),
                coverage=coverage,
                refresh=decision,
                persistence=persistence,
            )
        audit.append(self._audit(AuxiliaryStage.LOAD, AuxiliaryAuditOutcome.SUCCEEDED, {"view_key": loaded.key}))
        errors: tuple[AuxiliaryError, ...] = ()
        if not coverage.sufficient:
            errors = (
                AuxiliaryError(
                    AuxiliaryErrorCode.COVERAGE_INSUFFICIENT,
                    AuxiliaryStage.COVERAGE,
                    "dataset coverage 未达到 descriptor 声明的最低要求",
                ),
            )
            audit.append(
                self._audit(
                    AuxiliaryStage.COVERAGE,
                    AuxiliaryAuditOutcome.FAILED,
                    {"records": coverage.record_count, "ratio": coverage.instrument_coverage_ratio},
                )
            )
        else:
            audit.append(self._audit(AuxiliaryStage.COVERAGE, AuxiliaryAuditOutcome.SUCCEEDED, {"records": coverage.record_count}))
        return AuxiliaryDatasetResult(
            dataset_key=descriptor.key,
            view_key=descriptor.view_key,
            view=loaded.value,
            coverage=coverage,
            refresh=decision,
            audit=tuple(audit),
            errors=errors,
            persistence=persistence,
        )

    def _dependency_unavailable(
        self,
        descriptor: AuxiliaryDatasetDescriptor[Any, Any, Any],
        request: AuxiliaryDatasetRequest,
        dependency: str,
    ) -> AuxiliaryDatasetResult[Any]:
        coverage = descriptor.coverage_policy.evaluate_snapshot(
            record_count=0,
            covered_instruments=(),
            requested_instruments=request.instruments,
            source="dependency_blocked",
        )
        return self._failed_result(
            descriptor,
            request,
            stage=AuxiliaryStage.DEPENDENCY,
            code=AuxiliaryErrorCode.DEPENDENCY_UNAVAILABLE,
            message=f"依赖数据集 '{dependency}' 不可用，当前数据集未执行",
            coverage=coverage,
            refresh=RefreshDecision(False, RefreshReason.REFRESH_DISABLED, None),
        )

    def _failed_result(
        self,
        descriptor: AuxiliaryDatasetDescriptor[Any, Any, Any],
        request: AuxiliaryDatasetRequest,
        *,
        stage: AuxiliaryStage,
        code: AuxiliaryErrorCode,
        message: str,
        audit: tuple[AuxiliaryAuditEvent, ...] = (),
        exception: Exception | None = None,
        coverage: CoverageResult | None = None,
        refresh: RefreshDecision | None = None,
        persistence: Any = None,
    ) -> AuxiliaryDatasetResult[Any]:
        effective_coverage = coverage or descriptor.coverage_policy.evaluate_snapshot(
            record_count=0,
            covered_instruments=(),
            requested_instruments=request.instruments,
            source="failure",
        )
        effective_refresh = refresh or RefreshDecision(False, RefreshReason.REFRESH_DISABLED, None)
        error = AuxiliaryError(
            code=code,
            stage=stage,
            message=message,
            retriable=code
            in {
                AuxiliaryErrorCode.REPOSITORY_INSPECTION_FAILED,
                AuxiliaryErrorCode.FETCH_FAILED,
                AuxiliaryErrorCode.PERSIST_FAILED,
                AuxiliaryErrorCode.LOAD_FAILED,
                AuxiliaryErrorCode.CUSTOM_HANDLER_FAILED,
            },
            exception_type=type(exception).__name__ if exception is not None else None,
        )
        return AuxiliaryDatasetResult(
            dataset_key=descriptor.key,
            view_key=descriptor.view_key,
            view=None,
            coverage=effective_coverage,
            refresh=effective_refresh,
            audit=(*audit, self._audit(stage, AuxiliaryAuditOutcome.FAILED, {"code": code.value})),
            errors=(error,),
            persistence=persistence,
        )

    def _audit(
        self,
        stage: AuxiliaryStage,
        outcome: AuxiliaryAuditOutcome,
        details: dict[str, object],
    ) -> AuxiliaryAuditEvent:
        return AuxiliaryAuditEvent.create(stage, outcome, self._now(), details)

    def _now(self) -> datetime:
        current = self._clock()
        if current.tzinfo is None:
            raise ValueError("AuxiliaryDataOrchestrator clock 必须返回带时区 datetime")
        return current


__all__ = ["AuxiliaryDataOrchestrator", "Clock"]
