"""Frozen S4 auxiliary inventory and dependency-injected descriptor assembly.

The declarations in this module are intentionally *not* migrations and perform no
DDL, database connection, or vendor call.  SQLAlchemy model metadata lets unit tests
audit that a declared natural key/update field exists in code, but it cannot prove the
same constraint is deployed in MySQL.  Every inventory entry therefore stays blocked
until a future versioned MySQL migration has performed all declared checks.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from app.models.tables import (
    AuctionData,
    BlockTrade,
    BrokerRecommend,
    CapitalFlow,
    ConceptSector,
    ConceptSectorStock,
    DragonTiger,
    FinaIndicator,
    HolderNumber,
    HolderTrade,
    HsgtFlow,
    HsgtTop10,
    InstitutionalSurvey,
    LimitUpBoard,
    MarginDetail,
    NewsAnnouncement,
    PledgeStat,
    ShareFloat,
)
from app.services.auxiliary.contracts import (
    AuxiliaryDatasetDescriptor,
    AuxiliaryFetcher,
    AuxiliaryLoader,
    AuxiliaryNormalizer,
    AuxiliaryRepository,
    CoveragePolicy,
    RefreshMode,
    RefreshPolicy,
)
from app.services.auxiliary.orchestrator import AuxiliaryDataOrchestrator, Clock
from app.services.auxiliary.registry import AuxiliaryDatasetRegistry, build_auxiliary_registry


class AuxiliaryInventoryError(ValueError):
    """Raised when a frozen inventory declaration is incomplete or contradictory."""


FORBIDDEN_PER_ROW_UNIQUE_DATASETS = frozenset(
    {"block_trade", "share_float", "holder_trade", "institutional_survey", "news_announcement"}
)


@dataclass(frozen=True, slots=True)
class AuxiliaryUniqueConstraintDeclaration:
    """Expected deployed unique-key capability for a mutable state dataset."""

    version: str
    dataset_key: str
    table: str
    index: str
    natural_key: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.dataset_key in FORBIDDEN_PER_ROW_UNIQUE_DATASETS:
            raise AuxiliaryInventoryError(f"snapshot dataset cannot use a row unique key: {self.dataset_key}")
        if not self.table or not self.index or not self.natural_key:
            raise AuxiliaryInventoryError("unique-key capability requires table, index and natural_key")


class AuxiliarySchemaMigrationBlockedError(RuntimeError):
    """Raised when code tries to enable a dataset without production MySQL evidence."""


class AuxiliaryPersistenceIdentity(str, Enum):
    """Approved persistence identity semantics for S4 production datasets."""

    STATE_ATOMIC_UPSERT = "state_atomic_upsert"
    IMMUTABLE_PARTITION_SNAPSHOT = "immutable_partition_snapshot"


class AuxiliaryWriteStrategy(str, Enum):
    """Legacy/current state-row update policy; snapshot datasets do not use it."""

    ATOMIC_UPSERT = "atomic_upsert"
    REPLACE_SCOPE = "replace_scope"
    APPEND_DEDUP = "append_dedup"


class MySQLVerificationRequirement(str, Enum):
    """Evidence required before a declared natural key may drive production writes."""

    VERSIONED_UNIQUE_MIGRATION = "versioned_unique_migration"
    STORED_DATA_DUPLICATE_PREFLIGHT = "stored_data_duplicate_preflight"
    TWO_CONNECTION_CONCURRENCY = "two_connection_concurrency"


class SnapshotVerificationRequirement(str, Enum):
    """Evidence required before an event-like snapshot dataset may be published."""

    SNAPSHOT_SCHEMA = "snapshot_schema"
    PROVIDER_CONTRACT = "provider_contract"
    PUBLISH_ACCEPTANCE = "publish_acceptance"


@dataclass(frozen=True, slots=True)
class AuxiliaryUpdatePolicyDeclaration:
    """Auditable overwrite/keep/latest declaration independent of a live database."""

    strategy: AuxiliaryWriteStrategy
    overwrite_fields: tuple[str, ...] = ()
    preserve_fields: tuple[str, ...] = ()
    latest_wins_field: str | None = None
    replace_scope_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        all_fields = (*self.overwrite_fields, *self.preserve_fields, *self.replace_scope_fields)
        if any(not field.strip() for field in all_fields):
            raise AuxiliaryInventoryError("update policy 字段不能为空")
        if len(self.overwrite_fields) != len(set(self.overwrite_fields)):
            raise AuxiliaryInventoryError("overwrite_fields 不能重复")
        if len(self.preserve_fields) != len(set(self.preserve_fields)):
            raise AuxiliaryInventoryError("preserve_fields 不能重复")
        if len(self.replace_scope_fields) != len(set(self.replace_scope_fields)):
            raise AuxiliaryInventoryError("replace_scope_fields 不能重复")
        if set(self.overwrite_fields) & set(self.preserve_fields):
            raise AuxiliaryInventoryError("同一字段不能同时 overwrite 和 preserve")
        if self.strategy is AuxiliaryWriteStrategy.ATOMIC_UPSERT and not self.overwrite_fields:
            raise AuxiliaryInventoryError("atomic_upsert 必须声明 overwrite_fields")
        if self.strategy is not AuxiliaryWriteStrategy.REPLACE_SCOPE and self.replace_scope_fields:
            raise AuxiliaryInventoryError("仅 replace_scope 可以声明 replace_scope_fields")
        if self.strategy is AuxiliaryWriteStrategy.REPLACE_SCOPE and not self.replace_scope_fields:
            raise AuxiliaryInventoryError("replace_scope 必须声明 replace_scope_fields")
        if self.latest_wins_field is not None:
            if not self.latest_wins_field.strip():
                raise AuxiliaryInventoryError("latest_wins_field 不能是空白字符串")
            if self.latest_wins_field not in self.overwrite_fields:
                raise AuxiliaryInventoryError("latest_wins_field 必须属于 overwrite_fields")


@dataclass(frozen=True, slots=True)
class AuxiliaryDatasetInventoryEntry:
    """One immutable source of truth for S4 persistence identity and readiness work."""

    key: str
    category: str
    view_key: str
    model: type[Any]
    natural_key: tuple[str, ...]
    update_policy: AuxiliaryUpdatePolicyDeclaration | None
    freshness_field: str
    identity_semantics: AuxiliaryPersistenceIdentity = AuxiliaryPersistenceIdentity.STATE_ATOMIC_UPSERT
    provider: str | None = None
    endpoint: str | None = None
    dependencies: tuple[str, ...] = ()
    minimum_instrument_coverage: float = 0.0
    coverage_instrument_field: str | None = None
    mysql_blockers: tuple[MySQLVerificationRequirement, ...] = (
        MySQLVerificationRequirement.VERSIONED_UNIQUE_MIGRATION,
        MySQLVerificationRequirement.STORED_DATA_DUPLICATE_PREFLIGHT,
        MySQLVerificationRequirement.TWO_CONNECTION_CONCURRENCY,
    )
    snapshot_blockers: tuple[SnapshotVerificationRequirement, ...] = ()

    def __post_init__(self) -> None:
        if not self.key.strip() or not self.category.strip() or not self.view_key.strip():
            raise AuxiliaryInventoryError("inventory key/category/view_key 均不能为空")
        if self.key in self.dependencies or len(self.dependencies) != len(set(self.dependencies)):
            raise AuxiliaryInventoryError("inventory dependencies 不能自引用或重复")
        if any(not dependency.strip() for dependency in self.dependencies):
            raise AuxiliaryInventoryError("inventory dependency 不能为空")
        if not 0.0 <= self.minimum_instrument_coverage <= 1.0:
            raise AuxiliaryInventoryError("minimum_instrument_coverage 必须在 [0, 1] 范围内")
        if self.minimum_instrument_coverage > 0 and not self.coverage_instrument_field:
            raise AuxiliaryInventoryError("按股票覆盖率校验必须声明 coverage_instrument_field")
        if not self.freshness_field.strip():
            raise AuxiliaryInventoryError("inventory freshness_field 不能为空")

        if self.identity_semantics is AuxiliaryPersistenceIdentity.STATE_ATOMIC_UPSERT:
            if not self.natural_key or len(self.natural_key) != len(set(self.natural_key)):
                raise AuxiliaryInventoryError("state inventory natural_key 必须是非重复的非空字段元组")
            if any(not field.strip() for field in self.natural_key):
                raise AuxiliaryInventoryError("state inventory natural_key 不能包含空白字段")
            if self.update_policy is None or self.update_policy.strategy is not AuxiliaryWriteStrategy.ATOMIC_UPSERT:
                raise AuxiliaryInventoryError("state inventory 必须声明 atomic-upsert update policy")
            if self.provider is not None or self.endpoint is not None:
                raise AuxiliaryInventoryError("state inventory 不得声明 snapshot provider/endpoint")
            if set(self.mysql_blockers) != set(MySQLVerificationRequirement):
                raise AuxiliaryInventoryError("state dataset 必须保留完整的 MySQL UNIQUE/并发验证阻断")
            if self.snapshot_blockers:
                raise AuxiliaryInventoryError("state dataset 不得声明 snapshot blockers")
            return

        if self.identity_semantics is AuxiliaryPersistenceIdentity.IMMUTABLE_PARTITION_SNAPSHOT:
            if self.key not in FORBIDDEN_PER_ROW_UNIQUE_DATASETS:
                raise AuxiliaryInventoryError("未批准的数据集不得声明 immutable partition snapshot identity")
            if self.natural_key:
                raise AuxiliaryInventoryError(
                    "snapshot dataset 不得声明 provider-derived per-row natural_key；"
                    "唯一行身份固定为 (batch_id, row_ordinal)"
                )
            if self.update_policy is not None:
                raise AuxiliaryInventoryError("snapshot dataset 不得复用 state-row update policy")
            if not self.provider or not self.provider.strip() or not self.endpoint or not self.endpoint.strip():
                raise AuxiliaryInventoryError("snapshot dataset 必须声明 provider 与 endpoint")
            if self.mysql_blockers:
                raise AuxiliaryInventoryError("snapshot dataset 不得等待 per-row UNIQUE/MySQL upsert 验收")
            if set(self.snapshot_blockers) != set(SnapshotVerificationRequirement):
                raise AuxiliaryInventoryError("snapshot dataset 必须等待 schema/provider/publish 三项验收")
            return

        raise AuxiliaryInventoryError(f"未知 persistence identity semantics: {self.identity_semantics!r}")

    @property
    def table_name(self) -> str:
        """Current lossy ORM projection table; never snapshot source identity."""
        return str(self.model.__tablename__)

    @property
    def row_identity(self) -> tuple[str, ...]:
        if self.identity_semantics is AuxiliaryPersistenceIdentity.IMMUTABLE_PARTITION_SNAPSHOT:
            return ("batch_id", "row_ordinal")
        return self.natural_key

    @property
    def is_state_dataset(self) -> bool:
        return self.identity_semantics is AuxiliaryPersistenceIdentity.STATE_ATOMIC_UPSERT

    @property
    def is_snapshot_dataset(self) -> bool:
        return self.identity_semantics is AuxiliaryPersistenceIdentity.IMMUTABLE_PARTITION_SNAPSHOT


@runtime_checkable
class AuxiliarySchemaReadiness(Protocol):
    """Future migration evidence gate; production composition must inject one."""

    def assert_ready(self, entry: AuxiliaryDatasetInventoryEntry) -> None:
        """Raise unless this entry's declared MySQL blockers have real evidence."""


