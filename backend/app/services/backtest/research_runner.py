"""P0 executable, reproducible single-strategy research backtest runner.

The runner has two strictly separated phases:

* capture reads only local ``daily_kbars``/``stocks`` once and stores content
  addressed signal/outcome artifacts;
* execution and replay read those artifacts through separate reader capabilities.

It never calls the legacy ``run_backtest`` service and never writes
``Recommendation`` or ``RecommendationOutcome``.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, timedelta
import json
import math
from typing import Any, Iterable, Mapping, Sequence
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.engine.backtest import (
    ArtifactRef,
    ArtifactScope,
    BacktestDataPolicy,
    BacktestDomainError,
    BacktestErrorCode,
    BacktestRequest,
    BacktestStatus,
    BacktestValidationError,
    BarInput,
    CoverageResult,
    CoverageStatus,
    DataGap,
    DataSnapshot,
    DateRange,
    EvidenceRef,
    ExecutionPolicy,
    FailureAttribution,
    OutcomeInput,
    PartitionManifest,
    RecommendationInput,
    SignalInput,
    SignalInputRequirement,
    SingleDate,
    SnapshotArtifactUnavailableError,
    Trade,
    canonical_fingerprint,
    canonical_json,
)
from app.engine.simulation import attribute_failures, build_equity_curve, compute_metrics, simulate
from app.engine.skill_loader import load_skills
from app.engine.strategy import (
    Strategy,
    StrategyContext,
    StrategyDescriptor,
    StrategyError,
    StrategyRegistry,
    build_registry_from_skills,
)
from app.models.tables import BacktestInputArtifact, DailyKbar, Stock
from app.services.backtest.research_repository import (
    ResearchBacktestRepository,
    ResearchRecordNotFound,
    ResearchRepositoryError,
)


RUNNER_EXECUTION_VERSION = "research-backtest-runner/v1"
MINIMUM_SIGNAL_SCORE = 70.0
MAX_SIGNAL_DATES = 250
LOOKBACK_CALENDAR_DAYS = 420


class ResearchBacktestRunnerError(RuntimeError):
    """Stable runner error that the Gateway can safely project."""

    code = "research_backtest_runner_error"

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = dict(details or {})


class StrategyUnavailableError(ResearchBacktestRunnerError):
    code = "strategy_unavailable"


class SnapshotCaptureError(ResearchBacktestRunnerError):
    code = "research_snapshot_capture_failed"


@dataclass(frozen=True)
class SnapshotBundle:
    """A captured or rehydrated immutable snapshot plus its binary payloads."""

    snapshot: DataSnapshot
    payloads_by_artifact_id: Mapping[str, bytes]

    @property
    def signal_dates(self) -> tuple[str, ...]:
        return tuple(sorted(self.snapshot.artifacts_by_signal_date))

    def artifact_for(self, signal_date: str, scope: ArtifactScope) -> ArtifactRef:
        refs = self.snapshot.artifacts_for(signal_date, scope)
        if len(refs) != 1:
            raise SnapshotArtifactUnavailableError(f"{signal_date}:{scope.value}")
        return refs[0]


class ArtifactSignalDateDataReader:
    """Signal-side artifact capability; it never queries mutable market tables."""

    def __init__(self, repository: ResearchBacktestRepository, artifact: ArtifactRef) -> None:
        self._repository = repository
        self._artifact = artifact

    def read(self, signal_date: str, requirement: SignalInputRequirement) -> SignalInput:
        del requirement
        if signal_date != self._artifact.signal_date:
            raise SnapshotArtifactUnavailableError(self._artifact.artifact_id)
        row = self._repository.get_artifact(
            self._artifact.artifact_id,
            expected_content_hash=self._artifact.content_hash,
        )
        payload = _decode_artifact_payload(bytes(row.payload), self._artifact.artifact_id)
        visible_at = payload.get("visible_at")
        stocks = payload.get("stocks")
        if not isinstance(visible_at, str) or not isinstance(stocks, list):
            raise SnapshotCaptureError(
                "signal artifact payload is malformed",
                details={"artifact_id": self._artifact.artifact_id},
            )
        return SignalInput(
            signal_date=signal_date,
            visible_at=visible_at,
            payload={"stocks": stocks, "artifact_schema_version": payload.get("schema_version", 1)},
            artifact_refs=(self._artifact.artifact_id,),
        )


class ArtifactOutcomeDataReader:
    """Outcome-side artifact capability restricted to one candidate code."""

    def __init__(
        self,
        repository: ResearchBacktestRepository,
        artifact: ArtifactRef,
        *,
        code: str,
    ) -> None:
        self._repository = repository
        self._artifact = artifact
        self._code = code

    def read(self, signal_date: str, policy: ExecutionPolicy) -> OutcomeInput:
        del policy
        if signal_date != self._artifact.signal_date:
            raise SnapshotArtifactUnavailableError(self._artifact.artifact_id)
        row = self._repository.get_artifact(
            self._artifact.artifact_id,
            expected_content_hash=self._artifact.content_hash,
        )
        payload = _decode_artifact_payload(bytes(row.payload), self._artifact.artifact_id)
        bars_by_code = payload.get("bars_by_code")
        if not isinstance(bars_by_code, Mapping):
            raise SnapshotCaptureError(
                "outcome artifact payload is malformed",
                details={"artifact_id": self._artifact.artifact_id},
            )
        raw_bars = bars_by_code.get(self._code, [])
        if not isinstance(raw_bars, list):
            raise SnapshotCaptureError(
                "outcome bars must be a list",
                details={"artifact_id": self._artifact.artifact_id, "code": self._code},
            )
        bars = tuple(BarInput(**item) for item in raw_bars if isinstance(item, Mapping))
        return OutcomeInput(
            signal_date=signal_date,
            bars=bars,
            artifact_refs=(self._artifact.artifact_id,),
        )


class LocalResearchSnapshotBuilder:
    """Capture local daily-bar inputs once, with no refresh/network capability."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def capture(self, request: BacktestRequest) -> SnapshotBundle:
        dates = self._signal_dates(request)
        artifact_namespace = canonical_fingerprint(
            {
                "date_selector": request.date_selector,
                "target_codes": request.target_codes,
                "execution_policy": request.execution_policy,
                "data_policy": request.data_policy,
            }
        )[:20]
        if len(dates) > MAX_SIGNAL_DATES:
            raise SnapshotCaptureError(
                "research request exceeds the maximum reproducible signal-date window",
                details={"max_signal_dates": MAX_SIGNAL_DATES, "actual_signal_dates": len(dates)},
            )
        if not dates:
            return self._empty_bundle(request)

        first_date = date.fromisoformat(dates[0])
        last_date = date.fromisoformat(dates[-1])
        history_start = (first_date - timedelta(days=LOOKBACK_CALENDAR_DAYS)).isoformat()
        outcome_end = (
            last_date
            + timedelta(days=request.execution_policy.entry_lookahead_days + request.execution_policy.horizon_days + 14)
        ).isoformat()

        signal_rows = list(
            self._session.scalars(
                select(DailyKbar)
                .where(DailyKbar.trade_date.in_(dates))
                .order_by(DailyKbar.trade_date, DailyKbar.code)
            )
        )
        if request.target_codes:
            requested_codes = set(request.target_codes)
            signal_rows = [row for row in signal_rows if row.code in requested_codes]
        codes = tuple(sorted({str(row.code) for row in signal_rows}))
        if not codes:
            return self._empty_bundle(request)

        all_rows = list(
            self._session.scalars(
                select(DailyKbar)
                .where(
                    DailyKbar.code.in_(codes),
                    DailyKbar.trade_date >= history_start,
                    DailyKbar.trade_date <= outcome_end,
                )
                .order_by(DailyKbar.code, DailyKbar.trade_date)
            )
        )
        stock_rows = list(self._session.scalars(select(Stock).where(Stock.code.in_(codes))))
        stock_meta = {
            str(row.code): {
                "name": str(row.name or row.code),
                "industry": str(row.industry or "未分类"),
                "is_st": bool(row.is_st),
            }
            for row in stock_rows
        }
        rows_by_code: dict[str, list[DailyKbar]] = defaultdict(list)
        for row in all_rows:
            rows_by_code[str(row.code)].append(row)
        rows_at_date: dict[str, list[DailyKbar]] = defaultdict(list)
        for row in signal_rows:
            rows_at_date[str(row.trade_date)].append(row)

        artifact_refs: dict[str, tuple[ArtifactRef, ...]] = {}
        payloads: dict[str, bytes] = {}
        coverage: dict[str, CoverageResult] = {}
        gaps: list[DataGap] = []
        for signal_date in dates:
            raw_signal_rows = rows_at_date.get(signal_date, ())
            signal_stocks: list[dict[str, Any]] = []
            invalid_signal_rows = 0
            for row in raw_signal_rows:
                try:
                    signal_stocks.append(
                        _stock_as_of(
                            row,
                            rows_by_code.get(str(row.code), ()),
                            stock_meta.get(str(row.code), {}),
                            signal_date,
                        )
                    )
                except BacktestValidationError:
                    # A non-positive OHLC value cannot cross the pure simulation
                    # boundary. Keep it out of the strategy artifact and record
                    # the loss of coverage explicitly below.
                    invalid_signal_rows += 1
            signal_payload = {
                "schema_version": 1,
                "signal_date": signal_date,
                "visible_at": signal_date,
                "stocks": signal_stocks,
            }
            outcome_payload = {
                "schema_version": 1,
                "signal_date": signal_date,
                "bars_by_code": {
                    stock["code"]: _outcome_bars(
                        rows_by_code.get(str(stock["code"]), ()),
                        signal_date,
                        request.execution_policy,
                    )
                    for stock in signal_stocks
                },
            }
            signal_bytes = canonical_json(signal_payload).encode("utf-8")
            outcome_bytes = canonical_json(outcome_payload).encode("utf-8")
            signal_ref = ArtifactRef.from_content(
                artifact_id=_artifact_id("signal", signal_date, signal_bytes, namespace=artifact_namespace),
                signal_date=signal_date,
                artifact_scope=ArtifactScope.SIGNAL_INPUT,
                artifact_type="daily_kbars_strategy_input",
                schema_version=1,
                row_count=len(signal_stocks),
                payload=signal_bytes,
            )
            outcome_bar_count = sum(len(bars) for bars in outcome_payload["bars_by_code"].values())
            outcome_ref = ArtifactRef.from_content(
                artifact_id=_artifact_id("outcome", signal_date, outcome_bytes, namespace=artifact_namespace),
                signal_date=signal_date,
                artifact_scope=ArtifactScope.OUTCOME_INPUT,
                artifact_type="daily_kbars_execution_input",
                schema_version=1,
                row_count=outcome_bar_count,
                payload=outcome_bytes,
            )
            artifact_refs[signal_date] = (signal_ref, outcome_ref)
            payloads[signal_ref.artifact_id] = signal_bytes
            payloads[outcome_ref.artifact_id] = outcome_bytes

            has_future_bars = outcome_bar_count > 0
            if signal_stocks and has_future_bars and invalid_signal_rows == 0:
                coverage[signal_date] = CoverageResult(signal_date, CoverageStatus.READY, 1.0)
            else:
                if not signal_stocks:
                    status = CoverageStatus.INSUFFICIENT_DATA
                    error_code = BacktestErrorCode.INSUFFICIENT_DATA
                    requirement = "daily_kbars_signal_input"
                    detail = "no valid local daily bars on signal date"
                elif invalid_signal_rows:
                    status = CoverageStatus.INCOMPLETE_EVALUATION
                    error_code = BacktestErrorCode.INCOMPLETE_EVALUATION
                    requirement = "daily_kbars_signal_input"
                    detail = f"{invalid_signal_rows} local daily-bar rows have invalid non-positive OHLC values"
                else:
                    status = CoverageStatus.INCOMPLETE_EVALUATION
                    error_code = BacktestErrorCode.INCOMPLETE_EVALUATION
                    requirement = "daily_kbars_outcome_input"
                    detail = "no future local daily bars for execution"
                gap = DataGap(signal_date, requirement, error_code, detail)
                gaps.append(gap)
                total_signal_rows = len(raw_signal_rows)
                ratio = len(signal_stocks) / total_signal_rows if total_signal_rows else 0.0
                coverage[signal_date] = CoverageResult(signal_date, status, ratio, (gap,))

        partition_payload = {
            "dataset": "daily_kbars",
            "history_start": history_start,
            "outcome_end": outcome_end,
            "signal_dates": dates,
            "artifact_hashes": {
                signal_date: [ref.content_hash for ref in refs]
                for signal_date, refs in artifact_refs.items()
            },
            # The repository's immutable snapshot ledger keys partition manifests.
            # Include artifact identities as well as their payload hashes so two
            # distinct frozen request scopes never alias one snapshot row.
            "artifact_ids": {
                signal_date: [ref.artifact_id for ref in refs]
                for signal_date, refs in artifact_refs.items()
            },
        }
        partition = PartitionManifest(
            dataset="daily_kbars",
            query_range=DateRange(history_start, outcome_end),
            source_version="local-daily-kbars/v1",
            row_count=len(all_rows),
            content_hash=canonical_fingerprint(partition_payload),
        )
        provisional = DataSnapshot(
            snapshot_id="pending",
            snapshot_version=1,
            partitions=(partition,),
            artifacts_by_signal_date=artifact_refs,
            coverage_by_signal_date=coverage,
            missing_requirements=tuple(gaps),
        )
        snapshot = DataSnapshot(
            snapshot_id=f"bts_{provisional.input_manifest_hash}",
            snapshot_version=1,
            partitions=(partition,),
            artifacts_by_signal_date=artifact_refs,
            coverage_by_signal_date=coverage,
            missing_requirements=tuple(gaps),
            input_manifest_hash=provisional.input_manifest_hash,
        )
        return SnapshotBundle(snapshot=snapshot, payloads_by_artifact_id=payloads)

    def _signal_dates(self, request: BacktestRequest) -> tuple[str, ...]:
        selector = request.date_selector
        if isinstance(selector, SingleDate):
            return (selector.signal_date,)
        rows = self._session.execute(
            select(DailyKbar.trade_date)
            .where(DailyKbar.trade_date >= selector.start_date, DailyKbar.trade_date <= selector.end_date)
            .group_by(DailyKbar.trade_date)
            .order_by(DailyKbar.trade_date)
        ).all()
        return tuple(str(row[0]) for row in rows)

    def _empty_bundle(self, request: BacktestRequest) -> SnapshotBundle:
        selector = request.date_selector
        signal_date = selector.signal_date if isinstance(selector, SingleDate) else selector.start_date
        gap = DataGap(
            signal_date,
            "daily_kbars_signal_input",
            BacktestErrorCode.INSUFFICIENT_DATA,
            "no local daily bars match the requested signal date selector",
        )
        coverage = {signal_date: CoverageResult(signal_date, CoverageStatus.INSUFFICIENT_DATA, 0.0, (gap,))}
        partition = PartitionManifest(
            dataset="daily_kbars",
            query_range=DateRange(selector.start_date, selector.end_date) if isinstance(selector, DateRange) else DateRange(signal_date, signal_date),
            source_version="local-daily-kbars/v1",
            row_count=0,
            content_hash=canonical_fingerprint({"selector": selector}),
        )
        provisional = DataSnapshot(
            snapshot_id="pending",
            snapshot_version=1,
            partitions=(partition,),
            coverage_by_signal_date=coverage,
            missing_requirements=(gap,),
        )
        return SnapshotBundle(
            snapshot=DataSnapshot(
                snapshot_id=f"bts_{provisional.input_manifest_hash}",
                snapshot_version=1,
                partitions=(partition,),
                coverage_by_signal_date=coverage,
                missing_requirements=(gap,),
                input_manifest_hash=provisional.input_manifest_hash,
            ),
            payloads_by_artifact_id={},
        )


