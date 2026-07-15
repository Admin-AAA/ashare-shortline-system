"""Auxiliary Dataset contracts, frozen registry and generic orchestration boundary."""
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
from app.services.auxiliary.registry import AuxiliaryDatasetRegistry, build_auxiliary_registry

__all__ = [
    "AuxiliaryDataOrchestrator",
    "AuxiliaryDatasetDescriptor",
    "AuxiliaryDatasetRegistry",
    "AuxiliaryDatasetRequest",
    "AuxiliaryDatasetResult",
    "AuxiliaryInstrument",
    "AuxiliaryOrchestratorResult",
    "CoveragePolicy",
    "RefreshMode",
    "RefreshPolicy",
    "build_auxiliary_registry",
]