class UnverifiedMySQLSchemaReadiness:
    """Default-safe readiness implementation for both approved identity models."""

    def assert_ready(self, entry: AuxiliaryDatasetInventoryEntry) -> None:
        if entry.is_snapshot_dataset:
            blockers = ", ".join(item.value for item in entry.snapshot_blockers)
            raise AuxiliarySchemaMigrationBlockedError(
                f"数据集 '{entry.key}' 仍被阻断：需要 immutable partition snapshot 的 {blockers}；"
                "不得以 per-row UNIQUE 代替 snapshot/provider/publish 验收"
            )
        blockers = ", ".join(item.value for item in entry.mysql_blockers)
        raise AuxiliarySchemaMigrationBlockedError(
            f"数据集 '{entry.key}' 仍被阻断：需要真实 MySQL 的 {blockers}；"
            "ORM metadata 不是 migration/并发验证证据"
        )


@dataclass(frozen=True, slots=True)
class StandardAuxiliaryDatasetAdapters:
    """The complete standard lifecycle injected for one production inventory entry.

    It is deliberately a skeleton: callers supply adapters backed by their own
    transaction/session, provider client, and loader.  No custom handler or central
    dataset switch is required for a newly registered standard dataset.
    """

    fetcher: AuxiliaryFetcher[Mapping[str, Any]]
    normalizer: AuxiliaryNormalizer[Mapping[str, Any], Mapping[str, Any]]
    repository: AuxiliaryRepository[Mapping[str, Any]]
    loader: AuxiliaryLoader[Any]