class ResearchBacktestRunner:
    """Orchestrates one isolated P0 attempt from immutable strategy/data contracts."""

    def __init__(
        self,
        repository: ResearchBacktestRepository,
        *,
        session: Session | None = None,
        strategy_registry: StrategyRegistry | None = None,
        snapshot_builder: LocalResearchSnapshotBuilder | None = None,
    ) -> None:
        self._repository = repository
        self._session = session or repository.session
        self._strategy_registry = strategy_registry
        self._snapshot_builder = snapshot_builder or LocalResearchSnapshotBuilder(self._session)

    def preflight(self, request: BacktestRequest) -> dict[str, Any]:
        descriptor, _strategy = self._resolve_strategy(request)
        bundle = self._snapshot_builder.capture(request)
        return {
            "status": "ready" if _snapshot_ready(bundle.snapshot) else "insufficient_data",
            "strategy_id": descriptor.strategy_id,
            "strategy_version": descriptor.effective_version,
            "snapshot_id": bundle.snapshot.snapshot_id,
            "signal_dates": list(bundle.signal_dates),
            "coverage": {
                signal_date: result.canonical_payload()
                for signal_date, result in bundle.snapshot.coverage_by_signal_date.items()
            },
            "missing_requirements": [gap.canonical_payload() for gap in bundle.snapshot.missing_requirements],
            "local_only": True,
        }

    def run(self, request: BacktestRequest) -> dict[str, Any]:
        descriptor, strategy = self._resolve_strategy(request)
        resolved_request = replace(request, strategy_version=descriptor.effective_version)
        bundle = self._snapshot_builder.capture(resolved_request)
        return self._execute(resolved_request, descriptor, strategy, bundle)

    def replay(self, backtest_id: str, *, force_rerun: bool = True) -> dict[str, Any]:
        if not force_rerun:
            raise BacktestValidationError(
                BacktestErrorCode.INVALID_REQUEST,
                "replay must request force_rerun=true",
            )
        source = self._repository.get_run(backtest_id)
        request = _request_from_canonical_json(str(source.request_json), force_rerun=True)
        resolved_request = replace(request, strategy_version=str(source.strategy_version), force_rerun=True)
        descriptor, strategy = self._resolve_strategy(resolved_request)
        if descriptor.effective_version != str(source.strategy_version):
            raise StrategyUnavailableError(
                "the exact strategy version required by this immutable replay is not installed",
                details={
                    "strategy_id": source.strategy_id,
                    "required_version": source.strategy_version,
                    "available_version": descriptor.effective_version,
                },
            )
        bundle = self._load_snapshot_bundle(str(source.snapshot_id))
        return self._execute(resolved_request, descriptor, strategy, bundle)

    def _execute(
        self,
        request: BacktestRequest,
        descriptor: StrategyDescriptor,
        strategy: Strategy,
        bundle: SnapshotBundle,
    ) -> dict[str, Any]:
        self._persist_bundle(bundle)
        execution_version = f"{RUNNER_EXECUTION_VERSION}:{descriptor.effective_version[:24]}"
        request_fingerprint = request.request_fingerprint(
            snapshot_id=bundle.snapshot.snapshot_id,
            effective_strategy_version=descriptor.effective_version,
            execution_version=execution_version,
        )
        signal_date, date_from, date_to = _selector_dates(request)
        claim = self._repository.claim_request(
            request_fingerprint=request_fingerprint,
            backtest_id=f"btr_{uuid4().hex}",
            snapshot_id=bundle.snapshot.snapshot_id,
            strategy_id=descriptor.strategy_id,
            strategy_version=descriptor.effective_version,
            request_schema_version=request.schema_version,
            request_json=json.loads(request.to_canonical_json()),
            execution_version=execution_version,
            execution_policy_fingerprint=request.execution_policy.fingerprint(),
            signal_date=signal_date,
            date_from=date_from,
            date_to=date_to,
            experiment_id=request.experiment_id,
            force_rerun=request.force_rerun,
        )
        self._session.commit()
        if claim.reused:
            return _project_run(self._repository.get_run(claim.backtest_id), reused=True, reuse_reason=claim.reuse_reason)

        backtest_id = claim.backtest_id
        try:
            self._repository.mark_running(backtest_id)
            self._session.commit()
            if not _snapshot_ready(bundle.snapshot):
                result = _insufficient_result(bundle.snapshot, descriptor, execution_version)
                self._repository.fail_run(
                    backtest_id,
                    outcome_status=_snapshot_status(bundle.snapshot),
                    result=result,
                )
                self._session.commit()
                return _project_run(self._repository.get_run(backtest_id), reused=False, reuse_reason=None)

            result = self._evaluate_snapshot(
                backtest_id=backtest_id,
                request=request,
                descriptor=descriptor,
                strategy=strategy,
                bundle=bundle,
                request_fingerprint=request_fingerprint,
                execution_version=execution_version,
            )
            self._repository.complete_run(backtest_id, result=result)
            self._session.commit()
            return _project_run(self._repository.get_run(backtest_id), reused=False, reuse_reason=None)
        except Exception as exc:
            self._session.rollback()
            try:
                self._repository.fail_run(
                    backtest_id,
                    outcome_status="failed",
                    result={
                        "status": "failed",
                        "error_code": _error_code(exc),
                        "message": str(exc),
                        "snapshot_id": bundle.snapshot.snapshot_id,
                    },
                )
                self._session.commit()
            except Exception:
                self._session.rollback()
            raise

    def _evaluate_snapshot(
        self,
        *,
        backtest_id: str,
        request: BacktestRequest,
        descriptor: StrategyDescriptor,
        strategy: Strategy,
        bundle: SnapshotBundle,
        request_fingerprint: str,
        execution_version: str,
    ) -> dict[str, Any]:
        candidate_rows: list[dict[str, Any]] = []
        persisted_trades: list[Trade] = []
        attribution_rows: list[dict[str, Any]] = []
        signal_dates = bundle.signal_dates
        minimum_signal_score = _minimum_signal_score(request)
        for signal_date in signal_dates:
            signal_ref = bundle.artifact_for(signal_date, ArtifactScope.SIGNAL_INPUT)
            outcome_ref = bundle.artifact_for(signal_date, ArtifactScope.OUTCOME_INPUT)
            signal_input = ArtifactSignalDateDataReader(self._repository, signal_ref).read(
                signal_date,
                SignalInputRequirement(lookback_bars=0, required_datasets=("daily_kbars",)),
            )
            stocks = signal_input.payload["stocks"]
            if not isinstance(stocks, Sequence):
                raise SnapshotCaptureError("signal artifact stocks must be a sequence")
            for raw_stock in stocks:
                if not isinstance(raw_stock, Mapping):
                    continue
                stock = dict(raw_stock)
                code = str(stock.get("code") or "")
                if not code:
                    continue
                try:
                    signal = dict(strategy.evaluate(stock, StrategyContext()))
                except Exception as exc:  # preserve individual evidence without promoting it to a trade
                    candidate_rows.append(
                        {
                            "signal_date": signal_date,
                            "code": code,
                            "decision_status": "strategy_error",
                            "signal_json": None,
                            "audit_json": {
                                "error_code": "strategy_evaluation_error",
                                "error_type": type(exc).__name__,
                                "strategy_version": descriptor.effective_version,
                            },
                            "input_artifact_id": signal_ref.artifact_id,
                        }
                    )
                    continue
                actionable = _is_actionable_signal(signal, minimum_score=minimum_signal_score)
                candidate_rows.append(
                    {
                        "signal_date": signal_date,
                        "code": code,
                        "decision_status": "signal" if actionable else ("vetoed" if signal.get("hard_veto") else "no_signal"),
                        "signal_json": signal,
                        "audit_json": {
                            "score": signal.get("score", 0),
                            "minimum_signal_score": minimum_signal_score,
                            "strategy_version": descriptor.effective_version,
                            "selected": actionable,
                        },
                        "input_artifact_id": signal_ref.artifact_id,
                    }
                )
                if not actionable:
                    continue
                outcome = ArtifactOutcomeDataReader(
                    self._repository,
                    outcome_ref,
                    code=code,
                ).read(signal_date, request.execution_policy)
                trade = simulate(
                    RecommendationInput(
                        code=code,
                        signal_date=signal_date,
                        signal_price=_positive_number(stock.get("price") or stock.get("close"), "signal_price"),
                    ),
                    outcome.bars,
                    request.execution_policy,
                ).trade
                trade = replace(trade, trade_id=_trade_id(backtest_id, trade))
                persisted_trades.append(trade)
                for attribution in attribute_failures((trade,)):
                    attribution = replace(
                        attribution,
                        trade_id=trade.trade_id or "",
                        evidence_references=(
                            EvidenceRef(signal_ref.artifact_id),
                            EvidenceRef(outcome_ref.artifact_id),
                        ),
                    )
                    attribution_rows.append(
                        {
                            "trade_id": attribution.trade_id,
                            "reason_code": attribution.reason_code.value,
                            "rule_or_factor_reference": attribution.rule_or_factor_reference,
                            "evidence_references": [item.canonical_payload() for item in attribution.evidence_references],
                            "confidence": attribution.confidence,
                            "is_unknown": attribution.unknown,
                        }
                    )

        self._repository.persist_candidates(backtest_id, candidate_rows)
        self._repository.persist_trades(
            backtest_id,
            [
                {
                    "trade_id": trade.trade_id,
                    "signal_date": trade.signal_date,
                    "code": trade.code,
                    "entry_date": trade.entry_date,
                    "exit_date": trade.exit_date,
                    "outcome_label": trade.outcome_label.value,
                    "trade_json": trade.canonical_payload(),
                }
                for trade in persisted_trades
            ],
        )
        self._repository.persist_attributions(backtest_id, attribution_rows)
        metrics = compute_metrics(persisted_trades)
        equity_curve = build_equity_curve(persisted_trades)
        return {
            "schema_version": 1,
            "status": BacktestStatus.COMPLETED.value,
            "backtest_id": backtest_id,
            "request_fingerprint": request_fingerprint,
            "snapshot_id": bundle.snapshot.snapshot_id,
            "strategy_id": descriptor.strategy_id,
            "strategy_version": descriptor.effective_version,
            "execution_version": execution_version,
            "execution_policy_fingerprint": request.execution_policy.fingerprint(),
            "signal_dates": list(signal_dates),
            "diagnostic_scope": request.diagnostic_scope,
            "candidate_count": len(candidate_rows),
            "trade_count": len(persisted_trades),
            "metrics": metrics.canonical_payload(),
            "equity_curve": [point.canonical_payload() for point in equity_curve],
            "failure_attribution_count": len(attribution_rows),
            "artifact_refs": {
                signal_date: [ref.canonical_payload() for ref in bundle.snapshot.artifacts_for(signal_date)]
                for signal_date in signal_dates
            },
        }

    def _persist_bundle(self, bundle: SnapshotBundle) -> None:
        snapshot = bundle.snapshot
        self._repository.persist_snapshot(
            snapshot_id=snapshot.snapshot_id,
            snapshot_version=snapshot.snapshot_version,
            partition_manifest=[partition.canonical_payload() for partition in snapshot.partitions],
            coverage={
                signal_date: result.canonical_payload()
                for signal_date, result in snapshot.coverage_by_signal_date.items()
            },
            missing_requirements=[gap.canonical_payload() for gap in snapshot.missing_requirements],
        )
        for refs in snapshot.artifacts_by_signal_date.values():
            for ref in refs:
                payload = bundle.payloads_by_artifact_id.get(ref.artifact_id)
                if payload is None:
                    row = self._repository.get_artifact(ref.artifact_id, expected_content_hash=ref.content_hash)
                    payload = bytes(row.payload)
                self._repository.persist_artifact(
                    artifact_id=ref.artifact_id,
                    snapshot_id=snapshot.snapshot_id,
                    signal_date=ref.signal_date,
                    artifact_scope=ref.artifact_scope.value,
                    artifact_type=ref.artifact_type,
                    schema_version=ref.schema_version,
                    row_count=ref.row_count,
                    content_encoding=ref.content_encoding,
                    payload=payload,
                    content_hash=ref.content_hash,
                )
        self._session.commit()

    def _load_snapshot_bundle(self, snapshot_id: str) -> SnapshotBundle:
        row = self._repository.get_snapshot(snapshot_id)
        try:
            partitions_payload = json.loads(str(row.partition_manifest_json))
            coverage_payload = json.loads(str(row.coverage_json))
            gaps_payload = json.loads(str(row.missing_requirements_json))
        except json.JSONDecodeError as exc:
            raise SnapshotCaptureError("stored snapshot JSON is malformed", details={"snapshot_id": snapshot_id}) from exc
        artifacts_by_date: dict[str, list[ArtifactRef]] = defaultdict(list)
        payloads: dict[str, bytes] = {}
        for artifact in self._session.scalars(
            select(BacktestInputArtifact)
            .where(BacktestInputArtifact.snapshot_id == snapshot_id)
            .order_by(
                BacktestInputArtifact.signal_date,
                BacktestInputArtifact.artifact_scope,
                BacktestInputArtifact.artifact_type,
            )
        ):
            artifact = self._repository.get_artifact(str(artifact.artifact_id))
            ref = ArtifactRef(
                artifact_id=str(artifact.artifact_id),
                signal_date=str(artifact.signal_date),
                artifact_scope=str(artifact.artifact_scope),
                artifact_type=str(artifact.artifact_type),
                schema_version=int(artifact.schema_version),
                row_count=int(artifact.row_count),
                content_hash=str(artifact.content_hash),
                content_encoding=str(artifact.content_encoding),
            )
            artifacts_by_date[ref.signal_date].append(ref)
            payloads[ref.artifact_id] = bytes(artifact.payload)
        partitions = tuple(_partition_from_payload(value) for value in partitions_payload)
        coverage = {
            str(day): _coverage_from_payload(value)
            for day, value in coverage_payload.items()
        }
        gaps = tuple(_gap_from_payload(value) for value in gaps_payload)
        snapshot = DataSnapshot(
            snapshot_id=snapshot_id,
            snapshot_version=int(row.snapshot_version),
            partitions=partitions,
            artifacts_by_signal_date={day: tuple(refs) for day, refs in artifacts_by_date.items()},
            coverage_by_signal_date=coverage,
            missing_requirements=gaps,
        )
        return SnapshotBundle(snapshot=snapshot, payloads_by_artifact_id=payloads)

    def _resolve_strategy(self, request: BacktestRequest) -> tuple[StrategyDescriptor, Strategy]:
        registry = self._strategy_registry or self._build_default_registry(request.strategy_id)
        try:
            return registry.resolve(request.strategy_id, effective_version=request.strategy_version)
        except StrategyError as exc:
            raise StrategyUnavailableError(
                "requested strategy is not available for a reproducible local research run",
                details={"strategy_id": request.strategy_id, "requested_version": request.strategy_version},
            ) from exc

    @staticmethod
    def _build_default_registry(strategy_id: str) -> StrategyRegistry:
        skills = [
            skill
            for skill in load_skills(get_settings().skills_dir)
            if skill.skill_id == strategy_id and skill.enabled and skill.executable and skill.contract is not None
        ]
        if not skills:
            raise StrategyUnavailableError(
                "strategy is not an enabled declarative strategy",
                details={"strategy_id": strategy_id},
            )
        return build_registry_from_skills(skills)


