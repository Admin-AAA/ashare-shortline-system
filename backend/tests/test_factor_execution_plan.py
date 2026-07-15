"""S2 Factor ExecutionPlan：bootstrap 验证、确定性指纹与冻结边界。"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from app.services.factors import (
    FactorContext,
    FactorCycleError,
    FactorDependencyError,
    FactorOutputOwnershipError,
    FactorRegistry,
    FactorRegistryFrozenError,
    bootstrap_default_factor_registry,
    default_registry,
)


class _Factor:
    def __init__(self, factor_id: str, outputs: tuple[str, ...], depends_on: tuple[str, ...] = ()) -> None:
        self.id = factor_id
        self.output_fields = outputs
        self.depends_on = depends_on
        self.metadata: Mapping[str, Any] = {}

    def compute(self, stock: Mapping[str, Any], ctx: FactorContext) -> dict[str, Any]:
        return {field: self.id for field in self.output_fields}


def _planned_registry() -> FactorRegistry:
    registry = FactorRegistry()
    registry.register(_Factor("downstream", ("down",), ("upstream",)))
    registry.register(_Factor("independent", ("independent",)))
    registry.register(_Factor("upstream", ("up",)))
    return registry


def test_bootstrap_builds_deterministic_plan_and_freezes_registry():
    first = _planned_registry()
    second = _planned_registry()

    first_plan = first.bootstrap()
    second_plan = second.bootstrap()

    assert first.is_frozen is True
    assert first_plan.factor_ids == ("independent", "upstream", "downstream")
    assert first_plan.fingerprint == second_plan.fingerprint
    assert first.execution_plan is first_plan
    assert [factor.id for factor in first.resolve_order()] == list(first_plan.factor_ids)

    with pytest.raises(FactorRegistryFrozenError):
        first.register(_Factor("late", ("late",)))
    with pytest.raises(FactorRegistryFrozenError):
        first.unregister("upstream")
    with pytest.raises(FactorRegistryFrozenError):
        first.clear()


def test_bootstrap_rejects_duplicate_output_ownership_before_freeze():
    registry = FactorRegistry()
    registry.register(_Factor("first", ("shared",)))
    registry.register(_Factor("second", ("shared",)))

    with pytest.raises(FactorOutputOwnershipError) as error:
        registry.bootstrap()

    assert error.value.output_field == "shared"
    assert error.value.owners == ("first", "second")
    assert registry.is_frozen is False


@pytest.mark.parametrize(
    ("factors", "error_type"),
    [
        (("missing", ("x",), ("absent",)), FactorDependencyError),
        (("cycle-a", ("a",), ("cycle-b",)), FactorCycleError),
    ],
)
def test_bootstrap_validates_dependencies_before_publishing_plan(factors, error_type):
    registry = FactorRegistry()
    factor_id, outputs, dependencies = factors
    registry.register(_Factor(factor_id, outputs, dependencies))
    if error_type is FactorCycleError:
        registry.register(_Factor("cycle-b", ("b",), ("cycle-a",)))

    with pytest.raises(error_type):
        registry.bootstrap()
    assert registry.is_frozen is False


def test_explicit_default_bootstrap_is_idempotent_and_publishes_builtin_plan():
    first = bootstrap_default_factor_registry()
    second = bootstrap_default_factor_registry()

    assert first is second
    assert default_registry.is_frozen is True
    assert set(first.factor_ids) == {
        "market_context",
        "sector_ladder",
        "capital_resonance",
        "limit_up_overheat",
        "twenty_cm_risk",
        "leader",
        "dragon_tiger",
    }