REQUIRED_AUXILIARY_CATEGORIES = frozenset(
    {
        "limit_up",
        "concept",
        "dragon_tiger",
        "capital_flow",
        "news",
        "margin",
        "hsgt",
        "block_trade",
        "share_float",
        "fina_indicator",
        "auction",
        "holder_number",
        "holder_trade",
    }
)

# institutional_survey is deliberately modeled separately: it can retain immutable
# raw evidence for audit but is never a required production input or feature view.
AUDIT_ONLY_AUXILIARY_DATASET_KEYS = frozenset({"institutional_survey"})


def _atomic(
    overwrite_fields: tuple[str, ...],
    *,
    preserve_fields: tuple[str, ...] = ("id", "created_at"),
    latest_wins_field: str | None = None,
) -> AuxiliaryUpdatePolicyDeclaration:
    return AuxiliaryUpdatePolicyDeclaration(
        strategy=AuxiliaryWriteStrategy.ATOMIC_UPSERT,
        overwrite_fields=overwrite_fields,
        preserve_fields=preserve_fields,
        latest_wins_field=latest_wins_field,
    )


AUXILIARY_DATASET_INVENTORY: tuple[AuxiliaryDatasetInventoryEntry, ...] = (
    AuxiliaryDatasetInventoryEntry(
        key="limit_up_board", category="limit_up", view_key="market_pool.limit_up", model=LimitUpBoard,
        natural_key=("code", "trade_date"), freshness_field="trade_date",
        update_policy=_atomic(("name", "pct_chg", "limit_type", "limit_times", "first_time", "last_time", "open_times", "up_stat", "limit_amount_yi", "turnover_ratio", "fd_amount_yi", "industry")),
    ),
    AuxiliaryDatasetInventoryEntry(
        key="concept_sector", category="concept", view_key="market_pool.concept_sectors", model=ConceptSector,
        natural_key=("sector_code", "trade_date"), freshness_field="trade_date",
        update_policy=_atomic(("sector_name", "member_count", "exchange", "list_date", "type", "heat_rank")),
    ),
    AuxiliaryDatasetInventoryEntry(
        key="concept_sector_stock", category="concept", view_key="market_pool.concept_sector_members", model=ConceptSectorStock,
        natural_key=("sector_code", "code", "trade_date"), freshness_field="trade_date", dependencies=("concept_sector",),
        update_policy=_atomic(("name",)),
    ),
    AuxiliaryDatasetInventoryEntry(
        key="capital_flow", category="capital_flow", view_key="market_pool.capital_flows", model=CapitalFlow,
        natural_key=("code", "trade_date"), freshness_field="trade_date",
        minimum_instrument_coverage=0.90, coverage_instrument_field="code",
        update_policy=_atomic(("main_net_inflow_yi", "super_large_net_inflow_yi", "large_net_inflow_yi", "medium_net_inflow_yi", "small_net_inflow_yi", "main_net_inflow_ratio", "cumulative_inflow_3d_yi", "cumulative_inflow_5d_yi", "cumulative_inflow_10d_yi")),
    ),
    AuxiliaryDatasetInventoryEntry(
        key="dragon_tiger", category="dragon_tiger", view_key="market_pool.dragon_tigers", model=DragonTiger,
        natural_key=("code", "trade_date"), freshness_field="trade_date",
        update_policy=_atomic(("name", "reason", "buy_amount_yi", "sell_amount_yi", "net_amount_yi")),
    ),
    AuxiliaryDatasetInventoryEntry(
        key="news_announcement", category="news", view_key="market_pool.news", model=NewsAnnouncement,
        natural_key=(), freshness_field="announce_date", update_policy=None,
        identity_semantics=AuxiliaryPersistenceIdentity.IMMUTABLE_PARTITION_SNAPSHOT,
        provider="tushare_pro", endpoint="anns_d", mysql_blockers=(),
        snapshot_blockers=tuple(SnapshotVerificationRequirement),
    ),
    AuxiliaryDatasetInventoryEntry(
        key="margin_detail", category="margin", view_key="market_pool.margin_details", model=MarginDetail,
        natural_key=("code", "trade_date"), freshness_field="trade_date",
        update_policy=_atomic(("rzye", "rqye", "rzmre", "rzche", "rzrqye", "rqyl")),
    ),
    AuxiliaryDatasetInventoryEntry(
        key="block_trade", category="block_trade", view_key="market_pool.block_trades", model=BlockTrade,
        natural_key=(), freshness_field="trade_date", update_policy=None,
        identity_semantics=AuxiliaryPersistenceIdentity.IMMUTABLE_PARTITION_SNAPSHOT,
        provider="tushare_pro", endpoint="block_trade", mysql_blockers=(),
        snapshot_blockers=tuple(SnapshotVerificationRequirement),
    ),
    AuxiliaryDatasetInventoryEntry(
        key="hsgt_flow", category="hsgt", view_key="market_pool.hsgt_flow", model=HsgtFlow,
        natural_key=("trade_date",), freshness_field="trade_date",
        update_policy=_atomic(("north_money", "south_money", "hgt", "sgt")),
    ),
    AuxiliaryDatasetInventoryEntry(
        key="hsgt_top10", category="hsgt", view_key="market_pool.hsgt_top10", model=HsgtTop10,
        natural_key=("code", "trade_date"), freshness_field="trade_date",
        update_policy=_atomic(("name", "close", "change_pct", "rank", "amount", "net_amount", "buy")),
    ),
    AuxiliaryDatasetInventoryEntry(
        key="pledge_stat", category="pledge", view_key="market_pool.pledges", model=PledgeStat,
        natural_key=("code", "end_date"), freshness_field="end_date",
        update_policy=_atomic(("pledge_count", "unrest_pledge", "rest_pledge", "pledge_ratio")),
    ),
    AuxiliaryDatasetInventoryEntry(
        key="broker_recommend", category="broker_recs", view_key="market_pool.broker_recs", model=BrokerRecommend,
        natural_key=("code", "month", "broker"), freshness_field="month",
        update_policy=_atomic(("name",)),
    ),
    AuxiliaryDatasetInventoryEntry(
        key="share_float", category="share_float", view_key="market_pool.share_floats_upcoming", model=ShareFloat,
        natural_key=(), freshness_field="float_date", update_policy=None,
        identity_semantics=AuxiliaryPersistenceIdentity.IMMUTABLE_PARTITION_SNAPSHOT,
        provider="tushare_pro", endpoint="share_float", mysql_blockers=(),
        snapshot_blockers=tuple(SnapshotVerificationRequirement),
    ),
    AuxiliaryDatasetInventoryEntry(
        key="fina_indicator", category="fina_indicator", view_key="market_pool.finas", model=FinaIndicator,
        natural_key=("code", "end_date"), freshness_field="ann_date",
        update_policy=_atomic(("ann_date", "roe", "grossprofit_margin", "netprofit_margin", "netprofit_yoy", "or_yoy", "eps", "bps"), preserve_fields=("id", "updated_at")),
    ),
    AuxiliaryDatasetInventoryEntry(
        key="auction", category="auction", view_key="market_pool.auctions", model=AuctionData,
        natural_key=("code", "trade_date"), freshness_field="trade_date",
        update_policy=_atomic(("vol", "price", "amount", "pre_close", "turnover_rate", "volume_ratio", "float_share")),
    ),
    AuxiliaryDatasetInventoryEntry(
        key="holder_number", category="holder_number", view_key="market_pool.holder_numbers", model=HolderNumber,
        natural_key=("code", "end_date"), freshness_field="end_date",
        update_policy=_atomic(("holder_num",)),
    ),
    AuxiliaryDatasetInventoryEntry(
        key="holder_trade", category="holder_trade", view_key="market_pool.holder_trades", model=HolderTrade,
        natural_key=(), freshness_field="ann_date", update_policy=None,
        identity_semantics=AuxiliaryPersistenceIdentity.IMMUTABLE_PARTITION_SNAPSHOT,
        provider="tushare_pro", endpoint="stk_holdertrade", mysql_blockers=(),
        snapshot_blockers=tuple(SnapshotVerificationRequirement),
    ),
)