def _stock_as_of(
    row: DailyKbar,
    history: Sequence[DailyKbar],
    metadata: Mapping[str, Any],
    signal_date: str,
) -> dict[str, Any]:
    prior = [item for item in history if str(item.trade_date) <= signal_date]
    close = _positive_number(row.close, "close")
    closes = [_positive_number(item.close, "close") for item in prior if _number(item.close) > 0]
    highs = [_positive_number(item.high, "high") for item in prior if _number(item.high) > 0]
    lows = [_positive_number(item.low, "low") for item in prior if _number(item.low) > 0]
    volumes = [max(0.0, _number(item.volume)) for item in prior]
    lookback20 = closes[-20:]
    lookback60 = closes[-60:]
    recent_high20 = max(highs[-20:] or [close])
    recent_low20 = min(lows[-20:] or [close])
    recent_high60 = max(highs[-60:] or [close])
    recent_low60 = min(lows[-60:] or [close])
    ma5 = _number(row.ma5) or _mean(closes[-5:])
    ma10 = _number(row.ma10) or _mean(closes[-10:])
    ma20 = _number(row.ma20) or _mean(lookback20)
    ma60 = _number(row.ma60) or _mean(lookback60)
    volume5 = _mean(volumes[-5:])
    volume20 = _mean(volumes[-20:])
    vol5_20 = volume5 / volume20 if volume20 > 0 else 0.0
    pct = _number(row.pct_chg)
    amount_yi = _number(row.amount) / 100_000_000
    turnover = _number(row.turnover_rate)
    pos20 = _position(close, recent_low20, recent_high20)
    pos60 = _position(close, recent_low60, recent_high60)
    dist20 = _pct_delta(close, ma20)
    dist60 = _pct_delta(close, ma60)
    chg20 = _pct_delta(close, lookback20[0]) if lookback20 else 0.0
    chg60 = _pct_delta(close, lookback60[0]) if lookback60 else 0.0
    dsa_trend = 10 * int(close >= ma20) + 10 * int(ma5 >= ma10) + 10 * int(ma10 >= ma20)
    dsa_bias = 14 if -2.0 <= _pct_delta(close, ma5) <= 5.0 else 6
    dsa_volume = min(15, int(max(0.0, vol5_20) * 5))
    dsa_support = 10 if close >= ma5 * 0.985 else 3
    dsa_macd = 10 if _number(row.macd_dif) >= _number(row.macd_dea) else 2
    rsi = _number(row.rsi_12)
    dsa_rsi = 10 if 40 <= rsi <= 70 else 4
    max_dd20 = _max_drawdown_pct(closes[-20:])
    chip_concentration = _chip_concentration(row, close)
    stock = {
        "code": str(row.code),
        "name": str(metadata.get("name") or row.code),
        "industry": str(metadata.get("industry") or "未分类"),
        "is_st": bool(metadata.get("is_st", False)),
        "trade_date": signal_date,
        "price": close,
        "close": close,
        "open": _number(row.open),
        "high": _number(row.high),
        "low": _number(row.low),
        "pct": pct,
        "amount_yi": amount_yi,
        "turnover_rate": turnover,
        "volume": _number(row.volume),
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "ma60": ma60,
        "rsi_12": rsi,
        "macd_dif": _number(row.macd_dif),
        "macd_dea": _number(row.macd_dea),
        "breakout20": int(bool(row.breakout20) or close >= recent_high20),
        "breakout60": int(bool(row.breakout60) or close >= recent_high60),
        "max_dd20": max_dd20,
        "chip_concentration": chip_concentration,
        "pos20": pos20,
        "pos60": pos60,
        "dist20": dist20,
        "dist60": dist60,
        "off_high_pct": _pct_delta(close, recent_high60),
        "chg20": chg20,
        "chg60": chg60,
        "vol5_20": vol5_20,
        "support_ma5": close >= ma5 * 0.985,
        "support_ma10": close >= ma10 * 0.985,
        "volume_shrink_pullback": vol5_20 < 0.9 and pct <= 0,
        "volume_breakout_signal": vol5_20 >= 1.1 and close >= recent_high20,
        "resistance_distance_pct": _pct_delta(recent_high20, close),
        "limit_up_days20": sum(1 for item in prior[-20:] if _number(item.pct_chg) >= 9.5),
        "dsa_trend_score": float(dsa_trend),
        "dsa_bias_score": float(dsa_bias),
        "dsa_volume_score": float(dsa_volume),
        "dsa_support_score": float(dsa_support),
        "dsa_macd_score": float(dsa_macd),
        "dsa_rsi_score": float(dsa_rsi),
        "dsa_signal_score": float(dsa_trend + dsa_bias + dsa_volume + dsa_support + dsa_macd + dsa_rsi),
        "market_regime": "震荡",
        "twenty_cm_risk_gate": "normal",
        "news_risk_count": 0,
        "announcement_negative_count": 0,
        "limit_up_overheat_score": 0.0,
        "capital_resonance_score": 0.0,
        "sector_ladder_completeness_score": 0.0,
        "is_main_theme": False,
        "market_sample_count": len(prior),
        "data_completeness_score": 100.0,
        "evidence_quality_score": 100.0,
    }
    return stock


