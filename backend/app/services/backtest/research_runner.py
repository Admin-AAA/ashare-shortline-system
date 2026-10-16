"""P0 executable, reproducible single-strategy research backtest runner.

The runner has two strictly separated phases:

* capture reads only local ``daily_kbars``/``stocks`` once and stores content
  addressed signal/outcome artifacts;
* execution and replay read those artifacts through separate reader capabilities.

It never calls the legacy ``run_backtest`` service and never writes
``Recommendation`` or ``RecommendationOutcome``.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
import json
import math
import zlib
from typing import Any, Iterable, Mapping, Protocol, Sequence, runtime_checkable
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, load_only

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
    DataRequirement,
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
    SnapshotIntegrityError,
    Trade,
    canonical_fingerprint,
    canonical_json,
    content_hash,
)
from app.engine.execution_policy import execution_policy_from_payload
from app.engine.simulation import attribute_failures, build_equity_curve, compute_metrics, simulate
from app.engine.skill_loader import load_skills
from app.engine.strategy import (
    Strategy,
    StrategyContext,
    StrategyDescriptor,
    StrategyError,
    StrategyRegistry,
    build_registry_from_skills,
    validate_backtest_parameter_set,
)
from app.models.tables import (
    CapitalFlow,
    DailyKbar,
    DragonTiger,
    IndexDaily,
    LimitUpBoard,
    MinuteKbar,
    Stock,
    StockQuote,
)

# 回测快照捕获实际需要的 daily_kbars 列（共 21/80 列）。对 351MB / 80 列大宽表
# 用 load_only 只取这些列，避免 SELECT * 触发 30s 超时（MySQL 3024）。
# 与 capture() 下游消费者(_stock_as_of / _outcome_bars / _data_readiness / _chip_concentration)
# 逐一核对：新增列访问必须先在此登记，否则 SQLAlchemy 会对未加载列做逐行 lazy-load(N+1)。
RESEARCH_KBAR_LOADED_COLUMNS = (
    DailyKbar.code, DailyKbar.trade_date, DailyKbar.open, DailyKbar.high,
    DailyKbar.low, DailyKbar.close, DailyKbar.volume, DailyKbar.amount,
    DailyKbar.pct_chg, DailyKbar.turnover_rate, DailyKbar.ma5, DailyKbar.ma10,
    DailyKbar.ma20, DailyKbar.ma60, DailyKbar.macd_dif, DailyKbar.macd_dea,
    DailyKbar.rsi_12, DailyKbar.breakout20, DailyKbar.breakout60,
    DailyKbar.chip_cost_15, DailyKbar.chip_cost_85,
)

from app.services.backtest.research_repository import (
    ArtifactIntegrityError,
    ResearchBacktestRepository,
    ResearchRecordNotFound,
    ResearchRepositoryError,
)
from app.services.backtest.stock_metadata import (
    CURRENT_STATIC,
    POINT_IN_TIME,
    UNKNOWN,
    StockMetadataHistoryProvider,
    StockMetadataProvider,
)
from app.services.backtest.vectorbt_validation import cross_validate_trades
from app.services.factors.registry import bootstrap_default_factor_registry
from app.services.market_source.snapshot import _apply_relative_features
from app.services.shortline_factors import apply_shared_shortline_factors
from app.services.runs.contracts import (
    CanonicalExecutionVersionBuilder,
    ExecutionPolicyIdentity,
    ExecutionVersionInput,
    StrategyExecutionFingerprint,
    application_identity_from_environment,
)


MINIMUM_SIGNAL_SCORE = 70.0
MAX_SIGNAL_DATES = 250
ARTIFACT_CONTENT_ENCODING = "zlib+json"
_ZLIB_JSON_MAGIC = b"ashare-zlib-json-v1\x00"
CANDIDATE_EVIDENCE_ALL = "all"
CANDIDATE_EVIDENCE_ACTIONABLE = "actionable"
_CANDIDATE_EVIDENCE_SCOPES = frozenset(
    {CANDIDATE_EVIDENCE_ALL, CANDIDATE_EVIDENCE_ACTIONABLE}
)
# Trading calendars contain weekends and holidays; two calendar days per
# required bar plus a 120-day floor provides enough buffer for a 60-bar rule
# without loading years
# of irrelevant full-market history into one immutable research capture.
MIN_LOOKBACK_CALENDAR_DAYS = 120

# Signal stock 的输入字段面契约版本。每次 _stock_as_of 的字段面或 capture 的
# 注入逻辑发生向后不兼容变化时递增；strategy_profile._compatible_snapshot_id
# 用同一常量校验候选快照是否由当前契约生成，避免复用旧字段面快照导致策略
# prefilter 拿到缺失字段（0 信号）。
RESEARCH_INPUT_SCHEMA_VERSION = 8
RESEARCH_SOURCE_VERSION = f"local-daily-kbars/v{RESEARCH_INPUT_SCHEMA_VERSION}"
RESEARCH_STOCK_METADATA_TEMPORALITY = CURRENT_STATIC


def _research_execution_version(
    request: BacktestRequest,
    descriptor: StrategyDescriptor,
    *,
    candidate_evidence_scope: str = CANDIDATE_EVIDENCE_ALL,
) -> str:
    """Build the complete, secret-free audit identity for one P0 attempt."""
    factor_plan = bootstrap_default_factor_registry()
    version_input = ExecutionVersionInput(
        application=application_identity_from_environment(),
        request_schema_version=request.schema_version,
        execution_policy=ExecutionPolicyIdentity(
            policy_id="research-backtest-execution",
            policy_version=f"v{request.execution_policy.schema_version}",
            settings={
                **request.execution_policy.canonical_payload(),
                "minimum_signal_score": _minimum_signal_score(request),
                "candidate_evidence_scope": candidate_evidence_scope,
            },
        ),
        strategy_fingerprints=(StrategyExecutionFingerprint.from_descriptor(descriptor),),
        factor_execution_plan_fingerprint=factor_plan.fingerprint,
    )
    return CanonicalExecutionVersionBuilder().build(version_input)


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
    stock_metadata_by_signal_date: Mapping[str, Any] = field(default_factory=dict)
    captured_locally: bool = True

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
        row = _read_referenced_artifact(self._repository, self._artifact)
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


class _ArtifactOutcomePayloadCache:
    """Request-local cache populated only after artifact integrity verification."""

    def __init__(self, repository: ResearchBacktestRepository) -> None:
        self._repository = repository
        self._payload_by_key: dict[tuple[str, str], dict[str, Any]] = {}

    def read(self, artifact: ArtifactRef) -> dict[str, Any]:
        cache_key = (artifact.artifact_id, artifact.content_hash)
        payload = self._payload_by_key.get(cache_key)
        if payload is None:
            row = _read_referenced_artifact(self._repository, artifact)
            payload = _decode_artifact_payload(bytes(row.payload), artifact.artifact_id)
            self._payload_by_key[cache_key] = payload
        return payload

    def clear(self) -> None:
        self._payload_by_key.clear()


class ArtifactOutcomeDataReader:
    """Outcome-side artifact capability restricted to one candidate code."""

    def __init__(
        self,
        repository: ResearchBacktestRepository,
        artifact: ArtifactRef,
        *,
        code: str,
        payload_cache: _ArtifactOutcomePayloadCache | None = None,
    ) -> None:
        self._repository = repository
        self._artifact = artifact
        self._code = code
        self._payload_cache = payload_cache

    def read(self, signal_date: str, policy: ExecutionPolicy) -> OutcomeInput:
        del policy
        if signal_date != self._artifact.signal_date:
            raise SnapshotArtifactUnavailableError(self._artifact.artifact_id)
        cache = self._payload_cache or _ArtifactOutcomePayloadCache(self._repository)
        payload = cache.read(self._artifact)
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


def _read_referenced_artifact(
    repository: ResearchBacktestRepository,
    artifact: ArtifactRef,
) -> Any:
    """Load exactly one referenced artifact and normalize repository failures."""

    try:
        return repository.get_artifact(
            artifact.artifact_id,
            expected_content_hash=artifact.content_hash,
        )
    except ResearchRecordNotFound as exc:
        raise SnapshotArtifactUnavailableError(artifact.artifact_id) from exc
    except ArtifactIntegrityError as exc:
        actual_hash = str(
            exc.details.get("expected_hash")
            or exc.details.get("actual_hash")
            or artifact.content_hash
        )
        raise SnapshotIntegrityError(
            artifact.artifact_id,
            expected_hash=artifact.content_hash,
            actual_hash=actual_hash,
        ) from exc


@runtime_checkable
class DataReadinessPort(Protocol):
    """Read-only preflight capability for a frozen strategy data contract."""

    def assess(
        self,
        *,
        signal_date: str,
        codes: Sequence[str],
        daily_rows_by_code: Mapping[str, Sequence[DailyKbar]],
        data_requirement: DataRequirement,
        execution_policy: ExecutionPolicy,
    ) -> CoverageResult:
        """Return observable readiness without refreshing or mutating source data."""
        ...


class LocalDataReadinessAdapter:
    """Local-only ``DataReadinessPort`` implementation for P0 snapshot capture."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._valid_trade_dates_cache: dict[int, tuple[str, ...]] = {}

    def assess(
        self,
        *,
        signal_date: str,
        codes: Sequence[str],
        daily_rows_by_code: Mapping[str, Sequence[DailyKbar]],
        data_requirement: DataRequirement,
        execution_policy: ExecutionPolicy,
    ) -> CoverageResult:
        normalized_codes = tuple(sorted({str(code).strip() for code in codes if str(code).strip()}))
        if not normalized_codes:
            gap = DataGap(
                signal_date,
                "daily_kbars_signal_input",
                BacktestErrorCode.INSUFFICIENT_DATA,
                "no local daily-bar codes are available on the signal date",
            )
            return CoverageResult(signal_date, CoverageStatus.INSUFFICIENT_DATA, 0.0, (gap,))

        daily_ready: list[str] = []
        future_ready: list[str] = []
        required_future_bars = max(
            execution_policy.entry_lookahead_days + execution_policy.horizon_days,
            data_requirement.future_label_horizon_days,
        )
        for code in normalized_codes:
            rows = tuple(daily_rows_by_code.get(code, ()))
            cache_key = id(daily_rows_by_code.get(code))
            valid_dates = self._valid_trade_dates_cache.get(cache_key)
            if valid_dates is None:
                valid_dates = tuple(
                    str(row.trade_date) for row in rows if _has_valid_ohlc(row)
                )
                self._valid_trade_dates_cache[cache_key] = valid_dates
            history_count = bisect_right(valid_dates, signal_date)
            future_count = len(valid_dates) - history_count
            if (
                not data_requirement.requires_daily_bars
                or history_count >= data_requirement.lookback_bars
            ):
                daily_ready.append(code)
            if future_count >= required_future_bars:
                future_ready.append(code)

        total = len(normalized_codes)
        ratios = [len(daily_ready) / total, len(future_ready) / total]
        gaps: list[DataGap] = []
        hard_insufficiency = False
        if data_requirement.requires_daily_bars and len(daily_ready) / total < data_requirement.minimum_coverage:
            missing = [code for code in normalized_codes if code not in set(daily_ready)]
            gaps.append(
                DataGap(
                    signal_date,
                    "daily_kbars_lookback",
                    BacktestErrorCode.INSUFFICIENT_DATA,
                    (
                        f"requires {data_requirement.lookback_bars} valid local daily bars; "
                        f"missing for {len(missing)}/{total} codes: {', '.join(missing[:10])}"
                    ),
                )
            )
            hard_insufficiency = True
        if len(future_ready) / total < data_requirement.minimum_coverage:
            missing = [code for code in normalized_codes if code not in set(future_ready)]
            gaps.append(
                DataGap(
                    signal_date,
                    "daily_kbars_future_window",
                    BacktestErrorCode.INCOMPLETE_EVALUATION,
                    (
                        f"requires {required_future_bars} valid future daily bars "
                        f"(entry lookahead + horizon); missing for {len(missing)}/{total} codes: "
                        f"{', '.join(missing[:10])}"
                    ),
                )
            )
        if data_requirement.requires_minute_bars:
            minute_codes = {
                str(code)
                for code in self._session.scalars(
                    select(MinuteKbar.code).where(
                        MinuteKbar.code.in_(normalized_codes),
                        MinuteKbar.trade_date == signal_date,
                    )
                ).all()
            }
            minute_ratio = len(minute_codes) / total
            ratios.append(minute_ratio)
            if minute_ratio < data_requirement.minimum_coverage:
                missing = [code for code in normalized_codes if code not in minute_codes]
                gaps.append(
                    DataGap(
                        signal_date,
                        "minute_kbars_signal_input",
                        BacktestErrorCode.INSUFFICIENT_DATA,
                        f"local minute bars missing for {len(missing)}/{total} codes: {', '.join(missing[:10])}",
                    )
                )
                hard_insufficiency = True
        if data_requirement.benchmark_code:
            benchmark_present = self._session.scalar(
                select(IndexDaily.id)
                .where(
                    IndexDaily.ts_code == data_requirement.benchmark_code,
                    IndexDaily.trade_date == signal_date,
                )
                .limit(1)
            )
            ratios.append(1.0 if benchmark_present is not None else 0.0)
            if benchmark_present is None:
                gaps.append(
                    DataGap(
                        signal_date,
                        f"benchmark:{data_requirement.benchmark_code}",
                        BacktestErrorCode.INSUFFICIENT_DATA,
                        "required benchmark is unavailable in local index_daily",
                    )
                )
                hard_insufficiency = True
        unavailable_auxiliary = tuple(
            requirement.key
            for requirement in data_requirement.auxiliary_datasets
            if requirement.minimum_coverage > 0
        )
        if unavailable_auxiliary:
            ratios.append(0.0)
            gaps.append(
                DataGap(
                    signal_date,
                    "auxiliary_datasets",
                    BacktestErrorCode.INSUFFICIENT_DATA,
                    "local auxiliary readiness is unavailable for: " + ", ".join(unavailable_auxiliary),
                )
            )
            hard_insufficiency = True

        coverage_ratio = min(ratios) if ratios else 0.0
        if not gaps:
            return CoverageResult(signal_date, CoverageStatus.READY, coverage_ratio)
        status = CoverageStatus.INSUFFICIENT_DATA if hard_insufficiency else CoverageStatus.INCOMPLETE_EVALUATION
        return CoverageResult(signal_date, status, coverage_ratio, tuple(gaps))