# This declaration is intentionally outside the default production registry.
# It has no loader/view or active-pointer publication path; only the typed audit
# capture entrypoint may use its raw immutable snapshot identity.
INSTITUTIONAL_SURVEY_AUDIT_ONLY_DATASET = AuxiliaryDatasetInventoryEntry(
    key="institutional_survey",
    category="institutional_survey_audit_only",
    view_key="audit.institutional_survey",
    model=InstitutionalSurvey,
    natural_key=(),
    freshness_field="surv_date",
    update_policy=None,
    identity_semantics=AuxiliaryPersistenceIdentity.IMMUTABLE_PARTITION_SNAPSHOT,
    provider="tushare_pro",
    endpoint="stk_surv",
    mysql_blockers=(),
    snapshot_blockers=tuple(SnapshotVerificationRequirement),
)
AUXILIARY_AUDIT_ONLY_DATASET_INVENTORY: tuple[AuxiliaryDatasetInventoryEntry, ...] = (
    INSTITUTIONAL_SURVEY_AUDIT_ONLY_DATASET,
)
AUXILIARY_DATASET_BY_KEY: Mapping[str, AuxiliaryDatasetInventoryEntry] = MappingProxyType(
    {entry.key: entry for entry in AUXILIARY_DATASET_INVENTORY}
)
AUXILIARY_AUDIT_ONLY_DATASET_BY_KEY: Mapping[str, AuxiliaryDatasetInventoryEntry] = MappingProxyType(
    {entry.key: entry for entry in AUXILIARY_AUDIT_ONLY_DATASET_INVENTORY}
)
STATE_ATOMIC_UPSERT_DATASET_KEYS = frozenset(
    {
        "limit_up_board",
        "concept_sector",
        "concept_sector_stock",
        "capital_flow",
        "dragon_tiger",
        "margin_detail",
        "hsgt_flow",
        "hsgt_top10",
        "pledge_stat",
        "broker_recommend",
        "fina_indicator",
        "auction",
        "holder_number",
    }
)
IMMUTABLE_PARTITION_SNAPSHOT_DATASET_KEYS = frozenset(
    {"news_announcement", "block_trade", "share_float", "holder_trade"}
)
AUDIT_ONLY_IMMUTABLE_PARTITION_SNAPSHOT_DATASET_KEYS = frozenset(
    {"institutional_survey"}
)


class AuxiliaryMigrationRegistrationState(str, Enum):
    """Production discovery status for a restricted, forward-only migration."""

    REGISTERED = "registered"


class AuxiliaryCompletionState(str, Enum):
    """Evidence status; declarations never imply production acceptance."""

    DECLARED_NO_CONNECTION = "declared_no_connection"
    PENDING_REAL_MYSQL = "pending_real_mysql"
    PENDING_SNAPSHOT_SCHEMA = "pending_snapshot_schema"
    PENDING_PROVIDER_CONTRACT = "pending_provider_contract"
    PENDING_PUBLISH_ACCEPTANCE = "pending_publish_acceptance"
    BLOCKED_REAL_MYSQL = "blocked_real_mysql"
    BLOCKED_SNAPSHOT_ACCEPTANCE = "blocked_snapshot_acceptance"


@dataclass(frozen=True, slots=True)
class AuxiliaryUniqueMigrationPlan:
    """One restricted, forward-only state-UNIQUE migration in normal discovery.

    ``declaration`` remains a restricted-context factory.  The normal migration
    version registry materializes it in stable version order; materialization itself
    opens no connection and executes no DDL.
    """

    declaration: Any
    duplicate_preflight: MySQLVerificationRequirement
    registration_state: AuxiliaryMigrationRegistrationState = (
        AuxiliaryMigrationRegistrationState.REGISTERED
    )

    def __post_init__(self) -> None:
        if not isinstance(self.declaration, AuxiliaryUniqueConstraintDeclaration):
            raise AuxiliaryInventoryError(
                "auxiliary UNIQUE migration plan 必须使用 AuxiliaryUniqueConstraintDeclaration"
            )
        if self.duplicate_preflight is not MySQLVerificationRequirement.STORED_DATA_DUPLICATE_PREFLIGHT:
            raise AuxiliaryInventoryError("auxiliary UNIQUE migration plan 必须要求存量重复键预检")
        if self.registration_state is not AuxiliaryMigrationRegistrationState.REGISTERED:
            raise AuxiliaryInventoryError("S4 auxiliary UNIQUE migration 必须进入生产 discovery")

    @property
    def dataset_key(self) -> str:
        return str(self.declaration.dataset_key)

    @property
    def version(self) -> str:
        return str(self.declaration.version)