def _outcome_bars(
    history: Sequence[DailyKbar],
    signal_date: str,
    policy: ExecutionPolicy,
) -> list[dict[str, Any]]:
    max_bars = policy.entry_lookahead_days + policy.horizon_days + 3
    results: list[dict[str, Any]] = []
    for row in history:
        if str(row.trade_date) <= signal_date:
            continue
        values = (_number(row.open), _number(row.high), _number(row.low), _number(row.close))
        if any(value <= 0 for value in values):
            continue
        results.append(
            {
                "code": str(row.code),
                "trade_date": str(row.trade_date),
                "open": values[0],
                "high": values[1],
                "low": values[2],
                "close": values[3],
                "volume": max(0.0, _number(row.volume)),
                "tradable": _number(row.volume) > 0,
            }
        )
        if len(results) >= max_bars:
            break
    return results


def _request_from_canonical_json(raw: str, *, force_rerun: bool) -> BacktestRequest:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SnapshotCaptureError("stored request_json is malformed") from exc
    selector = payload.get("date_selector")
    if not isinstance(selector, Mapping):
        raise SnapshotCaptureError("stored request has no valid date_selector")
    if "signal_date" in selector:
        date_selector = SingleDate(str(selector["signal_date"]))
    else:
        date_selector = DateRange(str(selector.get("start_date") or ""), str(selector.get("end_date") or ""))
    return BacktestRequest(
        schema_version=int(payload.get("schema_version", 1)),
        strategy_id=str(payload.get("strategy_id") or ""),
        strategy_version=str(payload.get("strategy_version") or "") or None,
        date_selector=date_selector,
        execution_policy=ExecutionPolicy(**dict(payload.get("execution_policy") or {})),
        data_policy=BacktestDataPolicy(**dict(payload.get("data_policy") or {})),
        target_codes=tuple(payload.get("target_codes") or ()),
        parameter_set=payload.get("parameter_set"),
        experiment_id=payload.get("experiment_id"),
        baseline_backtest_id=payload.get("baseline_backtest_id"),
        force_rerun=force_rerun,
    )


