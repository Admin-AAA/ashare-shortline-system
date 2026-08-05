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
    ProductionHardeningFeature.EVIDENCE_PROJECTION: RolloutMode.SHADOW,
    ProductionHardeningFeature.CANONICAL_EXECUTION: RolloutMode.SHADOW,
    ProductionHardeningFeature.RELEASE_GATE: RolloutMode.AUDIT,
    ProductionHardeningFeature.PORTFOLIO_RISK: RolloutMode.SHADOW,
    ProductionHardeningFeature.SCHEDULER_CLAIM: RolloutMode.SHADOW,
    ProductionHardeningFeature.DASHBOARD_REFRESH: RolloutMode.SHADOW,
}


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
        "evidence_projection": "shadow",
        "canonical_execution": "shadow",
        "release_gate": "audit",
        "portfolio_risk": "shadow",
        "scheduler_claim": "shadow",
        "dashboard_refresh": "shadow",
    }
    assert all(
        flags.mode_for(feature) in {RolloutMode.SHADOW, RolloutMode.AUDIT}
        for feature in FEATURE_ORDER
    )


@pytest.mark.parametrize("feature", FEATURE_ORDER)
def test_each_settings_flag_can_be_overridden_independently(
    feature: ProductionHardeningFeature,
) -> None:
    field_name = _SETTINGS_FIELD_BY_FEATURE[feature]
    settings = Settings(
        _env_file=None,
        **_settings_values(**{field_name: RolloutMode.ENFORCED}),
    )
    flags = ProductionHardeningFlags.from_settings(settings)

    assert flags.mode_for(feature) is RolloutMode.ENFORCED
    assert {
        candidate
        for candidate in FEATURE_ORDER
        if flags.mode_for(candidate)
        is not DEFAULT_PRODUCTION_HARDENING_FLAGS.mode_for(candidate)
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
        evidence_projection=RolloutMode.ENFORCED
    )
    assert validate_staged_transition(
        DEFAULT_PRODUCTION_HARDENING_FLAGS,
        one_cutover,
    ) == (ProductionHardeningFeature.EVIDENCE_PROJECTION,)

    two_cutovers = ProductionHardeningFlags(
        evidence_projection=RolloutMode.ENFORCED,
        canonical_execution=RolloutMode.ENFORCED,
    )
    with pytest.raises(ValueError, match="at most one feature per release"):
        validate_staged_transition(
            DEFAULT_PRODUCTION_HARDENING_FLAGS,
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
        expected = current.newly_enforced_from(
            DEFAULT_PRODUCTION_HARDENING_FLAGS
        )
        if len(expected) > 1:
            with pytest.raises(ValueError):
                validate_staged_transition(
                    DEFAULT_PRODUCTION_HARDENING_FLAGS,
                    current,
                )
        else:
            assert validate_staged_transition(
                DEFAULT_PRODUCTION_HARDENING_FLAGS,
                current,
            ) == expected
