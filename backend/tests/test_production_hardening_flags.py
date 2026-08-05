from __future__ import annotations

from itertools import product

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.production_hardening_flags import (
    DEFAULT_PRODUCTION_HARDENING_FLAGS,
    FEATURE_ORDER,
    ProductionHardeningFeature,
    ProductionHardeningFlags,
    RolloutMode,
    validate_staged_transition,
)


_SETTINGS_FIELD_BY_FEATURE = {
    feature: f"hardening_{feature.value}_mode" for feature in FEATURE_ORDER
}
_DEFAULTS = {
    ProductionHardeningFeature.EVIDENCE_PROJECTION: RolloutMode.ENFORCED,
    ProductionHardeningFeature.CANONICAL_EXECUTION: RolloutMode.ENFORCED,
    ProductionHardeningFeature.RELEASE_GATE: RolloutMode.ENFORCED,
    ProductionHardeningFeature.PORTFOLIO_RISK: RolloutMode.ENFORCED,
    ProductionHardeningFeature.SCHEDULER_CLAIM: RolloutMode.ENFORCED,
    ProductionHardeningFeature.DASHBOARD_REFRESH: RolloutMode.ENFORCED,
}
_SAFE_DEFAULTS = ProductionHardeningFlags(
    evidence_projection=RolloutMode.SHADOW,
    canonical_execution=RolloutMode.SHADOW,
    release_gate=RolloutMode.AUDIT,
    portfolio_risk=RolloutMode.SHADOW,
    scheduler_claim=RolloutMode.SHADOW,
    dashboard_refresh=RolloutMode.SHADOW,
)


def _settings_values(**overrides: str | RolloutMode) -> dict[str, str | RolloutMode]:
    values: dict[str, str | RolloutMode] = {
        _SETTINGS_FIELD_BY_FEATURE[feature]: mode
        for feature, mode in _DEFAULTS.items()
    }
    values.update(overrides)
    return values


def _flags_from_modes(modes: tuple[RolloutMode, ...]) -> ProductionHardeningFlags:
    return ProductionHardeningFlags(
        **{
            feature.value: mode
            for feature, mode in zip(FEATURE_ORDER, modes, strict=True)
        }
    )


def test_settings_defaults_are_shadow_or_audit_and_match_contract() -> None:
    settings = Settings(_env_file=None, **_settings_values())
    flags = ProductionHardeningFlags.from_settings(settings)

    assert flags == DEFAULT_PRODUCTION_HARDENING_FLAGS
    assert flags.as_dict() == {
        "evidence_projection": "enforced",
        "canonical_execution": "enforced",
        "release_gate": "enforced",
        "portfolio_risk": "enforced",
        "scheduler_claim": "enforced",
        "dashboard_refresh": "enforced",
    }
    assert all(flags.mode_for(feature) is RolloutMode.ENFORCED for feature in FEATURE_ORDER)


@pytest.mark.parametrize("feature", FEATURE_ORDER)
def test_each_settings_flag_can_be_overridden_independently(
    feature: ProductionHardeningFeature,
) -> None:
    field_name = _SETTINGS_FIELD_BY_FEATURE[feature]
    override_mode = (
        RolloutMode.AUDIT
        if feature is ProductionHardeningFeature.RELEASE_GATE
        else RolloutMode.SHADOW
    )
    settings = Settings(
        _env_file=None,
        **_settings_values(**{field_name: override_mode}),
    )
    flags = ProductionHardeningFlags.from_settings(settings)

    assert flags.mode_for(feature) is override_mode
    baseline = ProductionHardeningFlags.from_settings(
        Settings(_env_file=None, **_settings_values())
    )
    assert {
        candidate
        for candidate in FEATURE_ORDER
        if flags.mode_for(candidate) is not baseline.mode_for(candidate)
    } == {feature}


def test_settings_reject_unknown_rollout_mode() -> None:
    with pytest.raises(ValidationError, match="hardening_evidence_projection_mode"):
        Settings(
            _env_file=None,
            **_settings_values(
                hardening_evidence_projection_mode="silently_replace_production"
            ),
        )


def test_staged_transition_allows_one_new_cutover_and_rejects_two() -> None:
    one_cutover = ProductionHardeningFlags(
        evidence_projection=RolloutMode.ENFORCED,
        canonical_execution=RolloutMode.SHADOW,
        release_gate=RolloutMode.AUDIT,
        portfolio_risk=RolloutMode.SHADOW,
        scheduler_claim=RolloutMode.SHADOW,
        dashboard_refresh=RolloutMode.SHADOW,
    )
    assert validate_staged_transition(
        _SAFE_DEFAULTS,
        one_cutover,
    ) == (ProductionHardeningFeature.EVIDENCE_PROJECTION,)

    two_cutovers = ProductionHardeningFlags(
        evidence_projection=RolloutMode.ENFORCED,
        canonical_execution=RolloutMode.ENFORCED,
        release_gate=RolloutMode.AUDIT,
        portfolio_risk=RolloutMode.SHADOW,
        scheduler_claim=RolloutMode.SHADOW,
        dashboard_refresh=RolloutMode.SHADOW,
    )
    with pytest.raises(ValueError, match="at most one feature per release"):
        validate_staged_transition(
            _SAFE_DEFAULTS,
            two_cutovers,
        )


def test_staged_transition_property_for_every_mode_combination() -> None:
    """**Validates: Requirements 9.4–9.7, 10.1**

    For every possible six-flag configuration, a transition is valid exactly
    when no more than one path becomes newly authoritative.
    """

    modes = tuple(RolloutMode)
    for combination in product(modes, repeat=len(FEATURE_ORDER)):
        current = _flags_from_modes(combination)
        expected = current.newly_enforced_from(_SAFE_DEFAULTS)
        if len(expected) > 1:
            with pytest.raises(ValueError):
                validate_staged_transition(
                    _SAFE_DEFAULTS,
                    current,
                )
        else:
            assert validate_staged_transition(
                _SAFE_DEFAULTS,
                current,
            ) == expected