def _selector_dates(request: BacktestRequest) -> tuple[str | None, str | None, str | None]:
    if isinstance(request.date_selector, SingleDate):
        return request.date_selector.signal_date, None, None
    return None, request.date_selector.start_date, request.date_selector.end_date


def _snapshot_ready(snapshot: DataSnapshot) -> bool:
    return bool(snapshot.artifacts_by_signal_date) and all(
        coverage.is_ready for coverage in snapshot.coverage_by_signal_date.values()
    )


def _snapshot_status(snapshot: DataSnapshot) -> str:
    statuses = {coverage.status for coverage in snapshot.coverage_by_signal_date.values()}
    if CoverageStatus.INSUFFICIENT_DATA in statuses:
        return BacktestStatus.INSUFFICIENT_DATA.value
    return BacktestStatus.INCOMPLETE_EVALUATION.value


def _insufficient_result(
    snapshot: DataSnapshot,
    descriptor: StrategyDescriptor,
    execution_version: str,
) -> dict[str, Any]:
    status = _snapshot_status(snapshot)
    return {
        "schema_version": 1,
        "status": status,
        "snapshot_id": snapshot.snapshot_id,
        "strategy_id": descriptor.strategy_id,
        "strategy_version": descriptor.effective_version,
        "execution_version": execution_version,
        "signal_dates": list(snapshot.artifacts_by_signal_date),
        "missing_requirements": [gap.canonical_payload() for gap in snapshot.missing_requirements],
    }


