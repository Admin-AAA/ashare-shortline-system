"""因子框架的公开入口：局部 Registry、显式生产 bootstrap 与冻结 ExecutionPlan。"""
from __future__ import annotations

from app.engine.factor_plan import FactorExecutionPlan, FactorPlanEntry

from .allowlist import (
    bootstrap_factor_allowlist,
    collect_factor_output_fields,
    inject_factor_output_fields,
)
from .base import (
    BaseFactor,
    Factor,
    FactorContext,
    FactorContractError,
    FactorCycleError,
    FactorDependencyError,
    FactorError,
    FactorOutputOwnershipError,
    FactorRegistryFrozenError,
)
from .driver import run_factors
from .explanations import (
    EXPLANATION_METADATA_KEY,
    build_factor_explanations,
    iter_explainable_factors,
)
from .registry import (
    FactorRegistry,
    bootstrap_default_factor_registry,
    default_registry,
    register_factor,
)

__all__ = [
    "Factor",
    "BaseFactor",
    "FactorContext",
    "FactorRegistry",
    "FactorExecutionPlan",
    "FactorPlanEntry",
    "default_registry",
    "register_factor",
    "bootstrap_default_factor_registry",
    "run_factors",
    "build_factor_explanations",
    "iter_explainable_factors",
    "EXPLANATION_METADATA_KEY",
    "collect_factor_output_fields",
    "inject_factor_output_fields",
    "bootstrap_factor_allowlist",
    "FactorError",
    "FactorContractError",
    "FactorDependencyError",
    "FactorCycleError",
    "FactorOutputOwnershipError",
    "FactorRegistryFrozenError",
]
