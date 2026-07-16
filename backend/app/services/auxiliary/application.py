"""The single S4 application/composition boundary for auxiliary data.

Callers supply an existing SQLAlchemy ``Session``.  This boundary obtains that
Session's connection, optionally acquires one Tushare client for a refresh, composes
the frozen registry, and leaves commit/rollback ownership with the caller.  It is the
only S4 production module allowed to call ``get_pro``.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from types import MappingProxyType
from typing import Any
import uuid

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.services.auxiliary.composition import (
    AuxiliaryProductionComposition,
    INSTITUTIONAL_SURVEY_AUDIT_CONTRACT,
    TushareRawProviderPort,
    build_auxiliary_production_composition,
    default_tushare_code,
)
from app.services.auxiliary.contracts import AuxiliaryInstrument, AuxiliaryOrchestratorResult
from app.services.auxiliary.event_snapshot import (
    EventSnapshotAuditCaptureResult,
    EventSnapshotAuditCaptureService,
    EventSnapshotPilot,
    EventSnapshotRequest,
    TushareSinglePageEventProviderClient,
)
from app.services.auxiliary.snapshot_repository import SQLAlchemyImmutablePartitionSnapshotRepository
from app.services.auxiliary.production import (
    AUXILIARY_DATASET_INVENTORY,
    AUXILIARY_UNIQUE_MIGRATION_PLANS,
    AuxiliaryDatasetInventoryEntry,
    AuxiliaryInventoryError,
    AuxiliarySchemaReadiness,
    StandardAuxiliaryDatasetAdapters,
)
from app.services.auxiliary.state_adapters import TushareStateProvider, build_standard_state_adapters


class _PointerOnlyRawProvider:
    """Fails closed if a refresh-disabled call ever attempts provider I/O."""

    def fetch_raw(self, api_name: str, *, params=None, fields: str = "") -> Any:
        del params, fields
        raise RuntimeError(f"provider I/O is forbidden while refresh is disabled: {api_name}")


class S4SchemaReadiness:
    """Check S4 state unique-key evidence at the composition boundary.

    MySQL execution checks both the recorded migration version and the deployed unique
    shape.  SQLite is only a local parity mode used by injected tests; it relies on the
    model metadata and does not claim production MySQL acceptance.
    """

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def assert_ready(self, entry: AuxiliaryDatasetInventoryEntry) -> None:
        if not entry.is_state_dataset:
            return
        plan = AUXILIARY_UNIQUE_MIGRATION_PLANS[entry.key]
        if self._connection.dialect.name != "mysql":
            self._assert_metadata_shape(entry)
            return
        applied = self._connection.scalar(
            text("SELECT COUNT(*) FROM schema_migrations WHERE version = :version"),
            {"version": plan.version},
        )
        if int(applied or 0) != 1:
            raise AuxiliaryInventoryError(
                f"S4 state migration is not recorded for {entry.key}: {plan.version}"
            )
        inspector = inspect(self._connection)
        shapes = {
            (str(item.get("name") or ""), tuple(item.get("column_names") or ()), bool(item.get("unique")))
            for item in inspector.get_indexes(entry.table_name)
        }
        shapes.update(
            (str(item.get("name") or ""), tuple(item.get("column_names") or ()), True)
            for item in inspector.get_unique_constraints(entry.table_name)
        )
        expected = (plan.declaration.index, entry.natural_key, True)
        if expected not in shapes:
            raise AuxiliaryInventoryError(f"S4 state unique shape is not deployed for {entry.key}")

    @staticmethod
    def _assert_metadata_shape(entry: AuxiliaryDatasetInventoryEntry) -> None:
        expected = entry.natural_key
        for constraint in entry.model.__table__.constraints:
            columns = tuple(column.name for column in constraint.columns)
            if columns == expected:
                return
        for index in entry.model.__table__.indexes:
            columns = tuple(column.name for column in index.columns)
            if tuple(column.name for column in index.columns) == expected and index.unique:
                return
        raise AuxiliaryInventoryError(f"state model metadata lacks unique natural key: {entry.key}")


@dataclass(frozen=True, slots=True)
class InstitutionalSurveyAuditRequest:
    """Explicit caller input for non-publishable institutional-survey evidence."""

    instruments: tuple[AuxiliaryInstrument, ...]
    requested_at: datetime

    def __post_init__(self) -> None:
        if not self.instruments:
            raise AuxiliaryInventoryError("institutional survey audit requires at least one instrument")
        if self.requested_at.tzinfo is None or self.requested_at.utcoffset() is None:
            raise AuxiliaryInventoryError("institutional survey audit requested_at must include a timezone")


@dataclass(frozen=True, slots=True)
class InstitutionalSurveyAuditCapture:
    """Typed audit result with immutable captures only; it intentionally has no view."""

    request: InstitutionalSurveyAuditRequest
    captures: tuple[EventSnapshotAuditCaptureResult, ...]


@dataclass(frozen=True, slots=True)
class AuxiliaryApplicationResult:
    """Immutable application result with typed audit plus feature-compatible views."""

    composition: AuxiliaryProductionComposition
    orchestrated: AuxiliaryOrchestratorResult
    feature_views: Mapping[str, Any]

    @property
    def audit_payload(self) -> Mapping[str, Any]:
        return MappingProxyType({
            item.dataset_key: {
                "view_key": item.view_key,
                "record_count": item.coverage.record_count,
                "coverage_sufficient": item.coverage.sufficient,
                "refresh_required": item.refresh.required,
                "refresh_reason": item.refresh.reason.value,
                "errors": tuple(error.code.value for error in item.errors),
                "audit": tuple(
                    {
                        "stage": event.stage.value,
                        "outcome": event.outcome.value,
                        "details": dict(event.details),
                    }
                    for event in item.audit
                ),
            }
            for item in self.orchestrated.results
        })


_EVENT_FEATURE_KEYS = MappingProxyType({
    "news_announcement": "news",
    "block_trade": "block_trades",
    "share_float": "share_floats_upcoming",
    "holder_trade": "holder_trades",
})


def _feature_views(result: AuxiliaryOrchestratorResult) -> Mapping[str, Any]:
    """Project declared descriptor views without a legacy refresh/load dispatcher."""
    views: dict[str, Any] = {
        "limit_up": {}, "broken_limit_up": {}, "concept_sectors": {},
        "capital_flows": {}, "dragon_tigers": {}, "news": {}, "auctions": {},
        "pledges": {}, "broker_recs": {}, "share_floats_upcoming": {},
        "margin_details": {}, "block_trades": {}, "hsgt_top10": {},
        "hsgt_flow": None, "finas": {}, "holder_numbers": {},
        "holder_trades": {}, "coverage": {},
    }
    coverage: dict[str, Any] = {}
    for item in result.results:
        coverage[item.dataset_key] = {
            "record_count": item.coverage.record_count,
            "covered_instruments": item.coverage.covered_instruments,
            "missing_instruments": item.coverage.missing_instruments,
            "sufficient": item.coverage.sufficient,
            "errors": tuple(error.code.value for error in item.errors),
        }
        if item.view is None:
            continue
        if item.dataset_key in _EVENT_FEATURE_KEYS:
            views[_EVENT_FEATURE_KEYS[item.dataset_key]] = item.view
        elif isinstance(item.view, Mapping):
            # State loaders declare their own public view keys.  This is a merge of
            # typed loader outputs, not a dataset-name switch or compatibility path.
            views.update(item.view)
    views["coverage"] = coverage
    return MappingProxyType(views)


def _instruments(values: Sequence[AuxiliaryInstrument | str]) -> tuple[AuxiliaryInstrument, ...]:
    result: list[AuxiliaryInstrument] = []
    seen: set[str] = set()
    for value in values:
        instrument = value if isinstance(value, AuxiliaryInstrument) else AuxiliaryInstrument(str(value))
        if instrument.code not in seen:
            result.append(instrument)
            seen.add(instrument.code)
    return tuple(result)


def _date(value: date | str) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def run_auxiliary_application(
    db: Session,
    *,
    instruments: Sequence[AuxiliaryInstrument | str],
    trade_date: date | str,
    refresh: bool,
    datasets: Sequence[str] | None = None,
    raw_provider: TushareRawProviderPort | None = None,
    state_adapters: Mapping[str, StandardAuxiliaryDatasetAdapters] | None = None,
    schema_readiness: AuxiliarySchemaReadiness | None = None,
) -> AuxiliaryApplicationResult:
    """Run S4 inventory through one frozen composition on the caller's transaction.

    No commit, rollback, Session construction, or event-table projection occurs here.
    For ``refresh=False`` no provider client is acquired and the injected sentinel
    guarantees pointer/current-state loads cannot accidentally make outbound calls.
    """
    connection = db.connection()
    effective_date = _date(trade_date)
    requested = tuple(datasets) if datasets is not None else tuple(
        entry.key for entry in AUXILIARY_DATASET_INVENTORY
    )
    if "institutional_survey" in requested:
        raise AuxiliaryInventoryError(
            "institutional_survey is audit-only; use capture_institutional_survey_audit explicitly"
        )
    provider: Any
    if not refresh:
        # A supplied provider must not weaken the pointer/current-state-only path.
        # The sentinel is passed through composition so any future custom handler
        # fails closed before it can make outbound provider I/O.
        provider = _PointerOnlyRawProvider()
    elif raw_provider is not None:
        provider = raw_provider
    else:
        # Deliberately the sole get_pro call in the S4 production path.
        from app.services.tushare_client import get_pro
        provider = get_pro()

    adapters = state_adapters
    if adapters is None:
        state_provider = TushareStateProvider(provider, db) if refresh else None
        adapters = build_standard_state_adapters(
            connection=connection,
            session=db,
            provider=state_provider,
        )
    composition = build_auxiliary_production_composition(
        connection=connection,
        raw_provider=provider,
        state_adapters=adapters,
        schema_readiness=schema_readiness or S4SchemaReadiness(connection),
    )
    orchestrated = composition.orchestrator.prepare(
        datasets=requested,
        instruments=_instruments(instruments),
        trade_date=effective_date,
        refresh=refresh,
    )
    return AuxiliaryApplicationResult(
        composition=composition,
        orchestrated=orchestrated,
        feature_views=_feature_views(orchestrated),
    )


def capture_institutional_survey_audit(
    db: Session,
    *,
    request: InstitutionalSurveyAuditRequest,
    raw_provider: TushareRawProviderPort,
) -> InstitutionalSurveyAuditCapture:
    """Persist raw `stk_surv` audit evidence without a production registry or pointer.

    The caller owns the Session transaction. Provider injection is mandatory: this
    boundary never acquires the standard production Tushare client and cannot return
    a feature view, read an active pointer, or fall back to legacy survey rows.
    """
    if not db.in_transaction():
        raise AuxiliaryInventoryError(
            "institutional survey audit requires an active caller-owned transaction"
        )
    connection = db.connection()
    repository = SQLAlchemyImmutablePartitionSnapshotRepository(connection)
    service = EventSnapshotAuditCaptureService(
        EventSnapshotPilot(
            INSTITUTIONAL_SURVEY_AUDIT_CONTRACT,
            TushareSinglePageEventProviderClient(
                raw_provider,
                INSTITUTIONAL_SURVEY_AUDIT_CONTRACT,
            ),
        ),
        repository,
    )
    captures: list[EventSnapshotAuditCaptureResult] = []
    for instrument in request.instruments:
        partition = EventSnapshotRequest.from_mapping(
            {"ts_code": default_tushare_code(instrument)}
        )
        captures.append(
            service.capture(
                batch_id=f"audit-institutional-survey-{uuid.uuid4().hex}",
                request=partition,
                requested_as_of=request.requested_at,
                fetched_at=datetime.now(timezone.utc),
            )
        )
    return InstitutionalSurveyAuditCapture(request=request, captures=tuple(captures))


def load_auxiliary_feature_views(
    db: Session,
    codes: Sequence[str],
    *,
    trade_date: date | str,
) -> Mapping[str, Any]:
    """Current-state/active-pointer read used by feature and prefilter consumers."""
    return run_auxiliary_application(
        db,
        instruments=tuple(codes),
        trade_date=trade_date,
        refresh=False,
    ).feature_views


def inject_auxiliary_prefilter_flags(
    stocks: Sequence[dict[str, Any]],
    views: Mapping[str, Any],
) -> int:
    """Inject already-loaded state facts before prefiltering without provider I/O."""
    limit_up = views.get("limit_up") if isinstance(views.get("limit_up"), Mapping) else {}
    dragon_tiger = views.get("dragon_tigers") if isinstance(views.get("dragon_tigers"), Mapping) else {}
    limit_up_hits = 0
    dragon_tiger_hits = 0
    for stock in stocks:
        code = str(stock.get("code") or "")
        if row := limit_up.get(code):
            limit_up_hits += 1
            stock["is_limit_up"] = True
            stock["limit_up_consecutive_days"] = int(row.get("limit_times") or 1)
            stock["limit_up_open_times"] = int(row.get("open_times") or 0)
            stock["limit_up_fd_amount_yi"] = float(row.get("fd_amount_yi") or 0.0)
            stock["limit_up_limit_amount_yi"] = float(row.get("limit_amount_yi") or 0.0)
            stock["limit_up_turnover_ratio"] = float(row.get("turnover_ratio") or 0.0)
            stock.setdefault("limit_up_days20", 1)
        else:
            stock.setdefault("is_limit_up", False)
            stock.setdefault("limit_up_consecutive_days", 0)
            stock.setdefault("limit_up_open_times", 0)
        stock.setdefault("twenty_cm_risk_gate", "not_applicable")
        if row := dragon_tiger.get(code):
            dragon_tiger_hits += 1
            net_amount_yi = float(row.get("net_amount_yi") or 0.0)
            amount_yi = float(stock.get("amount_yi") or 0.0)
            stock["dragon_tiger_is_on_list"] = True
            stock["dragon_tiger_net_amount_yi"] = net_amount_yi
            stock["dragon_tiger_institution_net_buy_yi"] = net_amount_yi
            stock["dragon_tiger_net_buy_ratio"] = round(min(1.0, max(-1.0, net_amount_yi / amount_yi)), 4) if amount_yi else 0.0
        else:
            stock.setdefault("dragon_tiger_is_on_list", False)
    return limit_up_hits + dragon_tiger_hits


__all__ = [
    "AuxiliaryApplicationResult",
    "InstitutionalSurveyAuditCapture",
    "InstitutionalSurveyAuditRequest",
    "S4SchemaReadiness",
    "capture_institutional_survey_audit",
    "inject_auxiliary_prefilter_flags",
    "load_auxiliary_feature_views",
    "run_auxiliary_application",
]