def _minimum_signal_score(request: BacktestRequest) -> float:
    """Read a frozen compatibility threshold without consulting mutable config."""
    parameters = request.parameter_set or {}
    value = parameters.get("minimum_signal_score", MINIMUM_SIGNAL_SCORE)
    try:
        score = float(value)
    except (TypeError, ValueError) as exc:
        raise BacktestValidationError(
            BacktestErrorCode.INVALID_REQUEST,
            "parameter_set.minimum_signal_score must be a number",
        ) from exc
    if not math.isfinite(score) or not 0 <= score <= 100:
        raise BacktestValidationError(
            BacktestErrorCode.INVALID_REQUEST,
            "parameter_set.minimum_signal_score must be within [0, 100]",
        )
    return score


def _is_actionable_signal(signal: Mapping[str, Any], *, minimum_score: float) -> bool:
    try:
        score = float(signal.get("score") or 0)
    except (TypeError, ValueError):
        return False
    return not bool(signal.get("hard_veto")) and score >= minimum_score


def _artifact_id(scope: str, signal_date: str, payload: bytes, *, namespace: str) -> str:
    """Build a globally unique immutable id without aliasing equal date payloads.

    ``BacktestInputArtifact.artifact_id`` is the primary key while its natural
    key is snapshot-scoped.  The request's frozen data scope is therefore part
    of the identifier in addition to the content hash: a single-date replay
    and a range snapshot can retain the same day payload independently.
    """
    return (
        f"bta_{scope}_{signal_date.replace('-', '')}_{namespace}_"
        f"{canonical_fingerprint(payload.decode('utf-8'))[:36]}"
    )


