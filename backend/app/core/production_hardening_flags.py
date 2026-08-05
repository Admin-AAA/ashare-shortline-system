from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, Self


class RolloutMode(StrEnum):
    """A reversible rollout state shared by production-hardening features."""

    LEGACY = "legacy"
    SHADOW = "shadow"
    AUDIT = "audit"
    ENFORCED = "enforced"


class ProductionHardeningFeature(StrEnum):
    EVIDENCE_PROJECTION = "evidence_projection"
    CANONICAL_EXECUTION = "canonical_execution"
    RELEASE_GATE = "release_gate"
    PORTFOLIO_RISK = "portfolio_risk"
    SCHEDULER_CLAIM = "scheduler_claim"
    DASHBOARD_REFRESH = "dashboard_refresh"


FEATURE_ORDER: tuple[ProductionHardeningFeature, ...] = tuple(
    ProductionHardeningFeature
)


class HardeningFlagSettings(Protocol):
    hardening_evidence_projection_mode: RolloutMode
    hardening_canonical_execution_mode: RolloutMode
    hardening_release_gate_mode: RolloutMode
    hardening_portfolio_risk_mode: RolloutMode
    hardening_scheduler_claim_mode: RolloutMode
    hardening_dashboard_refresh_mode: RolloutMode


@dataclass(frozen=True, slots=True)
class ProductionHardeningFlags:
    """Immutable snapshot of independently deployable hardening paths."""

    evidence_projection: RolloutMode = RolloutMode.ENFORCED
    canonical_execution: RolloutMode = RolloutMode.ENFORCED
    release_gate: RolloutMode = RolloutMode.ENFORCED
    portfolio_risk: RolloutMode = RolloutMode.ENFORCED
    scheduler_claim: RolloutMode = RolloutMode.ENFORCED
    dashboard_refresh: RolloutMode = RolloutMode.ENFORCED

    @classmethod
    def from_settings(cls, settings: HardeningFlagSettings) -> Self:
        return cls(
            evidence_projection=settings.hardening_evidence_projection_mode,
            canonical_execution=settings.hardening_canonical_execution_mode,
            release_gate=settings.hardening_release_gate_mode,
            portfolio_risk=settings.hardening_portfolio_risk_mode,
            scheduler_claim=settings.hardening_scheduler_claim_mode,
            dashboard_refresh=settings.hardening_dashboard_refresh_mode,
        )

    def mode_for(self, feature: ProductionHardeningFeature) -> RolloutMode:
        return getattr(self, feature.value)

    def as_dict(self) -> dict[str, str]:
        return {
            feature.value: self.mode_for(feature).value
            for feature in FEATURE_ORDER
        }

    def newly_enforced_from(
        self,
        previous: ProductionHardeningFlags,
    ) -> tuple[ProductionHardeningFeature, ...]:
        return tuple(
            feature
            for feature in FEATURE_ORDER
            if previous.mode_for(feature) is not RolloutMode.ENFORCED
            and self.mode_for(feature) is RolloutMode.ENFORCED
        )


DEFAULT_PRODUCTION_HARDENING_FLAGS = ProductionHardeningFlags()


def validate_staged_transition(
    previous: ProductionHardeningFlags,
    current: ProductionHardeningFlags,
) -> tuple[ProductionHardeningFeature, ...]:
    """Reject a release that makes more than one new path authoritative."""

    newly_enforced = current.newly_enforced_from(previous)
    if len(newly_enforced) > 1:
        names = ", ".join(feature.value for feature in newly_enforced)
        raise ValueError(
            "production hardening cutover must enforce at most one feature per release; "
            f"newly_enforced={names}"
        )
    return newly_enforced
