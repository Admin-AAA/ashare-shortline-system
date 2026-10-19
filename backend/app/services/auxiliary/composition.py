"""Explicit S4 production composition for registry-backed auxiliary lifecycle.

No domain object in this module opens a database connection or calls ``get_pro``.  The
application composition boundary must provide one caller-owned SQLAlchemy connection,
a raw provider adapter, and state-dataset lifecycle adapters.  Event feeds are routed
through immutable snapshot handlers and active reads have no legacy-table fallback.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import uuid
from typing import Any

from sqlalchemy.engine import Connection

from app.services.auxiliary.block_trade_snapshot import ActiveBlockTradeProjection
from app.services.auxiliary.contracts import (
    AuxiliaryDatasetDescriptor,
    AuxiliaryDatasetRequest,
    AuxiliaryInstrument,
    CoveragePolicy,
    RefreshMode,
    RefreshPolicy,
)
from app.services.auxiliary.event_snapshot import (
    ActiveEventSnapshotReader,
    BatchIdFactory,
    EventSnapshotAuxiliaryHandler,
    EventSnapshotRefreshService,
    EventSnapshotRequest,
    TushareRawProviderPort,
    TushareSinglePageEventContract,
    TushareSinglePageEventProviderClient,
    EventSnapshotPilot,
)
from app.services.auxiliary.orchestrator import AuxiliaryDataOrchestrator
from app.services.auxiliary.production import (
    AUXILIARY_DATASET_INVENTORY,
    AuxiliaryDatasetInventoryEntry,
    AuxiliaryInventoryError,
    AuxiliarySchemaReadiness,
    StandardAuxiliaryDatasetAdapters,
    assemble_standard_auxiliary_descriptors,
    validate_auxiliary_inventory,
)
from app.services.auxiliary.registry import AuxiliaryDatasetRegistry, build_auxiliary_registry
from app.services.auxiliary.snapshot_codec import decode_canonical_payload
from app.services.auxiliary.snapshot_contracts import ImmutablePartitionSnapshot
from app.services.auxiliary.snapshot_repository import SQLAlchemyImmutablePartitionSnapshotRepository


# Only publishable event feeds belong to the default production composition. Each
# declared cap and schema is verified by a read-only raw-envelope probe before use.
EVENT_SNAPSHOT_CONTRACTS: Mapping[str, TushareSinglePageEventContract] = {
    "block_trade": TushareSinglePageEventContract(
        dataset="block_trade",
        endpoint="block_trade",
        documented_fields=("ts_code", "trade_date", "price", "vol", "amount", "buyer", "seller"),
        documented_single_request_cap=1000,
        documentation_url="https://tushare.pro/document/2?doc_id=161",
    ),
    "share_float": TushareSinglePageEventContract(
        dataset="share_float",
        endpoint="share_float",
        documented_fields=(
            "ts_code", "ann_date", "float_date", "float_share", "float_ratio", "holder_name", "share_type",
        ),
        documented_single_request_cap=6000,
        documentation_url="https://tushare.pro/document/2?doc_id=160",
    ),
    "holder_trade": TushareSinglePageEventContract(
        dataset="holder_trade",
        endpoint="stk_holdertrade",
        documented_fields=(
            "ts_code", "ann_date", "holder_name", "holder_type", "in_de", "change_vol", "change_ratio",
            "after_share", "after_ratio", "avg_price", "total_share", "begin_date", "close_date",
        ),
        # promax/quantdata 实测（2026-10-19，start/end 30 天窗 2301 行）只回 11 字段，无 begin_date/close_date；
        # 旧 contract 只认 13 字段 → 10-09 后每日 event_provider_unknown_schema、0 行。
        alternate_field_schemas=((
            "ts_code", "ann_date", "holder_name", "holder_type", "in_de", "change_vol", "change_ratio",
            "after_share", "after_ratio", "avg_price", "total_share",
        ),),
        documented_single_request_cap=3000,
        documentation_url="https://tushare.pro/document/2?doc_id=175",
    ),
    "news_announcement": TushareSinglePageEventContract(
        dataset="news_announcement",
        endpoint="anns_d",
        # Tushare anns_d 实际返回 (2026-08-01 实测): ts_code, symbol, name, title,
        # ann_type, ann_date, ann_time, url (8 字段全量, 顺序即此). completeness_for
        # 用 envelope.fields != documented_fields 做有序元组精确匹配，旧 contract
        # (6 字段含 rec_time) 不匹配 → event_provider_unknown_schema → news 补数
        # fallback 逐代码慢路径 → post_sync 卡死 → formal run data_quality_blocked
        # → 0 推荐（D 项前瞻样本阻塞根因）。
        documented_fields=(
            "ts_code", "symbol", "name", "title", "ann_type",
            "ann_date", "ann_time", "url",
        ),
        # akshare 备源（stock_notice_report，7f4c394）同 8 字段 + source 列（2026-10-19 实测）。
        alternate_field_schemas=((
            "ts_code", "symbol", "name", "title", "ann_type", "ann_date", "ann_time", "url", "source",
        ),),
        # akshare 按页取尽整日全市场（09-01 实测 68 页 6722 行），2000 单次上限只约束 Tushare 本体。
        exhaustive_field_schemas=((
            "ts_code", "symbol", "name", "title", "ann_type", "ann_date", "ann_time", "url", "source",
        ),),
        documented_single_request_cap=2000,
        documentation_url="https://tushare.pro/document/2?doc_id=176",
    ),
}

# This contract is deliberately not part of EVENT_SNAPSHOT_CONTRACTS and cannot be
# registered by build_auxiliary_production_composition. It supports only the explicit
# audit boundary in application.py; cap/pagination/count/has-more semantics are absent.
INSTITUTIONAL_SURVEY_AUDIT_CONTRACT = TushareSinglePageEventContract(
    dataset="institutional_survey",
    endpoint="stk_surv",
    documented_fields=(
        "ts_code", "name", "surv_date", "fund_visitors", "rece_place", "rece_mode", "rece_org", "org_type", "comp_rece",
    ),
    documented_single_request_cap=None,
    documentation_url=None,
)


Clock = Callable[[], datetime]
TsCodeFormatter = Callable[[AuxiliaryInstrument], str]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _compact_day(value: date) -> str:
    return value.strftime("%Y%m%d")


def _iso_day(value: object) -> str:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text[:10]


def default_tushare_code(instrument: AuxiliaryInstrument) -> str:
    """Convert a public instrument reference without acquiring a provider client."""
    code = instrument.code.strip()
    if "." in code:
        return code
    exchange = (instrument.exchange or "").strip().upper()
    if exchange:
        suffix = {"SH": "SH", "SSE": "SH", "SZ": "SZ", "SZSE": "SZ", "BJ": "BJ", "BSE": "BJ"}.get(exchange)
        if suffix is None:
            raise AuxiliaryInventoryError(f"unsupported event-snapshot exchange: {exchange}")
        return f"{code}.{suffix}"
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    return f"{code}.SH" if code.startswith(("5", "6", "9")) else f"{code}.SZ"


def _instrument_partitions(
    request: AuxiliaryDatasetRequest,
    *,
    ts_code_formatter: TsCodeFormatter,
) -> tuple[EventSnapshotRequest, ...]:
    return tuple(
        EventSnapshotRequest.from_mapping({"ts_code": ts_code_formatter(instrument)})
        for instrument in request.instruments
    )


def _single_trade_date_partition(request: AuxiliaryDatasetRequest) -> tuple[EventSnapshotRequest, ...]:
    return (EventSnapshotRequest.from_mapping({"trade_date": _compact_day(request.trade_date)}),)


def _news_date_partitions(request: AuxiliaryDatasetRequest) -> tuple[EventSnapshotRequest, ...]:
    # Existing news reads use a seven-day local window.  Each announcement day is an
    # independent provider partition and must have its own active snapshot pointer.
    return tuple(
        EventSnapshotRequest.from_mapping({"ann_date": _compact_day(request.trade_date - timedelta(days=offset))})
        for offset in range(7)
    )


def _share_float_market_partition(request: AuxiliaryDatasetRequest) -> tuple[EventSnapshotRequest, ...]:
    """一次拉全市场未来 30 天解禁，避免按 5000 只股票逐股请求。"""
    return (
        EventSnapshotRequest.from_mapping(
            {
                "start_date": _compact_day(request.trade_date),
                "end_date": _compact_day(request.trade_date + timedelta(days=30)),
            }
        ),
    )


def _holder_trade_market_partition(request: AuxiliaryDatasetRequest) -> tuple[EventSnapshotRequest, ...]:
    """一次拉全市场最近 30 天股东增减持，避免逐股请求。"""
    return (
        EventSnapshotRequest.from_mapping(
            {
                "start_date": _compact_day(request.trade_date - timedelta(days=30)),
                "end_date": _compact_day(request.trade_date),
            }
        ),
    )


def _rows(snapshots: Sequence[ImmutablePartitionSnapshot]) -> tuple[Mapping[str, Any], ...]:
    records: list[Mapping[str, Any]] = []
    for snapshot in snapshots:
        for row in snapshot.rows:
            decoded = decode_canonical_payload(row.lossless_payload)
            if not isinstance(decoded, Mapping):
                raise AuxiliaryInventoryError("event snapshot row must decode to a mapping")
            records.append(decoded)
    return tuple(records)


def _code(value: object) -> str:
    return str(value or "").strip().split(".")[0]


def _block_trade_view(
    snapshots: tuple[ImmutablePartitionSnapshot, ...],
    request: AuxiliaryDatasetRequest,
) -> dict[str, list[dict[str, Any]]]:
    if not snapshots:
        return {}
    # One date partition is requested.  The established projection retains duplicate
    # immutable rows and uses only the passed active snapshot, never block_trades.
    return ActiveBlockTradeProjection.project(snapshots[0], codes=request.requested_codes)


def _share_float_view(
    snapshots: tuple[ImmutablePartitionSnapshot, ...],
    request: AuxiliaryDatasetRequest,
) -> dict[str, list[dict[str, Any]]]:
    cutoff = request.trade_date + timedelta(days=30)
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _rows(snapshots):
        code = _code(row.get("ts_code"))
        float_date = _iso_day(row.get("float_date"))
        if code not in request.requested_codes or not float_date:
            continue
        try:
            parsed = date.fromisoformat(float_date)
        except ValueError:
            continue
        if request.trade_date <= parsed <= cutoff:
            result[code].append(
                {
                    "ann_date": _iso_day(row.get("ann_date")),
                    "float_date": float_date,
                    "float_share": row.get("float_share"),
                    "float_ratio": row.get("float_ratio"),
                    "holder_name": row.get("holder_name"),
                    "share_type": row.get("share_type"),
                }
            )
    return dict(result)


def _holder_trade_view(
    snapshots: tuple[ImmutablePartitionSnapshot, ...],
    request: AuxiliaryDatasetRequest,
) -> dict[str, dict[str, Any]]:
    cutoff = request.trade_date - timedelta(days=30)
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in _rows(snapshots):
        code = _code(row.get("ts_code"))
        ann_date = _iso_day(row.get("ann_date"))
        try:
            current = date.fromisoformat(ann_date)
        except ValueError:
            continue
        if code in request.requested_codes and current >= cutoff:
            grouped[code].append(row)
    result: dict[str, dict[str, Any]] = {}
    for code, records in grouped.items():
        total_ratio = sum(float(item.get("change_ratio") or 0.0) for item in records)
        result[code] = {
            "holder_trade_count": len(records),
            "holder_trade_increase_count": sum(item.get("in_de") == "IN" for item in records),
            "holder_trade_decrease_count": sum(item.get("in_de") == "DE" for item in records),
            "holder_trade_net_ratio": round(total_ratio, 4),
            "holder_trade_latest_ann": max(_iso_day(item.get("ann_date")) for item in records),
        }
    return result


def _news_view(
    snapshots: tuple[ImmutablePartitionSnapshot, ...],
    request: AuxiliaryDatasetRequest,
) -> dict[str, list[dict[str, Any]]]:
    """Project Tushare announcements without inventing an article identifier."""
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _rows(snapshots):
        code = _code(row.get("ts_code"))
        if code not in request.requested_codes:
            continue
        result[code].append(
            {
                "source": "tushare_pro",
                "code": code,
                "announce_date": _iso_day(row.get("ann_date")),
                "name": row.get("name"),
                "title": row.get("title"),
                "url": row.get("url"),
                "rec_time": row.get("ann_time"),
            }
        )
    for entries in result.values():
        entries.sort(key=lambda item: str(item.get("announce_date") or ""), reverse=True)
    return dict(result)


@dataclass(frozen=True, slots=True)
class AuxiliaryProductionComposition:
    """Fully injected S4 assembly, including generic state and event descriptors."""

    registry: AuxiliaryDatasetRegistry
    orchestrator: AuxiliaryDataOrchestrator
    event_contracts: Mapping[str, TushareSinglePageEventContract]


def _default_batch_id(dataset: str, request: EventSnapshotRequest) -> str:
    del request
    return f"aux-{dataset}-{uuid.uuid4().hex}"


def _event_handler(
    *,
    contract: TushareSinglePageEventContract,
    connection: Connection,
    raw_provider: TushareRawProviderPort,
    partition_requests: Callable[[AuxiliaryDatasetRequest], tuple[EventSnapshotRequest, ...]],
    build_view: Callable[[tuple[ImmutablePartitionSnapshot, ...], AuxiliaryDatasetRequest], Any],
    batch_id_factory: BatchIdFactory,
    clock: Clock,
    view_key: str,
) -> EventSnapshotAuxiliaryHandler[Any]:
    repository = SQLAlchemyImmutablePartitionSnapshotRepository(connection, clock=clock)
    return EventSnapshotAuxiliaryHandler(
        contract=contract,
        refresh_service=EventSnapshotRefreshService(
            EventSnapshotPilot(
                contract,
                TushareSinglePageEventProviderClient(raw_provider, contract),
            ),
            repository,
        ),
        reader=ActiveEventSnapshotReader(contract, repository),
        partition_requests=partition_requests,
        build_view=build_view,
        batch_id_factory=batch_id_factory,
        clock=clock,
        view_key=view_key,
    )


def build_auxiliary_production_composition(
    *,
    connection: Connection,
    raw_provider: TushareRawProviderPort,
    state_adapters: Mapping[str, StandardAuxiliaryDatasetAdapters],
    schema_readiness: AuxiliarySchemaReadiness,
    clock: Clock | None = None,
    batch_id_factory: BatchIdFactory = _default_batch_id,
    ts_code_formatter: TsCodeFormatter = default_tushare_code,
    inventory: Sequence[AuxiliaryDatasetInventoryEntry] = AUXILIARY_DATASET_INVENTORY,
    event_contracts: Mapping[str, TushareSinglePageEventContract] = EVENT_SNAPSHOT_CONTRACTS,
) -> AuxiliaryProductionComposition:
    """Build one frozen Registry on an injected transaction-bound connection.

    State entries use the ordinary descriptor lifecycle with injected atomic-upsert
    adapters.  Event entries use typed custom handlers but return the identical audit,
    error, result, and Coordinator-facing contract.  No handler creates a Session,
    acquires Tushare credentials, or reads a legacy projection table.
    """
    effective_clock = clock or _utc_now
    entries = tuple(inventory)
    validate_auxiliary_inventory(entries, require_required_categories=entries == AUXILIARY_DATASET_INVENTORY)
    state_entries = tuple(entry for entry in entries if entry.is_state_dataset)
    event_entries = tuple(entry for entry in entries if entry.is_snapshot_dataset)
    expected_contracts = {entry.key for entry in event_entries}
    if set(event_contracts) != expected_contracts:
        raise AuxiliaryInventoryError(
            f"event contract inventory mismatch: expected={sorted(expected_contracts)!r}; actual={sorted(event_contracts)!r}"
        )

    descriptors: list[AuxiliaryDatasetDescriptor[Any, Any, Any]] = list(
        assemble_standard_auxiliary_descriptors(
            state_adapters,
            schema_readiness=schema_readiness,
            inventory=state_entries,
        )
    )
    for entry in event_entries:
        contract = event_contracts[entry.key]
        if contract.dataset != entry.key or contract.endpoint != entry.endpoint or contract.provider != entry.provider:
            raise AuxiliaryInventoryError(f"event contract does not match frozen inventory: {entry.key}")
        if entry.key == "block_trade":
            partitions = _single_trade_date_partition
            view = _block_trade_view
        elif entry.key == "share_float":
            partitions = _share_float_market_partition
            view = _share_float_view
        elif entry.key == "holder_trade":
            partitions = _holder_trade_market_partition
            view = _holder_trade_view
        elif entry.key == "news_announcement":
            partitions = _news_date_partitions
            view = _news_view
        else:  # pragma: no cover - guarded by the frozen inventory validation above
            raise AuxiliaryInventoryError(f"unsupported event snapshot dataset: {entry.key}")
        descriptors.append(
            AuxiliaryDatasetDescriptor(
                key=entry.key,
                view_key=entry.view_key,
                natural_key=("batch_id", "row_ordinal"),
                coverage_policy=CoveragePolicy(),
                refresh_policy=RefreshPolicy(RefreshMode.ALWAYS),
                custom_handler=_event_handler(
                    contract=contract,
                    connection=connection,
                    raw_provider=raw_provider,
                    partition_requests=partitions,
                    build_view=view,
                    batch_id_factory=batch_id_factory,
                    clock=effective_clock,
                    view_key=entry.view_key,
                ),
            )
        )
    registry = build_auxiliary_registry(descriptors)
    return AuxiliaryProductionComposition(
        registry=registry,
        orchestrator=AuxiliaryDataOrchestrator(registry, clock=effective_clock),
        event_contracts=dict(event_contracts),
    )


__all__ = [
    "AuxiliaryProductionComposition",
    "EVENT_SNAPSHOT_CONTRACTS",
    "INSTITUTIONAL_SURVEY_AUDIT_CONTRACT",
    "build_auxiliary_production_composition",
    "default_tushare_code",
]