def _trade_id(backtest_id: str, trade: Trade) -> str:
    return f"btt_{canonical_fingerprint({'backtest_id': backtest_id, 'trade': trade.canonical_payload()})[:48]}"


def _decode_artifact_payload(payload: bytes, artifact_id: str) -> dict[str, Any]:
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SnapshotCaptureError("artifact payload is not canonical UTF-8 JSON", details={"artifact_id": artifact_id}) from exc
    if not isinstance(decoded, dict):
        raise SnapshotCaptureError("artifact payload root must be an object", details={"artifact_id": artifact_id})
    return decoded


def _partition_from_payload(value: Any) -> PartitionManifest:
    if not isinstance(value, Mapping):
        raise SnapshotCaptureError("stored partition manifest item is malformed")
    range_value = value.get("query_range")
    if not isinstance(range_value, Mapping):
        raise SnapshotCaptureError("stored partition query range is malformed")
    return PartitionManifest(
        dataset=str(value.get("dataset") or ""),
        query_range=DateRange(str(range_value.get("start_date") or ""), str(range_value.get("end_date") or "")),
        source_version=str(value.get("source_version") or ""),
        row_count=int(value.get("row_count") or 0),
        content_hash=str(value.get("content_hash") or ""),
    )


def _gap_from_payload(value: Any) -> DataGap:
    if not isinstance(value, Mapping):
        raise SnapshotCaptureError("stored data gap is malformed")
    return DataGap(
        signal_date=str(value.get("signal_date") or ""),
        requirement=str(value.get("requirement") or ""),
        error_code=BacktestErrorCode(str(value.get("error_code") or "")),
        detail=str(value.get("detail") or ""),
    )