@dataclass(frozen=True, slots=True)
class AuxiliaryStateDatasetCompletion:
    """Readiness record for one of the thirteen mutable-state datasets."""

    dataset_key: str
    migration_version: str
    identity_semantics: AuxiliaryPersistenceIdentity = AuxiliaryPersistenceIdentity.STATE_ATOMIC_UPSERT
    duplicate_preflight: AuxiliaryCompletionState = AuxiliaryCompletionState.PENDING_REAL_MYSQL
    unique_migration: AuxiliaryCompletionState = AuxiliaryCompletionState.PENDING_REAL_MYSQL
    two_connection_acceptance: AuxiliaryCompletionState = AuxiliaryCompletionState.PENDING_REAL_MYSQL
    descriptor_wiring: AuxiliaryCompletionState = AuxiliaryCompletionState.DECLARED_NO_CONNECTION
    contract_evidence: AuxiliaryCompletionState = AuxiliaryCompletionState.DECLARED_NO_CONNECTION
    overall: AuxiliaryCompletionState = AuxiliaryCompletionState.BLOCKED_REAL_MYSQL

    def __post_init__(self) -> None:
        if not self.dataset_key.strip() or not self.migration_version.strip():
            raise AuxiliaryInventoryError("state completion dataset_key 与 migration_version 均不能为空")
        if self.dataset_key not in STATE_ATOMIC_UPSERT_DATASET_KEYS:
            raise AuxiliaryInventoryError("只有批准的 state dataset 可以等待 natural-key UNIQUE")
        pending_mysql = (self.duplicate_preflight, self.unique_migration, self.two_connection_acceptance)
        if any(state is not AuxiliaryCompletionState.PENDING_REAL_MYSQL for state in pending_mysql):
            raise AuxiliaryInventoryError("state dataset 不得伪造 MySQL UNIQUE 或并发验收证据")
        if self.descriptor_wiring is not AuxiliaryCompletionState.DECLARED_NO_CONNECTION:
            raise AuxiliaryInventoryError("descriptor 接线只能记录无连接声明证据")
        if self.contract_evidence is not AuxiliaryCompletionState.DECLARED_NO_CONNECTION:
            raise AuxiliaryInventoryError("contract evidence 只能记录无连接测试契约")
        if self.overall is not AuxiliaryCompletionState.BLOCKED_REAL_MYSQL:
            raise AuxiliaryInventoryError("state dataset 在真实 MySQL 验收前必须保持 fail-closed")

    @property
    def is_production_ready(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class AuxiliarySnapshotDatasetCompletion:
    """Readiness record for event datasets; no impossible UNIQUE work is represented."""

    dataset_key: str
    identity_semantics: AuxiliaryPersistenceIdentity = AuxiliaryPersistenceIdentity.IMMUTABLE_PARTITION_SNAPSHOT
    snapshot_schema: AuxiliaryCompletionState = AuxiliaryCompletionState.PENDING_SNAPSHOT_SCHEMA
    provider_contract: AuxiliaryCompletionState = AuxiliaryCompletionState.PENDING_PROVIDER_CONTRACT
    publish_acceptance: AuxiliaryCompletionState = AuxiliaryCompletionState.PENDING_PUBLISH_ACCEPTANCE
    contract_evidence: AuxiliaryCompletionState = AuxiliaryCompletionState.DECLARED_NO_CONNECTION
    overall: AuxiliaryCompletionState = AuxiliaryCompletionState.BLOCKED_SNAPSHOT_ACCEPTANCE

    def __post_init__(self) -> None:
        if self.dataset_key not in IMMUTABLE_PARTITION_SNAPSHOT_DATASET_KEYS:
            raise AuxiliaryInventoryError("只有批准的 event dataset 可以使用 snapshot completion")
        expected = (
            (self.snapshot_schema, AuxiliaryCompletionState.PENDING_SNAPSHOT_SCHEMA),
            (self.provider_contract, AuxiliaryCompletionState.PENDING_PROVIDER_CONTRACT),
            (self.publish_acceptance, AuxiliaryCompletionState.PENDING_PUBLISH_ACCEPTANCE),
            (self.contract_evidence, AuxiliaryCompletionState.DECLARED_NO_CONNECTION),
            (self.overall, AuxiliaryCompletionState.BLOCKED_SNAPSHOT_ACCEPTANCE),
        )
        if any(actual is not required for actual, required in expected):
            raise AuxiliaryInventoryError("event dataset 必须等待 snapshot schema/provider/publish 验收")

    @property
    def is_production_ready(self) -> bool:
        return False


AuxiliaryDatasetCompletion = AuxiliaryStateDatasetCompletion | AuxiliarySnapshotDatasetCompletion


# The names are retained as schema capability identifiers for audit output. Alembic
# owns deployment; this inventory never opens a connection or executes DDL.
_AUXILIARY_UNIQUE_MIGRATION_SPECS: tuple[tuple[str, str, str], ...] = (
    ("limit_up_board", "V006_auxiliary_limit_up_board_unique", "uq_limit_up_board_code_date"),
    ("concept_sector", "V007_auxiliary_concept_sector_unique", "uq_concept_sector_code_date"),
    ("concept_sector_stock", "V008_auxiliary_concept_sector_stock_unique", "uq_concept_sector_stock_sc_c_d"),
    ("capital_flow", "V009_auxiliary_capital_flow_unique", "uq_capital_flow_code_date"),
    ("dragon_tiger", "V010_auxiliary_dragon_tiger_unique", "uq_dragon_tiger_code_date"),
    ("margin_detail", "V012_auxiliary_margin_detail_unique", "uq_margin_detail_code_date"),
    ("hsgt_flow", "V014_auxiliary_hsgt_flow_unique", "uq_hsgt_flows_date"),
    ("hsgt_top10", "V015_auxiliary_hsgt_top10_unique", "uq_hsgt_top10_code_date"),
    ("pledge_stat", "V016_auxiliary_pledge_stat_unique", "uq_pledge_stats_code_date"),
    ("broker_recommend", "V017_auxiliary_broker_recommend_unique", "uq_broker_rec_code_month_broker"),
    ("fina_indicator", "V019_auxiliary_fina_indicator_unique", "uq_fina_code_end_date"),
    ("auction", "V020_auxiliary_auction_unique", "uq_auction_data_code_date"),
    ("holder_number", "V021_auxiliary_holder_number_unique", "uq_holder_numbers_code_date"),
)


def _build_unique_migration_plan(
    entry: AuxiliaryDatasetInventoryEntry,
    *,
    version: str,
    index: str,
) -> AuxiliaryUniqueMigrationPlan:
    # The declaration is materialized by normal migration discovery only when the
    # runner requests its version list; inventory import itself never executes DDL.
    return AuxiliaryUniqueMigrationPlan(
        declaration=AuxiliaryUniqueConstraintDeclaration(
            version=version,
            dataset_key=entry.key,
            table=entry.table_name,
            index=index,
            natural_key=entry.natural_key,
        ),
        duplicate_preflight=MySQLVerificationRequirement.STORED_DATA_DUPLICATE_PREFLIGHT,
    )


AUXILIARY_UNIQUE_MIGRATION_PLANS: Mapping[str, AuxiliaryUniqueMigrationPlan] = MappingProxyType(
    {
        dataset_key: _build_unique_migration_plan(
            AUXILIARY_DATASET_BY_KEY[dataset_key],
            version=version,
            index=index,
        )
        for dataset_key, version, index in _AUXILIARY_UNIQUE_MIGRATION_SPECS
    }
)
AUXILIARY_DATASET_COMPLETION: Mapping[str, AuxiliaryDatasetCompletion] = MappingProxyType(
    {
        **{
            dataset_key: AuxiliaryStateDatasetCompletion(
                dataset_key=dataset_key,
                migration_version=plan.version,
            )
            for dataset_key, plan in AUXILIARY_UNIQUE_MIGRATION_PLANS.items()
        },
        **{
            dataset_key: AuxiliarySnapshotDatasetCompletion(dataset_key=dataset_key)
            for dataset_key in IMMUTABLE_PARTITION_SNAPSHOT_DATASET_KEYS
        },
    }
)


def validate_auxiliary_migration_inventory(
    inventory: Sequence[AuxiliaryDatasetInventoryEntry] = AUXILIARY_DATASET_INVENTORY,
    plans: Mapping[str, AuxiliaryUniqueMigrationPlan] = AUXILIARY_UNIQUE_MIGRATION_PLANS,
    completion: Mapping[str, AuxiliaryDatasetCompletion] = AUXILIARY_DATASET_COMPLETION,
) -> None:
    """Validate state UNIQUE plans and snapshot exclusions without touching a database."""
    entries = tuple(inventory)
    entry_by_key = {entry.key: entry for entry in entries}
    expected_completion_keys = set(entry_by_key)
    expected_plan_keys = {entry.key for entry in entries if entry.is_state_dataset}
    event_keys = {entry.key for entry in entries if entry.is_snapshot_dataset}
    if event_keys & set(plans):
        raise AuxiliaryInventoryError(
            "event snapshot datasets must never appear in per-row UNIQUE plans: "
            + ", ".join(sorted(event_keys & set(plans)))
        )
    if set(plans) != expected_plan_keys:
        missing = sorted(expected_plan_keys - set(plans))
        unexpected = sorted(set(plans) - expected_plan_keys)
        raise AuxiliaryInventoryError(
            "auxiliary UNIQUE migration plans must cover only state datasets: "
            f"missing={missing!r}; unexpected={unexpected!r}"
        )
    if set(completion) != expected_completion_keys:
        missing = sorted(expected_completion_keys - set(completion))
        unexpected = sorted(set(completion) - expected_completion_keys)
        raise AuxiliaryInventoryError(
            "auxiliary completion records must cover the full inventory: "
            f"missing={missing!r}; unexpected={unexpected!r}"
        )
    versions = tuple(plan.version for plan in plans.values())
    if len(versions) != len(set(versions)):
        raise AuxiliaryInventoryError("每个 state dataset 必须有独立 UNIQUE migration version")
    declarations = tuple(plan.declaration for plan in plans.values())
    for dataset_key in expected_plan_keys:
        entry = entry_by_key[dataset_key]
        plan = plans[dataset_key]
        declaration = plan.declaration
        if (
            plan.dataset_key != entry.key
            or declaration.table != entry.table_name
            or declaration.natural_key != entry.natural_key
        ):
            raise AuxiliaryInventoryError(
                f"auxiliary UNIQUE migration plan 与 state inventory 不一致：{entry.key}"
            )
        if plan.registration_state is not AuxiliaryMigrationRegistrationState.REGISTERED:
            raise AuxiliaryInventoryError("auxiliary UNIQUE migration 未进入生产 discovery")
        record = completion[entry.key]
        if not isinstance(record, AuxiliaryStateDatasetCompletion):
            raise AuxiliaryInventoryError(f"state dataset completion 类型错误：{entry.key}")
        if record.dataset_key != entry.key or record.migration_version != plan.version:
            raise AuxiliaryInventoryError(f"state completion 与 migration plan 不一致：{entry.key}")
        if record.is_production_ready:
            raise AuxiliaryInventoryError(f"state dataset 不得在真实 MySQL 验收前标记 ready：{entry.key}")
    for dataset_key in event_keys:
        record = completion[dataset_key]
        if not isinstance(record, AuxiliarySnapshotDatasetCompletion):
            raise AuxiliaryInventoryError(f"event dataset completion 类型错误：{dataset_key}")
        if record.is_production_ready:
            raise AuxiliaryInventoryError(
                f"event dataset 不得在 snapshot schema/provider/publish 验收前标记 ready：{dataset_key}"
            )
    if len({declaration.index for declaration in declarations}) != len(declarations):
        raise AuxiliaryInventoryError("auxiliary UNIQUE migration index 名称必须逐数据集唯一")


validate_auxiliary_migration_inventory()


def validate_auxiliary_inventory(
    inventory: Sequence[AuxiliaryDatasetInventoryEntry] = AUXILIARY_DATASET_INVENTORY,
    *,
    require_required_categories: bool = True,
) -> None:
    """Audit declarations against ORM metadata without opening a database connection."""
    entries = tuple(inventory)
    keys = tuple(entry.key for entry in entries)
    views = tuple(entry.view_key for entry in entries)
    if len(keys) != len(set(keys)):
        raise AuxiliaryInventoryError("inventory dataset key 重复")
    if len(views) != len(set(views)):
        raise AuxiliaryInventoryError("inventory view_key 重复")
    categories = {entry.category for entry in entries}
    if require_required_categories:
        missing_categories = sorted(REQUIRED_AUXILIARY_CATEGORIES - categories)
        if missing_categories:
            raise AuxiliaryInventoryError("inventory 缺少任务要求类别：" + ", ".join(missing_categories))
    identity_keys = {entry.key for entry in entries}
    if require_required_categories:
        if identity_keys != STATE_ATOMIC_UPSERT_DATASET_KEYS | IMMUTABLE_PARTITION_SNAPSHOT_DATASET_KEYS:
            raise AuxiliaryInventoryError("production inventory 必须精确覆盖批准的 13 state + 4 publishable snapshot datasets")
        classified_state = {entry.key for entry in entries if entry.is_state_dataset}
        classified_snapshot = {entry.key for entry in entries if entry.is_snapshot_dataset}
        if classified_state != STATE_ATOMIC_UPSERT_DATASET_KEYS:
            raise AuxiliaryInventoryError("state atomic-upsert identity classification 与批准清单不一致")
        if classified_snapshot != IMMUTABLE_PARTITION_SNAPSHOT_DATASET_KEYS:
            raise AuxiliaryInventoryError("immutable snapshot identity classification 与批准清单不一致")
    known_keys = set(keys)
    for entry in entries:
        missing_dependencies = set(entry.dependencies) - known_keys
        if missing_dependencies:
            raise AuxiliaryInventoryError(
                f"inventory '{entry.key}' 依赖未声明数据集：{', '.join(sorted(missing_dependencies))}"
            )
        columns = set(entry.model.__table__.c.keys())
        if entry.freshness_field not in columns:
            raise AuxiliaryInventoryError(
                f"inventory '{entry.key}' freshness field 不存在于当前 projection：{entry.freshness_field}"
            )
        if entry.is_snapshot_dataset:
            # The current ORM model is a lossy read projection.  Snapshot identity and
            # hashing intentionally do not inspect its projected columns.
            continue
        policy = entry.update_policy
        if policy is None:  # guarded by __post_init__, retained for type narrowing
            raise AuxiliaryInventoryError(f"state inventory '{entry.key}' 缺少 update policy")
        referenced = {
            *entry.natural_key,
            entry.freshness_field,
            *policy.overwrite_fields,
            *policy.preserve_fields,
            *policy.replace_scope_fields,
        }
        if policy.latest_wins_field is not None:
            referenced.add(policy.latest_wins_field)
        missing_columns = sorted(referenced - columns)
        if missing_columns:
            raise AuxiliaryInventoryError(
                f"inventory '{entry.key}' 声明了 model '{entry.table_name}' 不存在的字段："
                + ", ".join(missing_columns)
            )
        illegal_overwrites = set(entry.natural_key) & set(policy.overwrite_fields)
        if illegal_overwrites:
            raise AuxiliaryInventoryError(
                f"inventory '{entry.key}' 不得 overwrite natural key：{', '.join(sorted(illegal_overwrites))}"
            )


def validate_audit_only_auxiliary_inventory(
    inventory: Sequence[AuxiliaryDatasetInventoryEntry] = AUXILIARY_AUDIT_ONLY_DATASET_INVENTORY,
) -> None:
    """Validate the explicit non-publishable inventory without treating it as production."""
    entries = tuple(inventory)
    validate_auxiliary_inventory(entries, require_required_categories=False)
    keys = {entry.key for entry in entries}
    if keys != AUDIT_ONLY_AUXILIARY_DATASET_KEYS:
        raise AuxiliaryInventoryError("audit-only inventory 必须精确覆盖批准的非生产数据集")
    if keys & set(AUXILIARY_DATASET_BY_KEY):
        raise AuxiliaryInventoryError("audit-only dataset 不得同时属于 production inventory")
    if any(not entry.is_snapshot_dataset for entry in entries):
        raise AuxiliaryInventoryError("audit-only dataset 必须使用 immutable partition snapshot identity")


def _mapping_freshness(field: str):
    def extract(record: Mapping[str, Any]) -> date | datetime | None:
        value = record.get(field)
        if isinstance(value, datetime):
            return value
        if isinstance(value, date):
            return value
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            return None
    return extract


def _mapping_instrument(field: str | None):
    if field is None:
        return None

    def extract(record: Mapping[str, Any]) -> str | None:
        value = str(record.get(field) or "").strip()
        return value or None

    return extract


def assemble_standard_auxiliary_descriptors(
    adapters: Mapping[str, StandardAuxiliaryDatasetAdapters],
    *,
    schema_readiness: AuxiliarySchemaReadiness,
    inventory: Sequence[AuxiliaryDatasetInventoryEntry] = AUXILIARY_DATASET_INVENTORY,
) -> tuple[AuxiliaryDatasetDescriptor[Mapping[str, Any], Mapping[str, Any], Any], ...]:
    """Assemble only verified standard descriptors without dataset-specific branches."""
    entries = tuple(inventory)
    validate_auxiliary_inventory(entries, require_required_categories=entries == AUXILIARY_DATASET_INVENTORY)
    snapshot_keys = sorted(entry.key for entry in entries if entry.is_snapshot_dataset)
    if snapshot_keys:
        raise AuxiliarySchemaMigrationBlockedError(
            "immutable partition snapshot datasets are blocked until snapshot schema/provider/publish acceptance: "
            + ", ".join(snapshot_keys)
        )
    expected_keys = {entry.key for entry in entries}
    supplied_keys = set(adapters)
    missing = sorted(expected_keys - supplied_keys)
    unexpected = sorted(supplied_keys - expected_keys)
    if missing or unexpected:
        parts: list[str] = []
        if missing:
            parts.append("缺少 adapters：" + ", ".join(missing))
        if unexpected:
            parts.append("出现未登记 adapters：" + ", ".join(unexpected))
        raise AuxiliaryInventoryError("；".join(parts))

    descriptors: list[AuxiliaryDatasetDescriptor[Mapping[str, Any], Mapping[str, Any], Any]] = []
    for entry in entries:
        schema_readiness.assert_ready(entry)
        adapter = adapters[entry.key]
        descriptors.append(
            AuxiliaryDatasetDescriptor(
                key=entry.key,
                view_key=entry.view_key,
                natural_key=entry.natural_key,
                coverage_policy=CoveragePolicy(
                    minimum_instrument_coverage=entry.minimum_instrument_coverage,
                    instrument_extractor=_mapping_instrument(entry.coverage_instrument_field),
                ),
                refresh_policy=RefreshPolicy(RefreshMode.IF_STALE),
                fetcher=adapter.fetcher,
                normalizer=adapter.normalizer,
                repository=adapter.repository,
                loader=adapter.loader,
                freshness_field=entry.freshness_field,
                freshness_extractor=_mapping_freshness(entry.freshness_field),
                dependencies=entry.dependencies,
            )
        )
    return tuple(descriptors)


def _build_event_capable_production_composition(
    adapters: Mapping[str, StandardAuxiliaryDatasetAdapters],
    *,
    schema_readiness: AuxiliarySchemaReadiness,
    inventory: Sequence[AuxiliaryDatasetInventoryEntry],
    connection: Any | None,
    raw_provider: Any | None,
    clock: Clock | None = None,
) -> Any | None:
    """Build the immutable-event path only when its injected dependencies exist.

    State-only inventories deliberately retain the small, generic descriptor path.
    The frozen default inventory also contains immutable event snapshots, which must
    be composed with one caller-owned connection and an injected raw provider rather
    than being sent to the state-only assembler.
    """
    entries = tuple(inventory)
    snapshot_keys = tuple(entry.key for entry in entries if entry.is_snapshot_dataset)
    if not snapshot_keys:
        return None
    if connection is None:
        raise AuxiliaryInventoryError(
            "production event snapshots require an injected caller-owned connection: "
            + ", ".join(snapshot_keys)
        )
    if raw_provider is None:
        raise AuxiliaryInventoryError(
            "production event snapshots require an injected raw provider: "
            + ", ".join(snapshot_keys)
        )

    # Deferred import avoids the production-inventory/composition import cycle.
    from app.services.auxiliary.composition import build_auxiliary_production_composition

    return build_auxiliary_production_composition(
        connection=connection,
        raw_provider=raw_provider,
        state_adapters=adapters,
        schema_readiness=schema_readiness,
        clock=clock,
        inventory=entries,
    )


def build_production_auxiliary_registry(
    adapters: Mapping[str, StandardAuxiliaryDatasetAdapters],
    *,
    schema_readiness: AuxiliarySchemaReadiness,
    inventory: Sequence[AuxiliaryDatasetInventoryEntry] = AUXILIARY_DATASET_INVENTORY,
    connection: Any | None = None,
    raw_provider: Any | None = None,
) -> AuxiliaryDatasetRegistry:
    """Build the frozen production registry for either state-only or full S4 inventory.

    Supplying the default 13-state-plus-4-event inventory requires the transaction
    connection and raw provider needed by immutable snapshots.  State-only callers
    remain supported without either dependency.
    """
    entries = tuple(inventory)
    composition = _build_event_capable_production_composition(
        adapters,
        schema_readiness=schema_readiness,
        inventory=entries,
        connection=connection,
        raw_provider=raw_provider,
    )
    if composition is not None:
        return composition.registry
    return build_auxiliary_registry(
        assemble_standard_auxiliary_descriptors(
            adapters,
            schema_readiness=schema_readiness,
            inventory=entries,
        )
    )


def build_production_auxiliary_orchestrator(
    adapters: Mapping[str, StandardAuxiliaryDatasetAdapters],
    *,
    schema_readiness: AuxiliarySchemaReadiness,
    inventory: Sequence[AuxiliaryDatasetInventoryEntry] = AUXILIARY_DATASET_INVENTORY,
    clock: Clock | None = None,
    connection: Any | None = None,
    raw_provider: Any | None = None,
) -> AuxiliaryDataOrchestrator:
    """Build the full 17-item S4 orchestrator or an explicit state-only subset."""
    entries = tuple(inventory)
    composition = _build_event_capable_production_composition(
        adapters,
        schema_readiness=schema_readiness,
        inventory=entries,
        connection=connection,
        raw_provider=raw_provider,
        clock=clock,
    )
    if composition is not None:
        return composition.orchestrator
    return AuxiliaryDataOrchestrator(
        build_production_auxiliary_registry(
            adapters,
            schema_readiness=schema_readiness,
            inventory=entries,
        ),
        clock=clock,
    )


validate_auxiliary_inventory()
validate_audit_only_auxiliary_inventory()


__all__ = [
    "AUDIT_ONLY_AUXILIARY_DATASET_KEYS",
    "AUDIT_ONLY_IMMUTABLE_PARTITION_SNAPSHOT_DATASET_KEYS",
    "AUXILIARY_AUDIT_ONLY_DATASET_BY_KEY",
    "AUXILIARY_AUDIT_ONLY_DATASET_INVENTORY",
    "AUXILIARY_DATASET_BY_KEY",
    "AUXILIARY_DATASET_COMPLETION",
    "AUXILIARY_DATASET_INVENTORY",
    "AUXILIARY_UNIQUE_MIGRATION_PLANS",
    "INSTITUTIONAL_SURVEY_AUDIT_ONLY_DATASET",
    "IMMUTABLE_PARTITION_SNAPSHOT_DATASET_KEYS",
    "STATE_ATOMIC_UPSERT_DATASET_KEYS",
    "AuxiliaryCompletionState",
    "AuxiliaryDatasetCompletion",
    "AuxiliaryDatasetInventoryEntry",
    "AuxiliaryInventoryError",
    "AuxiliaryMigrationRegistrationState",
    "AuxiliaryPersistenceIdentity",
    "AuxiliarySchemaMigrationBlockedError",
    "AuxiliarySchemaReadiness",
    "AuxiliarySnapshotDatasetCompletion",
    "AuxiliaryStateDatasetCompletion",
    "AuxiliaryUniqueMigrationPlan",
    "AuxiliaryUpdatePolicyDeclaration",
    "AuxiliaryWriteStrategy",
    "MySQLVerificationRequirement",
    "REQUIRED_AUXILIARY_CATEGORIES",
    "SnapshotVerificationRequirement",
    "StandardAuxiliaryDatasetAdapters",
    "UnverifiedMySQLSchemaReadiness",
    "assemble_standard_auxiliary_descriptors",
    "build_production_auxiliary_orchestrator",
    "build_production_auxiliary_registry",
    "validate_auxiliary_inventory",
    "validate_audit_only_auxiliary_inventory",
    "validate_auxiliary_migration_inventory",
]
