"""Auxiliary Dataset contracts, frozen registry and generic orchestration boundary."""
from app.services.auxiliary.application import (
    AuxiliaryApplicationResult,
    InstitutionalSurveyAuditCapture,
    InstitutionalSurveyAuditRequest,
    capture_institutional_survey_audit,
)
from app.services.auxiliary.composition import (
    AuxiliaryProductionComposition,
    EVENT_SNAPSHOT_CONTRACTS,
    INSTITUTIONAL_SURVEY_AUDIT_CONTRACT,
    build_auxiliary_production_composition,
)
from app.services.auxiliary.event_snapshot import (
    ActiveEventSnapshotReader,
    EventSnapshotAuxiliaryHandler,
    EventSnapshotPilot,
    EventSnapshotRefreshService,
    EventSnapshotRequest,
    TushareSinglePageEventContract,
    TushareSinglePageEventProviderClient,
)
from app.services.auxiliary.block_trade_snapshot import (
    ActiveBlockTradeProjection,
    BlockTradeSnapshotPilot,
)
from app.services.auxiliary.contracts import (
    AuxiliaryDatasetDescriptor,
    AuxiliaryDatasetRequest,
    AuxiliaryDatasetResult,
    AuxiliaryInstrument,
    AuxiliaryOrchestratorResult,
    CoveragePolicy,
    RefreshMode,
    RefreshPolicy,
)
from app.services.auxiliary.orchestrator import AuxiliaryDataOrchestrator
from app.services.auxiliary.production import (
    AUXILIARY_DATASET_BY_KEY,
    AUXILIARY_DATASET_INVENTORY,
    AuxiliarySchemaMigrationBlockedError,
    StandardAuxiliaryDatasetAdapters,
    build_production_auxiliary_orchestrator,
    build_production_auxiliary_registry,
)
from app.services.auxiliary.provider_results import (
    ProviderPartitionResult,
    ResponseCompletenessEvidence,
    SnapshotPublishBlockedError,
)
from app.services.auxiliary.registry import AuxiliaryDatasetRegistry, build_auxiliary_registry
from app.services.auxiliary.snapshot_repository import (
    ImmutablePartitionSnapshotRepositoryPort,
    SQLAlchemyImmutablePartitionSnapshotRepository,
)

__all__ = [
    "AUXILIARY_DATASET_BY_KEY",
    "AUXILIARY_DATASET_INVENTORY",
    "AuxiliaryApplicationResult",
    "AuxiliaryProductionComposition",
    "EVENT_SNAPSHOT_CONTRACTS",
    "INSTITUTIONAL_SURVEY_AUDIT_CONTRACT",
    "InstitutionalSurveyAuditCapture",
    "InstitutionalSurveyAuditRequest",
    "ActiveBlockTradeProjection",
    "ActiveEventSnapshotReader",
    "AuxiliaryDataOrchestrator",
    "AuxiliaryDatasetDescriptor",
    "AuxiliaryDatasetRegistry",
    "AuxiliaryDatasetRequest",
    "AuxiliaryDatasetResult",
    "AuxiliaryInstrument",
    "AuxiliaryOrchestratorResult",
    "AuxiliarySchemaMigrationBlockedError",
    "BlockTradeSnapshotPilot",
    "CoveragePolicy",
    "ImmutablePartitionSnapshotRepositoryPort",
    "ProviderPartitionResult",
    "RefreshMode",
    "RefreshPolicy",
    "ResponseCompletenessEvidence",
    "SQLAlchemyImmutablePartitionSnapshotRepository",
    "SnapshotPublishBlockedError",
    "EventSnapshotAuxiliaryHandler",
    "EventSnapshotPilot",
    "EventSnapshotRefreshService",
    "EventSnapshotRequest",
    "TushareSinglePageEventContract",
    "TushareSinglePageEventProviderClient",
    "capture_institutional_survey_audit",
    "build_auxiliary_production_composition",
    "build_auxiliary_registry",
    "build_production_auxiliary_orchestrator",
    "build_production_auxiliary_registry",
]