def _coverage_from_payload(value: Any) -> CoverageResult:
    if not isinstance(value, Mapping):
        raise SnapshotCaptureError("stored coverage is malformed")
    return CoverageResult(
        signal_date=str(value.get("signal_date") or ""),
        status=CoverageStatus(str(value.get("status") or "")),
        coverage_ratio=float(value.get("coverage_ratio") or 0.0),
        gaps=tuple(_gap_from_payload(item) for item in value.get("gaps", [])),
    )


def _project_run(run: Any, *, reused: bool, reuse_reason: str | None) -> dict[str, Any]:
    try:
        result = json.loads(str(run.result_json))
    except (TypeError, json.JSONDecodeError):
        result = {}
    return {
        "backtest_id": str(run.backtest_id),
        "attempt_no": int(run.attempt_no),
        "status": str(run.status),
        "snapshot_id": str(run.snapshot_id),
        "strategy_id": str(run.strategy_id),
        "strategy_version": str(run.strategy_version),
        "reused": reused,
        "reuse_reason": reuse_reason,
        "result": result,
    }


def _number(value: object) -> float:
    try:
        result = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return result if result == result and result not in {float("inf"), float("-inf")} else 0.0


def _positive_number(value: object, field: str) -> float:
    result = _number(value)
    if result <= 0:
        raise BacktestValidationError(BacktestErrorCode.INVALID_SIMULATION_INPUT, f"{field} must be positive")
    return result


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _pct_delta(value: float, reference: float) -> float:
    return (value / reference - 1) * 100 if reference > 0 else 0.0


def _position(value: float, low: float, high: float) -> float:
    return 50.0 if high <= low else max(0.0, min(100.0, (value - low) / (high - low) * 100))


def _max_drawdown_pct(closes: Sequence[float]) -> float:
    peak = 0.0
    max_drawdown = 0.0
    for close in closes:
        peak = max(peak, close)
        if peak > 0:
            max_drawdown = min(max_drawdown, (close / peak - 1) * 100)
    return max_drawdown


def _chip_concentration(row: DailyKbar, close: float) -> float:
    low = _number(row.chip_cost_15)
    high = _number(row.chip_cost_85)
    if low <= 0 or high <= low:
        return 0.0
    return max(0.0, min(100.0, 100 - (high - low) / close * 100))


def _error_code(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    return code.value if hasattr(code, "value") else str(code or "research_backtest_execution_failed")


__all__ = [
    "ArtifactOutcomeDataReader",
    "ArtifactSignalDateDataReader",
    "LocalResearchSnapshotBuilder",
    "ResearchBacktestRunner",
    "ResearchBacktestRunnerError",
    "SnapshotCaptureError",
    "SnapshotBundle",
    "StrategyUnavailableError",
]