class LocalResearchSnapshotBuilder:
    """Capture local daily-bar inputs once, with no refresh/network capability."""

    def __init__(
        self,
        session: Session,
        *,
        data_readiness: DataReadinessPort | None = None,
        stock_metadata_provider: StockMetadataProvider | None = None,
    ) -> None:
        self._session = session
        self._data_readiness = data_readiness or LocalDataReadinessAdapter(session)
        self._stock_metadata_provider = stock_metadata_provider or StockMetadataHistoryProvider(session)

    def capture(
        self,
        request: BacktestRequest,
        *,
        data_requirement: DataRequirement | None = None,
    ) -> SnapshotBundle:
        requirement = data_requirement or DataRequirement(
            lookback_bars=60,
            future_label_horizon_days=0,
            requires_daily_bars=True,
        )
        dates = self._signal_dates(request)
        if len(dates) > MAX_SIGNAL_DATES:
            raise SnapshotCaptureError(
                "research request exceeds the maximum reproducible signal-date window",
                details={"max_signal_dates": MAX_SIGNAL_DATES, "actual_signal_dates": len(dates)},
            )
        if not dates:
            return self._empty_bundle(request)

        first_date = date.fromisoformat(dates[0])
        last_date = date.fromisoformat(dates[-1])
        lookback_calendar_days = max(MIN_LOOKBACK_CALENDAR_DAYS, requirement.lookback_bars * 2)
        history_start = (first_date - timedelta(days=lookback_calendar_days)).isoformat()
        future_window_days = max(
            request.execution_policy.entry_lookahead_days + request.execution_policy.horizon_days,
            requirement.future_label_horizon_days,
        )
        outcome_end = (last_date + timedelta(days=future_window_days + 14)).isoformat()
        # Artifact IDs are global primary keys while their natural key is
        # snapshot-scoped. Include the concrete local query window so a future
        # change to capture scope cannot alias an earlier immutable artifact
        # whose per-day payload happens to be identical.
        artifact_namespace = canonical_fingerprint(
            {
                # v2（2026-08-08）：signal stock 字段面从「日K 子集」扩展为
                # 「日K + 辅助表注入 + 相对特征 + 因子管线输入」，与
                # _compatible_snapshot_id 的 INPUT_SCHEMA_VERSION 校验联动——
                # 旧 namespace 快照不再被复用，避免 0 信号旧快照被新策略误用。
                "input_schema_version": RESEARCH_INPUT_SCHEMA_VERSION,
                "stock_metadata_temporality": RESEARCH_STOCK_METADATA_TEMPORALITY,
                "date_selector": request.date_selector,
                "target_codes": request.target_codes,
                "execution_policy": request.execution_policy,
                "data_policy": request.data_policy,
                "data_requirement": requirement,
                "input_window": {"history_start": history_start, "outcome_end": outcome_end},
            }
        )[:20]

        # 市场广度必须以全市场当日横截面计算。target_codes 只限定待评估
        # 标的，不能把单只股票的涨跌误当成市场 regime。该查询只需
        # code/trade_date/pct_chg 三列（_historical_market_context 仅读 pct_chg），
        # 用 load_only 避免对 351MB / 80 列大宽表做 SELECT *（曾触发 30s 超时 3024）。
        market_signal_rows = list(
            self._session.scalars(
                select(DailyKbar)
                .options(load_only(DailyKbar.code, DailyKbar.trade_date, DailyKbar.pct_chg))
                .where(DailyKbar.trade_date.in_(dates))
            )
        )
        market_context_by_date = {
            signal_date: _historical_market_context(rows)
            for signal_date, rows in _rows_by_trade_date(market_signal_rows).items()
        }
        # 信号行与历史/结果行共享同一 21 列消费契约。这里曾遗漏
        # load_only，导致长窗全市场快照读取约 80 列大宽表；下游实际不会
        # 访问其余列，收窄后也避免 SQLAlchemy 逐行 lazy-load。
        signal_query = (
            select(DailyKbar)
            .options(load_only(*RESEARCH_KBAR_LOADED_COLUMNS))
            .where(DailyKbar.trade_date.in_(dates))
        )
        requested_codes = set(request.target_codes) if request.target_codes else set()
        if requested_codes:
            signal_query = signal_query.where(DailyKbar.code.in_(tuple(sorted(requested_codes))))
        signal_rows = _deterministic_kbar_rows(
            self._session.scalars(signal_query)
        )
        codes = tuple(sorted({str(row.code) for row in signal_rows}))
        if not codes:
            return self._empty_bundle(request)

        # signal_rows 已经覆盖完整信号区间，历史/结果查询只读取区间两侧，
        # 避免把长窗内上百万根 K 线重复传输和物化一次。
        boundary_rows = list(
            self._session.scalars(
                select(DailyKbar)
                .options(load_only(*RESEARCH_KBAR_LOADED_COLUMNS))
                .where(
                    DailyKbar.code.in_(codes),
                    DailyKbar.trade_date >= history_start,
                    DailyKbar.trade_date <= outcome_end,
                    or_(
                        DailyKbar.trade_date < dates[0],
                        DailyKbar.trade_date > dates[-1],
                    ),
                )
            )
        )
        all_rows = [*boundary_rows, *signal_rows]
        all_rows.sort(key=lambda row: (str(row.code), str(row.trade_date)))
        stock_rows = list(self._session.scalars(select(Stock).where(Stock.code.in_(codes))))
        static_meta = {
            str(row.code): {
                "name": str(row.name or row.code),
                "industry": str(row.industry or "未分类"),
                "is_st": bool(row.is_st),
            }
            for row in stock_rows
        }
        day_resolutions: dict[str, Any] = {}
        stock_metadata_by_date: dict[str, Any] = {}
        for signal_date in dates:
            try:
                day_resolution = self._stock_metadata_provider.resolve(codes, signal_date)
            except Exception:  # noqa: BLE001 - best-effort fallback, now debug-logged
                day_resolution = None
            day_resolutions[signal_date] = day_resolution
            if day_resolution is not None and day_resolution.temporality == POINT_IN_TIME:
                stock_metadata_by_date[signal_date] = {
                    "signal_date": signal_date,
                    "temporality": day_resolution.temporality,
                    "source_version": day_resolution.source_version,
                    "fingerprint": day_resolution.fingerprint,
                    "missing_code_count": len(day_resolution.missing_codes),
                }
            else:
                stock_metadata_by_date[signal_date] = {
                    "signal_date": signal_date,
                    "temporality": CURRENT_STATIC,
                    "source_version": "stocks/current-static/v1",
                    "fingerprint": canonical_fingerprint(
                        {
                            "signal_date": signal_date,
                            "temporality": CURRENT_STATIC,
                            "source_version": "stocks/current-static/v1",
                            "metadata_by_code": static_meta,
                        }
                    ),
                    "missing_code_count": 0,
                }
        rows_by_code: dict[str, list[DailyKbar]] = defaultdict(list)
        for row in all_rows:
            rows_by_code[str(row.code)].append(row)
        row_dates_by_code = {
            code: tuple(str(row.trade_date) for row in rows)
            for code, rows in rows_by_code.items()
        }
        rows_at_date: dict[str, list[DailyKbar]] = defaultdict(list)
        for row in signal_rows:
            rows_at_date[str(row.trade_date)].append(row)

        artifact_refs: dict[str, tuple[ArtifactRef, ...]] = {}
        payloads: dict[str, bytes] = {}
        coverage: dict[str, CoverageResult] = {}
        gaps: list[DataGap] = []
        # 辅助数据按日一次取全：涨停板/龙虎榜/行情快照（free_mv_yi 等）只读本地
        # 表，回测路径据此复刻生产 build_features 的注入行为。表不存在（本地池
        # 缺表）时回退为空，不影响纯日K策略。
        aux_rows_by_date = self._load_research_aux_rows(dates)
        # 资金流必须取信号日前一已发布交易日（L-014 strictly-prior-day），不能
        # 注入信号当日资金流。构造日期映射：signal_date → prior_available_date。
        capital_rows_by_date = self._load_research_capital_flow_rows(dates)
        prior_date_map = self._build_prior_date_map(dates, capital_rows_by_date)
        for signal_date in dates:
            raw_signal_rows = rows_at_date.get(signal_date, ())
            readiness = self._data_readiness.assess(
                signal_date=signal_date,
                codes=tuple(str(row.code) for row in raw_signal_rows),
                daily_rows_by_code=rows_by_code,
                data_requirement=requirement,
                execution_policy=request.execution_policy,
            )
            signal_stocks: list[dict[str, Any]] = []
            outcome_rows_by_code: dict[str, Sequence[DailyKbar]] = {}
            invalid_signal_rows = 0
            market_context = market_context_by_date.get(signal_date, _historical_market_context(()))
            day_resolution = day_resolutions.get(signal_date)
            if day_resolution is not None and day_resolution.metadata_by_code:
                stock_meta = {
                    str(code): dict(metadata)
                    for code, metadata in day_resolution.metadata_by_code.items()
                }
            else:
                stock_meta = static_meta
            for row in raw_signal_rows:
                try:
                    code = str(row.code)
                    history = rows_by_code.get(code, ())
                    cutoff = bisect_right(row_dates_by_code.get(code, ()), signal_date)
                    feature_lookback = max(60, requirement.lookback_bars)
                    feature_history = history[
                        max(0, cutoff - feature_lookback):cutoff
                    ]
                    outcome_rows_by_code[code] = history[cutoff:]
                    signal_stocks.append(
                        _stock_as_of(
                            row,
                            feature_history,
                            stock_meta.get(code, {}),
                            signal_date,
                            market_context,
                        )
                    )
                except BacktestValidationError:
                    # A non-positive OHLC value cannot cross the pure simulation
                    # boundary. Keep it out of the strategy artifact and record
                    # the loss of coverage explicitly below.
                    invalid_signal_rows += 1
            # 复刻生产 build_features 的辅助数据注入 + 相对特征（industry 热度排名
            # 依赖同横截面全体股票，不能只对单只股票算）。
            self._inject_research_aux(signal_stocks, aux_rows_by_date.get(signal_date, {}))
            # 资金流必须使用前一已发布交易日数据（L-014 strictly-prior-day）
            prior_date = prior_date_map.get(signal_date)
            prior_flows = capital_rows_by_date.get(prior_date, {}) if prior_date else {}
            self._inject_research_capital_flow(
                signal_stocks, prior_flows, source_date=prior_date
            )
            _apply_relative_features(signal_stocks)
            signal_payload = {
                "schema_version": 1,
                "signal_date": signal_date,
                "visible_at": signal_date,
                "stock_metadata_temporality": (
                    stock_metadata_by_date.get(signal_date, {}).get(
                        "temporality", RESEARCH_STOCK_METADATA_TEMPORALITY
                    )
                ),
                "stock_metadata_source_version": (
                    stock_metadata_by_date.get(signal_date, {}).get("source_version", "stocks/current-static/v1")
                ),
                "stock_metadata_fingerprint": (
                    stock_metadata_by_date.get(signal_date, {}).get(
                        "fingerprint",
                        canonical_fingerprint(
                            {
                                "temporality": CURRENT_STATIC,
                                "source_version": "stocks/current-static/v1",
                                "signal_date": signal_date,
                            }
                        ),
                    )
                ),
                "stocks": signal_stocks,
            }
            outcome_payload = {
                "schema_version": 1,
                "signal_date": signal_date,
                "bars_by_code": {
                    stock["code"]: _outcome_bars(
                        outcome_rows_by_code.get(str(stock["code"]), ()),
                        signal_date,
                        request.execution_policy,
                    )
                    for stock in signal_stocks
                },
            }
            signal_bytes = _encode_artifact_payload(signal_payload)
            outcome_bytes = _encode_artifact_payload(outcome_payload)
            signal_ref = ArtifactRef.from_content(
                artifact_id=_artifact_id("signal", signal_date, signal_bytes, namespace=artifact_namespace),
                signal_date=signal_date,
                artifact_scope=ArtifactScope.SIGNAL_INPUT,
                artifact_type="daily_kbars_strategy_input",
                schema_version=1,
                row_count=len(signal_stocks),
                payload=signal_bytes,
                content_encoding=ARTIFACT_CONTENT_ENCODING,
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
                content_encoding=ARTIFACT_CONTENT_ENCODING,
            )
            artifact_refs[signal_date] = (signal_ref, outcome_ref)
            payloads[signal_ref.artifact_id] = signal_bytes
            payloads[outcome_ref.artifact_id] = outcome_bytes

            has_future_bars = outcome_bar_count > 0
            total_signal_rows = len(raw_signal_rows)
            # A small fraction of invalid OHLC rows (new listings, suspended
            # stocks, data gaps) is tolerable: those rows are excluded from the
            # signal artifact and do not affect the quality of the rest.  Only
            # when the loss exceeds the strategy's minimum_coverage threshold
            # does the date become incomplete.
            invalid_ratio = invalid_signal_rows / total_signal_rows if total_signal_rows else 0.0
            invalid_tolerable = invalid_ratio <= max(0.0, 1.0 - requirement.minimum_coverage)
            if not readiness.is_ready:
                coverage[signal_date] = readiness
                gaps.extend(readiness.gaps)
            elif signal_stocks and has_future_bars and invalid_tolerable:
                coverage[signal_date] = CoverageResult(signal_date, CoverageStatus.READY, readiness.coverage_ratio)
            else:
                if not signal_stocks:
                    status = CoverageStatus.INSUFFICIENT_DATA
                    error_code = BacktestErrorCode.INSUFFICIENT_DATA
                    requirement_name = "daily_kbars_signal_input"
                    detail = "no valid local daily bars on signal date"
                elif invalid_signal_rows and not invalid_tolerable:
                    status = CoverageStatus.INCOMPLETE_EVALUATION
                    error_code = BacktestErrorCode.INCOMPLETE_EVALUATION
                    requirement_name = "daily_kbars_signal_input"
                    detail = f"{invalid_signal_rows} local daily-bar rows have invalid non-positive OHLC values"
                else:
                    status = CoverageStatus.INCOMPLETE_EVALUATION
                    error_code = BacktestErrorCode.INCOMPLETE_EVALUATION
                    requirement_name = "daily_kbars_outcome_input"
                    detail = "no future local daily bars for execution"
                gap = DataGap(signal_date, requirement_name, error_code, detail)
                gaps.append(gap)
                ratio = len(signal_stocks) / total_signal_rows if total_signal_rows else 0.0
                coverage[signal_date] = CoverageResult(signal_date, status, ratio, (gap,))

        metadata_evidence = {
            "temporality": (
                POINT_IN_TIME
                if stock_metadata_by_date
                and all(
                    value.get("temporality") == POINT_IN_TIME
                    for value in stock_metadata_by_date.values()
                )
                else CURRENT_STATIC
            ),
            "source_versions": sorted(
                {
                    str(value.get("source_version") or "")
                    for value in stock_metadata_by_date.values()
                }
            ),
            "by_signal_date": {
                signal_date: {
                    "temporality": str(value.get("temporality") or CURRENT_STATIC),
                    "source_version": str(value.get("source_version") or "stocks/current-static/v1"),
                    "fingerprint": str(value.get("fingerprint") or ""),
                    "missing_code_count": int(value.get("missing_code_count") or 0),
                }
                for signal_date, value in stock_metadata_by_date.items()
            },
        }
        partition_payload = {
            "dataset": "daily_kbars",
            "input_schema_version": RESEARCH_INPUT_SCHEMA_VERSION,
            "stock_metadata": metadata_evidence,
            "stock_metadata_temporality": metadata_evidence["temporality"],
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
            # source_version 编码 signal-stock 输入字段面版本（与
            # RESEARCH_INPUT_SCHEMA_VERSION 联动）：字段面升级后旧快照的
            # source_version 不变，strategy_profile 据此拒绝复用旧快照。
            source_version=RESEARCH_SOURCE_VERSION,
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
        return SnapshotBundle(
            snapshot=snapshot,
            payloads_by_artifact_id=payloads,
            stock_metadata_by_signal_date=stock_metadata_by_date,
        )

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

    def _load_research_aux_rows(self, dates: Sequence[str]) -> dict[str, dict[str, Any]]:
        """按 signal_date 一次取全本地辅助数据（涨停板/龙虎榜/行情快照）。

        生产路径 build_features 在盘后从 auxiliary 池注入这些字段；回测路径
        没有 real-time 池，只能读本地表复刻。表缺失（本地池未建表）时返回
        空 dict，纯日K策略不受影响。
        """
        rows_by_date: dict[str, dict[str, Any]] = {d: {} for d in dates}
        try:
            limit_up_rows = self._session.scalars(
                select(LimitUpBoard).where(LimitUpBoard.trade_date.in_(tuple(dates)))
            ).all()
        except Exception:  # noqa: BLE001 - best-effort fallback, now debug-logged
            limit_up_rows = ()
        for row in limit_up_rows:
            rows_by_date.setdefault(str(row.trade_date), {})[
                str(row.code)
            ] = {
                "limit_up_consecutive_days": int(row.limit_times or 1),
                "limit_up_open_times": int(row.open_times or 0),
                "limit_up_fd_amount_yi": float(row.fd_amount_yi or 0),
                "limit_up_seal_time": row.first_time,
            }
        # 龙虎榜约 21:00 入库，17:35 决策时不可见（L-163/L-167）：T 日上榜记录只在下一个
        # 回放交易日可见，与实盘 state_adapters._decision_visible_date 同口径。
        sorted_dates = sorted(str(d) for d in dates)
        try:
            from datetime import date as _date, timedelta as _td

            _lo = _date.fromisoformat(sorted_dates[0]) - _td(days=15)
            dragon_tiger_rows = self._session.scalars(
                select(DragonTiger).where(
                    DragonTiger.trade_date >= _lo,
                    DragonTiger.trade_date < sorted_dates[-1],
                )
            ).all()
        except Exception:  # noqa: BLE001 - best-effort fallback, now debug-logged
            dragon_tiger_rows = ()
        import bisect as _bisect

        for row in dragon_tiger_rows:
            _i = _bisect.bisect_right(sorted_dates, str(row.trade_date))
            if _i >= len(sorted_dates):
                continue
            visible_date = sorted_dates[_i]
            rows_by_date.setdefault(visible_date, {}).setdefault(str(row.code), {}).update(
                {
                    "dragon_tiger_is_on_list": True,
                    "dragon_tiger_recent_count": 1,
                    "dragon_tiger_latest_date": str(row.trade_date),
                    "dragon_tiger_buy_amount_yi": float(row.buy_amount_yi or 0),
                    "dragon_tiger_sell_amount_yi": float(row.sell_amount_yi or 0),
                    "dragon_tiger_net_amount_yi": float(row.net_amount_yi or 0),
                    # 2026-09-13 修复（深审 A3-2）：回测路径同样不再用总净买冒充机构净买
                    # （原代理使 inst_buy_ratio 恒为 ±1）；top_inst 席位明细接入前保持 0。
                    "dragon_tiger_institution_net_buy_yi": 0.0,
                }
            )
        try:
            quote_rows = self._session.scalars(
                select(StockQuote).where(StockQuote.trade_date.in_(tuple(dates)))
            ).all()
        except Exception:  # noqa: BLE001 - best-effort fallback, now debug-logged
            quote_rows = ()
        for row in quote_rows:
            rows_by_date.setdefault(str(row.trade_date), {}).setdefault(str(row.code), {}).update(
                {
                    "free_mv_yi": float(row.free_mv_yi or 0),
                    "total_mv_yi": float(row.total_mv_yi or 0),
                }
            )
        return rows_by_date

    def _build_prior_date_map(
        self,
        dates: Sequence[str],
        capital_rows_by_date: dict[str, dict[str, dict[str, Any]]],
    ) -> dict[str, str | None]:
        """构造信号日→前一可得资金流日期的映射（L-014 strictly-prior-day）。

        来源日期来自实际可得记录，独立于信号日采样及窗口边界。
        每个信号仅接受严格早于该日的最近记录；没有前置记录则保留缺失。
        """
        prior_map: dict[str, str | None] = {}
        available_dates = sorted(day for day, rows in capital_rows_by_date.items() if rows)
        for signal_date in dates:
            index = bisect_left(available_dates, signal_date)
            prior_map[signal_date] = available_dates[index - 1] if index else None
        return prior_map

    def _load_research_capital_flow_rows(
        self, dates: Sequence[str]
    ) -> dict[str, dict[str, dict[str, Any]]]:
        """按 signal_date 一次取全本地个股资金流向（tushare moneyflow 落库）。

        让回测的 capital_resonance 因子走真实 ``capital_flow`` 口径而非
        ``price_volume_fallback`` 代理（代理把涨幅+量比最强的候选标成"资金配合"，
        与真实资金语义反相关，导致资本类毒区在回测中删除的恰是赢家）。表缺失
        （本地池未建表）或日期无数据时返回空 dict，纯日K策略不受影响。
        """
        rows_by_date: dict[str, dict[str, dict[str, Any]]] = {
            d: {} for d in dates
        }
        if not dates:
            return rows_by_date
        try:
            # 首个信号也需要窗口外的前置输入，不能由请求边界制造数据缺失。
            prior_date = self._session.scalar(
                select(CapitalFlow.trade_date)
                .where(CapitalFlow.trade_date < min(dates))
                .order_by(CapitalFlow.trade_date.desc()).limit(1)
            )
            flow_rows = self._session.scalars(
                select(CapitalFlow).where(
                    CapitalFlow.trade_date >= (prior_date or min(dates)),
                    CapitalFlow.trade_date < max(dates),
                )
            ).all()
        except Exception:  # noqa: BLE001 - best-effort fallback, now debug-logged
            flow_rows = ()
        for row in flow_rows:
            rows_by_date.setdefault(str(row.trade_date), {})[
                str(row.code)
            ] = {
                "main_net_inflow_yi": float(row.main_net_inflow_yi or 0),
                "super_large_net_inflow_yi": float(row.super_large_net_inflow_yi or 0),
                "large_net_inflow_yi": float(row.large_net_inflow_yi or 0),
                "small_net_inflow_yi": float(row.small_net_inflow_yi or 0),
                "main_net_inflow_ratio": float(row.main_net_inflow_ratio or 0),
            }
        return rows_by_date

    def _inject_research_capital_flow(
        self,
        stocks: list[dict[str, Any]],
        flows: dict[str, dict[str, Any]],
        *,
        source_date: str | None = None,
    ) -> None:
        """把资金流字段合并进当日 signal stocks，并复刻板块资金排名。

        L-014 strictly-prior-day：资金流必须来自信号日前一已发布交易日，不能注入
        信号当日资金流。调用方通过 prior_date_map 获取正确的 source_date。

        - 有资金流行的个股：写入 main_net_inflow_yi 等字段、置
          ``capital_flow_checked=True`` 并移除 missing_fields 中的
          ``capital_flow``，使 CapitalResonanceFactor 走真实口径。
        - 无资金流行（数据缺口）的个股保持原样，因子走 price_volume_fallback。
        - sector_fund_rank：按板块（concept_sector_name/industry）当日主力净流入
          合计排序（1=最强），复刻生产 build_features 的板块排名语义；无流数据
          的板块不参与，个股保持 _stock_as_of 的默认值。
        - source_date：资金流实际来源日期，用于审计和验证；为 None 时表示无可用数据。
        """
        if not flows:
            return
        # 板块资金排名需要横截面全体：先按板块合计主力净流入，再按 code 映射个股。
        rank_by_sector: dict[str, int] = {}
        sector_inflow: dict[str, float] = {}
        for stock in stocks:
            code = str(stock.get("code") or "")
            f = flows.get(code)
            if not f:
                continue
            sector = str(
                stock.get("concept_sector_name") or stock.get("industry") or "未分类"
            )
            sector_inflow[sector] = sector_inflow.get(sector, 0.0) + float(
                f.get("main_net_inflow_yi") or 0.0
            )
        if sector_inflow:
            ranked = [
                s
                for s, _ in sorted(
                    sector_inflow.items(), key=lambda kv: kv[1], reverse=True
                )
            ]
            rank_by_sector = {s: i + 1 for i, s in enumerate(ranked)}
        for stock in stocks:
            code = str(stock.get("code") or "")
            f = flows.get(code)
            if not f:
                continue
            for key, value in f.items():
                stock[key] = value
            sector = str(
                stock.get("concept_sector_name") or stock.get("industry") or "未分类"
            )
            stock["sector_fund_rank"] = int(rank_by_sector.get(sector, 99))
            data_quality = dict(stock.get("data_quality") or {})
            missing = set(data_quality.get("missing_fields") or [])
            missing.discard("capital_flow")
            data_quality["missing_fields"] = sorted(missing)
            data_quality["capital_flow_checked"] = True
            stock["data_quality"] = data_quality

    def _inject_research_aux(
        self,
        stocks: list[dict[str, Any]],
        aux: dict[str, dict[str, Any]],
    ) -> None:
        """把本地辅助数据字段合并进当日 signal stocks（回测复刻生产注入）。"""
        if not aux:
            return
        for stock in stocks:
            code = str(stock.get("code") or "")
            fields = aux.get(code)
            if not fields:
                continue
            if "limit_up_consecutive_days" in fields:
                stock["is_limit_up"] = True
                stock["limit_up_consecutive_days"] = fields["limit_up_consecutive_days"]
                stock["limit_up_open_times"] = fields["limit_up_open_times"]
                stock["limit_up_fd_amount_yi"] = fields["limit_up_fd_amount_yi"]
                if fields.get("limit_up_seal_time"):
                    stock["limit_up_seal_time"] = fields["limit_up_seal_time"]
            if fields.get("dragon_tiger_is_on_list"):
                stock.update(
                    {
                        "dragon_tiger_is_on_list": True,
                        "dragon_tiger_recent_count": 1,
                        "dragon_tiger_latest_date": fields["dragon_tiger_latest_date"],
                        "dragon_tiger_buy_amount_yi": fields["dragon_tiger_buy_amount_yi"],
                        "dragon_tiger_sell_amount_yi": fields["dragon_tiger_sell_amount_yi"],
                        "dragon_tiger_net_amount_yi": fields["dragon_tiger_net_amount_yi"],
                        "dragon_tiger_institution_net_buy_yi": fields["dragon_tiger_institution_net_buy_yi"],
                    }
                )
                amount_yi = float(stock.get("amount_yi") or 0)
                net_amt = fields["dragon_tiger_net_amount_yi"]
                stock["dragon_tiger_net_buy_ratio"] = (
                    round(min(1.0, max(-1.0, net_amt / amount_yi)), 4) if amount_yi else 0.0
                )
            if fields.get("free_mv_yi"):
                stock["free_mv_yi"] = fields["free_mv_yi"]
                stock["total_mv_yi"] = fields["total_mv_yi"]

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
            stock_metadata_by_signal_date={
                signal_date: {
                    "signal_date": signal_date,
                    "temporality": CURRENT_STATIC,
                    "source_version": "stocks/current-static/v1",
                    "fingerprint": canonical_fingerprint(
                        {
                            "signal_date": signal_date,
                            "temporality": CURRENT_STATIC,
                            "source_version": "stocks/current-static/v1",
                        }
                    ),
                    "missing_code_count": 0,
                }
            },
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
        candidate_evidence_scope: str = CANDIDATE_EVIDENCE_ALL,
    ) -> None:
        if candidate_evidence_scope not in _CANDIDATE_EVIDENCE_SCOPES:
            raise ValueError(
                "candidate_evidence_scope must be 'all' or 'actionable'"
            )
        self._repository = repository
        self._session = session or repository.session
        self._strategy_registry = strategy_registry
        self._snapshot_builder = snapshot_builder or LocalResearchSnapshotBuilder(self._session)
        self._candidate_evidence_scope = candidate_evidence_scope

    def resolve_request(self, request: BacktestRequest) -> BacktestRequest:
        """Freeze the installed strategy version and its validated parameters."""

        descriptor, _strategy = self._resolve_strategy(request)
        validated_request = self._request_with_validated_parameters(request, descriptor)
        return replace(validated_request, strategy_version=descriptor.effective_version)

    def preflight(self, request: BacktestRequest) -> dict[str, Any]:
        descriptor, _strategy = self._resolve_strategy(request)
        resolved_request = self._request_with_validated_parameters(request, descriptor)
        bundle = self._snapshot_builder.capture(
            resolved_request,
            data_requirement=descriptor.data_requirement,
        )
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
        validated_request = self._request_with_validated_parameters(request, descriptor)
        resolved_request = replace(validated_request, strategy_version=descriptor.effective_version)
        bundle = self._snapshot_builder.capture(
            resolved_request,
            data_requirement=descriptor.data_requirement,
        )
        return self._execute(resolved_request, descriptor, strategy, bundle)

    def run_on_snapshot(self, request: BacktestRequest, snapshot_id: str) -> dict[str, Any]:
        """Execute a new request against one previously captured immutable snapshot."""

        if not isinstance(snapshot_id, str) or not snapshot_id.strip():
            raise BacktestValidationError(
                BacktestErrorCode.INVALID_REQUEST,
                "snapshot_id must be a non-empty string",
            )
        descriptor, strategy = self._resolve_strategy(request)
        validated_request = self._request_with_validated_parameters(request, descriptor)
        resolved_request = replace(validated_request, strategy_version=descriptor.effective_version)
        bundle = self._load_snapshot_bundle(snapshot_id.strip())
        _validate_snapshot_selector(resolved_request, bundle.signal_dates)
        return self._execute(resolved_request, descriptor, strategy, bundle)

    def load_snapshot_bundle(self, snapshot_id: str) -> SnapshotBundle:
        """Load and integrity-check a frozen snapshot and every referenced artifact."""

        return self._load_snapshot_bundle(snapshot_id)

    def snapshot_stock_metadata_temporality(self, snapshot_id: str) -> str:
        """Read metadata temporality from an integrity-checked snapshot manifest."""

        row = self._repository.get_snapshot(snapshot_id)
        try:
            manifest = json.loads(str(row.partition_manifest_json))
        except json.JSONDecodeError as exc:
            raise SnapshotCaptureError(
                "stored snapshot JSON is malformed",
                details={"snapshot_id": snapshot_id},
            ) from exc
        metadata = manifest.get("stock_metadata") if isinstance(manifest, Mapping) else None
        if isinstance(metadata, Mapping):
            temporality = str(metadata.get("temporality") or UNKNOWN)
            if temporality in {POINT_IN_TIME, CURRENT_STATIC, UNKNOWN}:
                return temporality
        partitions = manifest.get("partitions") if isinstance(manifest, Mapping) else None
        if not isinstance(partitions, list):
            raise SnapshotCaptureError(
                "stored immutable snapshot manifest is malformed",
                details={"snapshot_id": snapshot_id},
            )
        source_versions = {
            str(item.get("source_version") or "")
            for item in partitions
            if isinstance(item, Mapping) and item.get("dataset") == "daily_kbars"
        }
        if source_versions == {RESEARCH_SOURCE_VERSION}:
            return RESEARCH_STOCK_METADATA_TEMPORALITY
        return "unknown"

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
        resolved_request = self._request_with_validated_parameters(resolved_request, descriptor)
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
        execution_version = _research_execution_version(
            request,
            descriptor,
            candidate_evidence_scope=self._candidate_evidence_scope,
        )
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
            except Exception:  # noqa: BLE001 - best-effort fallback, now debug-logged
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
        evaluated_candidate_count = 0
        persisted_trades: list[Trade] = []
        attribution_rows: list[dict[str, Any]] = []
        signal_dates = bundle.signal_dates
        minimum_signal_score = _minimum_signal_score(request)
        for signal_date in signal_dates:
            signal_ref = bundle.artifact_for(signal_date, ArtifactScope.SIGNAL_INPUT)
            outcome_ref = bundle.artifact_for(signal_date, ArtifactScope.OUTCOME_INPUT)
            outcome_payload_cache = _ArtifactOutcomePayloadCache(self._repository)
            signal_input = ArtifactSignalDateDataReader(self._repository, signal_ref).read(
                signal_date,
                SignalInputRequirement(lookback_bars=0, required_datasets=("daily_kbars",)),
            )
            stocks = signal_input.payload["stocks"]
            if not isinstance(stocks, Sequence):
                raise SnapshotCaptureError("signal artifact stocks must be a sequence")
            # 复刻生产 build_features 的因子管线：跨股聚合一次算好 → 建
            # FactorContext → run_factors 按 depends_on 拓扑序驱动全部已注册短线
            # 因子（sector_ladder/capital_resonance/limit_up_overheat/leader/
            # dragon_tiger/twenty_cm_risk），使策略 prefilter/scoring 在 evaluate
            # 前拿到与生产一致的衍生字段。纯 local 数据，不改不可变 artifact。
            stock_list = [dict(raw) for raw in stocks if isinstance(raw, Mapping)]
            _apply_shared_factors(stock_list, bundle)
            stocks = stock_list
            for raw_stock in stocks:
                if not isinstance(raw_stock, Mapping):
                    continue
                stock = dict(raw_stock)
                code = str(stock.get("code") or "")
                if not code:
                    continue
                evaluated_candidate_count += 1
                try:
                    signal = dict(strategy.evaluate(stock, StrategyContext()))
                except Exception as exc:  # preserve individual evidence without promoting it to a trade  # noqa: BLE001
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
                # Keep the signal-side context that the P0-P4 quality layer
                # needs beside the strategy score.  These fields are all
                # visible on the signal date and therefore do not expand the
                # future-data boundary.  Older candidate rows may not contain
                # them; the quality report marks those rows as unknown rather
                # than guessing a regime from the outcome.
                signal.update(
                    {
                        "signal_date": signal_date,
                        "signal_price": float(stock.get("price") or stock.get("close") or 0),
                        "market_regime": str(stock.get("market_regime") or "unknown"),
                        "market_sentiment_phase": str(
                            stock.get("market_sentiment_phase") or "unknown"
                        ),
                        "industry": str(stock.get("industry") or ""),
                        "concept_sector_name": str(stock.get("concept_sector_name") or ""),
                        "is_main_theme": bool(stock.get("is_main_theme")),
                    }
                )
                actionable = _is_actionable_signal(signal, minimum_score=minimum_signal_score)
                if (
                    self._candidate_evidence_scope == CANDIDATE_EVIDENCE_ALL
                    or actionable
                ):
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
                    payload_cache=outcome_payload_cache,
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
            outcome_payload_cache.clear()

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
        output_evidence_fingerprint = self._repository.output_evidence_fingerprint(
            backtest_id
        )
        metrics = compute_metrics(persisted_trades)
        equity_curve = build_equity_curve(persisted_trades)
        cross_validation = cross_validate_trades(tuple(persisted_trades), request.execution_policy)
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
            "candidate_count": evaluated_candidate_count,
            "persisted_candidate_count": len(candidate_rows),
            "candidate_evidence_scope": self._candidate_evidence_scope,
            "trade_count": len(persisted_trades),
            "metrics": metrics.canonical_payload(),
            "equity_curve": [point.canonical_payload() for point in equity_curve],
            "failure_attribution_count": len(attribution_rows),
            "output_evidence_fingerprint": output_evidence_fingerprint,
            "cross_validation": cross_validation.as_dict(),
            "artifact_refs": {
                signal_date: [ref.canonical_payload() for ref in bundle.snapshot.artifacts_for(signal_date)]
                for signal_date in signal_dates
            },
        }

    def _persist_bundle(self, bundle: SnapshotBundle) -> None:
        snapshot = bundle.snapshot
        if not bundle.captured_locally:
            # A rehydrated bundle already belongs to the immutable repository.
            # Signal/outcome readers verify each payload hash when it is used;
            # loading and hashing every large BLOB here would duplicate that I/O.
            self._repository.get_snapshot(snapshot.snapshot_id)
            return
        metadata_summary = {
            signal_date: {
                "temporality": str(value.get("temporality") or CURRENT_STATIC),
                "source_version": str(value.get("source_version") or "stocks/current-static/v1"),
                "fingerprint": str(value.get("fingerprint") or ""),
                "missing_code_count": int(value.get("missing_code_count") or 0),
            }
            for signal_date, value in sorted(bundle.stock_metadata_by_signal_date.items())
        }
        immutable_manifest = {
            "schema_version": 3,
            "partitions": [partition.canonical_payload() for partition in snapshot.partitions],
            "stock_metadata": {
                "temporality": (
                    POINT_IN_TIME
                    if bundle.stock_metadata_by_signal_date
                    and all(
                        str(value.get("temporality") or "") == POINT_IN_TIME
                        for value in bundle.stock_metadata_by_signal_date.values()
                    )
                    else CURRENT_STATIC
                ),
                "by_signal_date": metadata_summary,
                "fingerprint": canonical_fingerprint(metadata_summary),
            },
            "artifacts_by_signal_date": {
                signal_date: [reference.canonical_payload() for reference in references]
                for signal_date, references in snapshot.artifacts_by_signal_date.items()
            },
        }
        self._repository.persist_snapshot(
            snapshot_id=snapshot.snapshot_id,
            snapshot_version=snapshot.snapshot_version,
            partition_manifest=immutable_manifest,
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
                    row = _read_referenced_artifact(self._repository, ref)
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
            stored_manifest = json.loads(str(row.partition_manifest_json))
            coverage_payload = json.loads(str(row.coverage_json))
            gaps_payload = json.loads(str(row.missing_requirements_json))
        except json.JSONDecodeError as exc:
            raise SnapshotCaptureError("stored snapshot JSON is malformed", details={"snapshot_id": snapshot_id}) from exc
        if not isinstance(stored_manifest, Mapping) or stored_manifest.get("schema_version") not in {2, 3}:
            # A legacy list-only manifest cannot prove which current artifact rows
            # belong to the historical snapshot.  Safe-fail rather than infer.
            raise SnapshotArtifactUnavailableError(f"{snapshot_id}:artifact_manifest")
        partitions_payload = stored_manifest.get("partitions")
        artifact_payload = stored_manifest.get("artifacts_by_signal_date")
        metadata_payload = stored_manifest.get("stock_metadata") or {}
        if not isinstance(partitions_payload, list) or not isinstance(artifact_payload, Mapping):
            raise SnapshotCaptureError("stored immutable snapshot manifest is malformed", details={"snapshot_id": snapshot_id})
        if not isinstance(metadata_payload, Mapping):
            raise SnapshotCaptureError("stored stock metadata manifest is malformed", details={"snapshot_id": snapshot_id})
        if not isinstance(coverage_payload, Mapping) or not isinstance(gaps_payload, list):
            raise SnapshotCaptureError("stored snapshot coverage JSON is malformed", details={"snapshot_id": snapshot_id})

        artifacts_by_date: dict[str, tuple[ArtifactRef, ...]] = {}
        for signal_date, ref_payloads in artifact_payload.items():
            if not isinstance(signal_date, str) or not isinstance(ref_payloads, list):
                raise SnapshotCaptureError("stored artifact reference manifest is malformed", details={"snapshot_id": snapshot_id})
            refs = tuple(_artifact_ref_from_payload(value) for value in ref_payloads)
            if any(ref.signal_date != signal_date for ref in refs):
                raise SnapshotCaptureError(
                    "stored artifact reference signal date is inconsistent",
                    details={"snapshot_id": snapshot_id, "signal_date": signal_date},
                )
            artifacts_by_date[signal_date] = refs
            for ref in refs:
                _read_referenced_artifact(self._repository, ref)

        partitions = tuple(_partition_from_payload(value) for value in partitions_payload)
        coverage = {
            str(day): _coverage_from_payload(value)
            for day, value in coverage_payload.items()
        }
        gaps = tuple(_gap_from_payload(value) for value in gaps_payload)
        metadata_by_date = metadata_payload.get("by_signal_date") or {}
        if not isinstance(metadata_by_date, Mapping):
            raise SnapshotCaptureError("stored stock metadata by-date manifest is malformed", details={"snapshot_id": snapshot_id})
        snapshot = DataSnapshot(
            snapshot_id=snapshot_id,
            snapshot_version=int(row.snapshot_version),
            partitions=partitions,
            artifacts_by_signal_date=artifacts_by_date,
            coverage_by_signal_date=coverage,
            missing_requirements=gaps,
        )
        return SnapshotBundle(
            snapshot=snapshot,
            payloads_by_artifact_id={},
            stock_metadata_by_signal_date={
                str(signal_date): dict(value)
                for signal_date, value in metadata_by_date.items()
                if isinstance(value, Mapping)
            },
            captured_locally=False,
        )

    def _resolve_strategy(self, request: BacktestRequest) -> tuple[StrategyDescriptor, Strategy]:
        registry = self._strategy_registry or self._build_default_registry(request.strategy_id)
        try:
            return registry.resolve(request.strategy_id, effective_version=request.strategy_version)
        except StrategyError as exc:
            raise StrategyUnavailableError(
                "requested strategy is not available for a reproducible local research run",
                details={"strategy_id": request.strategy_id, "requested_version": request.strategy_version},
            ) from exc

    def _request_with_validated_parameters(
        self,
        request: BacktestRequest,
        descriptor: StrategyDescriptor,
    ) -> BacktestRequest:
        """Freeze only the descriptor-declared parameters before data capture."""

        return replace(
            request,
            parameter_set=validate_backtest_parameter_set(descriptor, request.parameter_set),
        )

    @staticmethod
    def _build_default_registry(strategy_id: str) -> StrategyRegistry:
        skills = [
            skill
            for skill in load_skills(get_settings().skills_dir)
            if skill.skill_id == strategy_id and skill.enabled and skill.executable
        ]
        if not skills:
            raise StrategyUnavailableError(
                "strategy is not an enabled executable strategy",
                details={"strategy_id": strategy_id},
            )
        return build_registry_from_skills(skills)


def _stock_as_of(
    row: DailyKbar,
    history: Sequence[DailyKbar],
    metadata: Mapping[str, Any],
    signal_date: str,
    market_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    prior = list(history)
    close = _positive_number(row.close, "close")
    closes = [_positive_number(item.close, "close") for item in prior if _number(item.close) > 0]
    highs = [_positive_number(item.high, "high") for item in prior if _number(item.high) > 0]
    lows = [_positive_number(item.low, "low") for item in prior if _number(item.low) > 0]
    volumes = [max(0.0, _number(item.volume)) for item in prior]
    lookback20 = closes[-20:]
    lookback60 = closes[-60:]
    lookback120 = closes[-120:]
    recent_high20 = max(highs[-20:] or [close])
    recent_low20 = min(lows[-20:] or [close])
    recent_high60 = max(highs[-60:] or [close])
    recent_low60 = min(lows[-60:] or [close])
    recent_high120 = max(highs[-120:] or [close])
    recent_low120 = min(lows[-120:] or [close])
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
    limit_pct = _limit_pct_for_bar(row, metadata)
    is_limit_up = pct >= limit_pct - 0.2
    pos20 = _position(close, recent_low20, recent_high20)
    pos60 = _position(close, recent_low60, recent_high60)
    pos120 = _position(close, recent_low120, recent_high120)
    dist20 = _pct_delta(close, ma20)
    dist60 = _pct_delta(close, ma60)
    chg20 = _pct_delta(close, lookback20[0]) if lookback20 else 0.0
    chg60 = _pct_delta(close, lookback60[0]) if lookback60 else 0.0
    # 效率系数（Kaufman 口径）与 indicator_computer.efficiency 一致
    eff20 = 0.0
    if len(closes) > 20:
        direction = abs(closes[-1] - closes[-21])
        noise = sum(abs(closes[index] - closes[index - 1]) for index in range(len(closes) - 20, len(closes)))
        eff20 = direction / noise if noise else 0.0
    dsa_trend = 10 * int(close >= ma20) + 10 * int(ma5 >= ma10) + 10 * int(ma10 >= ma20)
    dsa_bias = 14 if -2.0 <= _pct_delta(close, ma5) <= 5.0 else 6
    dsa_volume = min(15, int(max(0.0, vol5_20) * 5))
    dsa_support = 10 if close >= ma5 * 0.985 else 3
    # MACD/RSI：列值缺失或为 0（历史批量同步损坏）时按 indicator_computer 口径
    # 从本地 closes 重算，与生产运行时兜底一致（否则损坏日期上 dsa_macd/dsa_rsi
    # 恒为最低分，条件如 rsi_12>72 静默失效）。
    macd_dif_col = _number(row.macd_dif)
    macd_dea_col = _number(row.macd_dea)
    if macd_dif_col <= 0 and macd_dea_col <= 0 and len(closes) >= 35:
        _alpha12 = 2 / 13
        _alpha26 = 2 / 27
        _alpha9 = 2 / 10
        _ema12 = closes[0]
        _ema26 = closes[0]
        _dea = 0.0
        for _c in closes[:-1]:
            _ema12 = _alpha12 * _c + (1 - _alpha12) * _ema12
            _ema26 = _alpha26 * _c + (1 - _alpha26) * _ema26
            _dea = _alpha9 * (_ema12 - _ema26) + (1 - _alpha9) * _dea
        _ema12 = _alpha12 * closes[-1] + (1 - _alpha12) * _ema12
        _ema26 = _alpha26 * closes[-1] + (1 - _alpha26) * _ema26
        macd_dif_col = _ema12 - _ema26
        macd_dea_col = _alpha9 * macd_dif_col + (1 - _alpha9) * _dea
    dsa_macd = 10 if macd_dif_col >= macd_dea_col else 2
    rsi_col = _number(row.rsi_12)
    if rsi_col <= 0 and len(closes) > 12:
        _deltas = [closes[i] - closes[i - 1] for i in range(len(closes) - 12, len(closes))]
        _gains = [d for d in _deltas if d > 0]
        _losses = [-d for d in _deltas if d < 0]
        _avg_gain = sum(_gains) / len(_gains) if _gains else 0.0
        _avg_loss = sum(_losses) / len(_losses) if _losses else 0.0
        rsi_col = 100.0 if _avg_loss == 0 and _avg_gain > 0 else (50.0 if _avg_loss == 0 else 100 - (100 / (1 + _avg_gain / _avg_loss)))
    rsi = rsi_col
    dsa_rsi = 10 if 40 <= rsi <= 70 else 4
    # 乖离率：列缺失时按 (close-ma)/ma*100 计算（生产 bias 口径）
    bias_ma5 = _pct_delta(close, ma5)
    bias_ma10 = _pct_delta(close, ma10)
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
        "macd_dif": macd_dif_col,
        "macd_dea": macd_dea_col,
        "bias_ma5": bias_ma5,
        "bias_ma10": bias_ma10,
        "breakout20": int(bool(row.breakout20) or close >= recent_high20),
        "breakout60": int(bool(row.breakout60) or close >= recent_high60),
        "max_dd20": max_dd20,
        "chip_concentration": chip_concentration,
        "pos20": pos20,
        "pos60": pos60,
        "pos120": pos120,
        "dist20": dist20,
        "dist60": dist60,
        "bias_ma20": dist20,
        "eff20": eff20,
        "off_high_pct": _pct_delta(close, recent_high20),
        "off_high60_pct": _pct_delta(close, recent_high60),
        "chg20": chg20,
        "chg60": chg60,
        "vol5_20": vol5_20,
        "support_ma5": close >= ma5 * 0.985,
        "support_ma10": close >= ma10 * 0.985,
        "volume_shrink_pullback": vol5_20 < 0.9 and pct <= 0,
        "volume_breakout_signal": vol5_20 >= 1.1 and close >= recent_high20,
        "resistance_distance_pct": _pct_delta(recent_high20, close),
        # 上影线偏长计数（上影/全幅 ≥ 0.45），与 indicator_computer.upper_shadow_count
        # 及生产 `_build_stock_from_bars` 的 `_upper_shadow_count` 口径一致。
        "upper_shadow_count20": sum(
            1
            for item in prior[-20:]
            if _upper_shadow_ratio(item) is not None and _upper_shadow_ratio(item) >= 0.45
        ),
        "is_limit_up": is_limit_up,
        "limit_up_consecutive_days": 1 if is_limit_up else 0,
        "limit_up_open_times": 0,
        "limit_up_fd_amount_yi": 0.0,
        "limit_up_days20": sum(1 for item in prior[-20:] if _number(item.pct_chg) >= limit_pct - 0.2),
        "dragon_tiger_is_on_list": False,
        "dragon_tiger_recent_count": 0,
        "dragon_tiger_net_amount_yi": 0.0,
        "dragon_tiger_institution_net_buy_yi": 0.0,
        "dragon_tiger_net_buy_ratio": 0.0,
        "dragon_tiger_buy_seat_count": 0,
        "dragon_tiger_buy_dispersion": "未上榜",
        "dragon_tiger_inst_buy_ratio": 0.0,
        "dsa_trend_score": float(dsa_trend),
        "dsa_bias_score": float(dsa_bias),
        "dsa_volume_score": float(dsa_volume),
        "dsa_support_score": float(dsa_support),
        "dsa_macd_score": float(dsa_macd),
        "dsa_rsi_score": float(dsa_rsi),
        "dsa_signal_score": float(dsa_trend + dsa_bias + dsa_volume + dsa_support + dsa_macd + dsa_rsi),
        "market_regime": str((market_context or {}).get("regime") or "震荡"),
        "up_count": int((market_context or {}).get("up_count") or 0),
        "down_count": int((market_context or {}).get("down_count") or 0),
        "limit_up_count": int((market_context or {}).get("limit_up_count") or 0),
        "limit_down_count": int((market_context or {}).get("limit_down_count") or 0),
        "twenty_cm_risk_gate": "normal",
        "news_risk_count": 0,
        "announcement_negative_count": 0,
        "limit_up_overheat_score": 0.0,
        "capital_resonance_score": 0.0,
        "sector_ladder_completeness_score": 0.0,
        "is_main_theme": False,
        "market_sample_count": int((market_context or {}).get("sample_count") or 0),
        "data_completeness_score": 100.0,
        "evidence_quality_score": 100.0,
        # 供因子管线复刻生产聚合（_apply_relative_features / build_features）
        "free_mv_yi": 0.0,
        "total_mv_yi": 0.0,
        "main_net_inflow_yi": 0.0,
        "concept_sector_heat_rank": 999,
        # 回测无资金流池：声明缺失让 capital_resonance 因子走 price_volume_fallback
        # degradation 分支（否则 has_flow=True 用全零资金流字段算分，恒 39 无区分度，
        # 直接卡死依赖 capital_resonance_score>=45 的 setup，如 overnight-holding）。
        "data_quality": {
            "missing_fields": ["capital_flow"],
            "capital_flow_checked": False,
        },
    }
    return stock


def _upper_shadow_ratio(item: DailyKbar) -> float | None:
    """单根日K的上影线占比（上影 / 全幅）；无效数据返回 None。"""
    high = _number(item.high)
    low = _number(item.low)
    open_ = _number(item.open)
    close = _number(item.close)
    spread = high - low
    if spread <= 0:
        return None
    return (high - max(open_, close)) / spread


def _limit_pct_for_bar(row: DailyKbar, metadata: Mapping[str, Any]) -> float:
    """按证券身份返回回测板的涨跌停幅度：ST 优先，其次北交所/代码板位。

    与生产 `board_rules.limit_pct_for_stock` 一致：ST/北交所用更低幅度，
    创业/科创用 20cm，其余主板 10cm。回测信号行没有 quote 的 exchange
    字段，用 Stock 元数据里的名字与 ST 标记近似。
    """
    name = str(metadata.get("name") or "").upper()
    if bool(metadata.get("is_st")) or "ST" in name:
        return 5.0
    code = str(row.code)
    if code.startswith(("300", "301")):
        return 20.0
    if code.startswith(("688", "689")):
        return 20.0
    if code.startswith(("8", "4")):
        return 30.0
    return 10.0


def _rows_by_trade_date(rows: Sequence[DailyKbar]) -> dict[str, list[DailyKbar]]:
    grouped: dict[str, list[DailyKbar]] = defaultdict(list)
    for row in rows:
        grouped[str(row.trade_date)].append(row)
    return grouped


def _apply_shared_factors(stocks: list[dict[str, Any]], bundle: SnapshotBundle) -> None:
    """Replica of production ``build_features`` factor orchestration for research.

    Production applies ``apply_shared_shortline_factors`` with the day's quote
    snapshot; research has no quote snapshot, so it reconstructs the breadth
    aggregates from the signal artifact itself (industry statistics, sentiment
    phase, main industries) — the same inputs the capture already embeds in
    every stock.  Factor outputs are merged in place so the strategy sees
    sector_ladder / capital_resonance / limit_up_overheat / leader /
    dragon_tiger / twenty_cm_risk exactly like production.
    """
    if not stocks:
        return
    if not stocks:
        return
    # ``capture`` already computes these breadth values from the full signal-date
    # market.  Keep them when a caller evaluates a target-code subset; deriving
    # sentiment from that subset changes the strategy input and breaks parity with
    # the production feature builder.
    first = stocks[0]
    snapshot = {
        "sample_count": int(first.get("market_sample_count") or len(stocks)),
        "up_count": int(first.get("up_count") or 0),
        "down_count": int(first.get("down_count") or 0),
        "limit_up_count": int(first.get("limit_up_count") or 0),
        "limit_down_count": int(first.get("limit_down_count") or 0),
        "main_industries": [
            str(stock.get("industry") or "")
            for stock in stocks
            if stock.get("is_main_theme")
        ],
    }
    apply_shared_shortline_factors(stocks, snapshot)


def _historical_market_context(rows: Sequence[DailyKbar]) -> dict[str, Any]:
    """Build the production-equivalent breadth fields from local daily bars.

    This intentionally mirrors ``market_source.snapshot._breadth_from_counts``:
    research evaluates a strategy with the market information known at the
    signal close, while preserving a local-only, immutable input artifact.
    """

    sample_count = len(rows)
    up_count = sum(1 for row in rows if _number(row.pct_chg) > 0)
    down_count = sum(1 for row in rows if _number(row.pct_chg) < 0)
    limit_up_count = sum(1 for row in rows if _number(row.pct_chg) >= 9.8)
    limit_down_count = sum(1 for row in rows if _number(row.pct_chg) <= -9.8)
    up_ratio = up_count / max(1, sample_count)
    limit_up_ratio = limit_up_count / max(1, sample_count)
    if up_ratio >= 0.62 and limit_up_ratio >= 0.03:
        regime = "强势"
    elif up_ratio >= 0.55:
        regime = "结构性强势"
    elif up_ratio >= 0.42:
        regime = "震荡"
    else:
        regime = "弱势"
    return {
        "sample_count": sample_count,
        "up_count": up_count,
        "down_count": down_count,
        "limit_up_count": limit_up_count,
        "limit_down_count": limit_down_count,
        "regime": regime,
    }


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
    policy_payload = _drop_none_values(dict(payload.get("execution_policy") or {}))
    if isinstance(policy_payload.get("entry"), Mapping):
        # Nested canonical payload persisted by current CanonicalValue contracts.
        execution_policy = execution_policy_from_payload(policy_payload)
    else:
        # Legacy flat API projection retained for pre-refactor stored rows.
        execution_policy = ExecutionPolicy(**policy_payload)
    return BacktestRequest(
        schema_version=int(payload.get("schema_version", 1)),
        strategy_id=str(payload.get("strategy_id") or ""),
        strategy_version=str(payload.get("strategy_version") or "") or None,
        date_selector=date_selector,
        execution_policy=execution_policy,
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


def _drop_none_values(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _drop_none_values(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [_drop_none_values(item) for item in value]
    return value


def _validate_snapshot_selector(request: BacktestRequest, signal_dates: Sequence[str]) -> None:
    dates = tuple(signal_dates)
    if isinstance(request.date_selector, SingleDate):
        compatible = dates == (request.date_selector.signal_date,)
    else:
        compatible = bool(dates) and all(
            request.date_selector.start_date <= signal_date <= request.date_selector.end_date
            for signal_date in dates
        )
    if not compatible:
        raise BacktestValidationError(
            BacktestErrorCode.INVALID_REQUEST,
            "snapshot signal dates are incompatible with the request date selector",
            details={
                "snapshot_signal_dates": list(dates),
                "request_date_selector": request.date_selector.canonical_payload(),
            },
        )


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
        f"{content_hash(payload)[:36]}"
    )


def _deterministic_kbar_rows(rows: Iterable[DailyKbar]) -> list[DailyKbar]:
    return sorted(rows, key=lambda row: (str(row.trade_date), str(row.code)))


def _trade_id(backtest_id: str, trade: Trade) -> str:
    return f"btt_{canonical_fingerprint({'backtest_id': backtest_id, 'trade': trade.canonical_payload()})[:48]}"


def _encode_artifact_payload(payload: Mapping[str, Any]) -> bytes:
    raw = canonical_json(payload).encode("utf-8")
    return _ZLIB_JSON_MAGIC + zlib.compress(raw, level=1)


def _decode_artifact_payload(payload: bytes, artifact_id: str) -> dict[str, Any]:
    try:
        raw = (
            zlib.decompress(payload[len(_ZLIB_JSON_MAGIC) :])
            if payload.startswith(_ZLIB_JSON_MAGIC)
            else payload
        )
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, zlib.error) as exc:
        raise SnapshotCaptureError("artifact payload is not canonical UTF-8 JSON", details={"artifact_id": artifact_id}) from exc
    if not isinstance(decoded, dict):
        raise SnapshotCaptureError("artifact payload root must be an object", details={"artifact_id": artifact_id})
    return decoded


def _artifact_ref_from_payload(value: Any) -> ArtifactRef:
    if not isinstance(value, Mapping):
        raise SnapshotCaptureError("stored artifact reference is malformed")
    try:
        return ArtifactRef(
            artifact_id=str(value.get("artifact_id") or ""),
            signal_date=str(value.get("signal_date") or ""),
            artifact_scope=str(value.get("artifact_scope") or ""),
            artifact_type=str(value.get("artifact_type") or ""),
            schema_version=int(value.get("schema_version") or 0),
            row_count=int(value.get("row_count") or 0),
            content_hash=str(value.get("content_hash") or ""),
            content_encoding=str(value.get("content_encoding") or "json"),
        )
    except (TypeError, ValueError, BacktestValidationError) as exc:
        raise SnapshotCaptureError("stored artifact reference is invalid") from exc


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


def _has_valid_ohlc(row: DailyKbar) -> bool:
    return all(_number(getattr(row, field_name, 0)) > 0 for field_name in ("open", "high", "low", "close"))


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
    "DataReadinessPort",
    "LocalDataReadinessAdapter",
    "LocalResearchSnapshotBuilder",
    "ResearchBacktestRunner",
    "ResearchBacktestRunnerError",
    "SnapshotCaptureError",
    "SnapshotBundle",
    "StrategyUnavailableError",
]
