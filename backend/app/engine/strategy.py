"""Strategy protocol, immutable descriptor, and descriptor-backed registry."""
from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import math
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Protocol, cast, runtime_checkable

from app.engine.backtest import (
    AuxiliaryDataRequirement,
    BacktestErrorCode,
    BacktestValidationError,
    DataRequirement,
)
from app.engine.signal import Signal
from app.engine.skill_loader import StrategySkill
from app.engine.strategy_runner import run_strategy


STRATEGY_DESCRIPTOR_CONTRACT_VERSION = "strategy-descriptor/v1"


class StrategyError(Exception):
    """Base error for the strategy framework."""


class StrategyNotFoundError(StrategyError, KeyError):
    """The requested strategy_id is not registered."""

    def __init__(self, strategy_id: str, available_values: tuple[str, ...] = ()) -> None:
        self.strategy_id = strategy_id
        self.available_values = tuple(sorted({str(value) for value in available_values}))
        hint = f"可用值：{', '.join(self.available_values)}" if self.available_values else "可用值：（无）"
        super().__init__(f"未注册的策略：'{strategy_id}'；{hint}。")


class StrategyUnavailableError(StrategyError):
    """A disabled or deprecated descriptor was requested for new execution."""

    def __init__(self, descriptor: "StrategyDescriptor") -> None:
        self.strategy_id = descriptor.strategy_id
        self.status = descriptor.status
        detail = descriptor.deprecation_reason or "策略状态不允许新执行"
        super().__init__(
            f"策略 '{descriptor.strategy_id}' 当前状态为 '{descriptor.status.value}'，无法解析：{detail}。"
        )


class DuplicateStrategyError(StrategyError):
    """A descriptor strategy_id was registered more than once."""

    def __init__(self, strategy_id: str) -> None:
        self.strategy_id = strategy_id
        super().__init__(f"策略 id 重复注册：'{strategy_id}'。")


class StrategyIdentityError(StrategyError):
    """Manifest, legacy contract, or implementation identity drifted."""


class StrategyRegistryFrozenError(StrategyError):
    """A frozen production registry was modified."""

    def __init__(self) -> None:
        super().__init__("StrategyRegistry 已冻结；不可动态注册、移除或清空策略。")


class StrategyAssemblyError(StrategyError):
    """An imperative strategy could not be assembled during bootstrap."""


class StrategyMode(str, Enum):
    DECLARATIVE = "declarative"
    IMPERATIVE = "imperative"


class StrategyStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"
    DEPRECATED = "deprecated"


class BacktestParameterType(str, Enum):
    """Supported, deterministic scalar types for a strategy research parameter."""

    NUMBER = "number"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    STRING = "string"


@dataclass(frozen=True)
class BacktestParameterSpec:
    """One manifest-owned, bounded parameter accepted by a research backtest."""

    name: str
    value_type: BacktestParameterType
    default: object | None = None
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[object, ...] = ()

    def __post_init__(self) -> None:
        name = str(self.name).strip()
        if not name:
            raise StrategyIdentityError("backtest parameter name must not be blank")
        try:
            value_type = (
                self.value_type
                if isinstance(self.value_type, BacktestParameterType)
                else BacktestParameterType(str(self.value_type))
            )
        except ValueError as exc:
            raise StrategyIdentityError(f"backtest parameter '{name}' has an invalid type") from exc
        if (self.minimum is not None or self.maximum is not None) and value_type not in {
            BacktestParameterType.NUMBER,
            BacktestParameterType.INTEGER,
        }:
            raise StrategyIdentityError(f"backtest parameter '{name}' range requires a numeric type")
        minimum = _parameter_bound(self.minimum, name, "minimum")
        maximum = _parameter_bound(self.maximum, name, "maximum")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise StrategyIdentityError(f"backtest parameter '{name}' minimum must not exceed maximum")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "value_type", value_type)
        object.__setattr__(self, "minimum", minimum)
        object.__setattr__(self, "maximum", maximum)
        normalized_choices = tuple(self._coerce(value) for value in self.choices)
        if len(set(_parameter_identity(value) for value in normalized_choices)) != len(normalized_choices):
            raise StrategyIdentityError(f"backtest parameter '{name}' choices must not repeat")
        object.__setattr__(self, "choices", normalized_choices)
        if self.default is not None:
            object.__setattr__(self, "default", self.normalize(self.default))

    def _coerce(self, value: object) -> object:
        if self.value_type is BacktestParameterType.NUMBER:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise StrategyIdentityError(f"backtest parameter '{self.name}' must be a finite number")
            return float(value)
        if self.value_type is BacktestParameterType.INTEGER:
            if isinstance(value, bool) or not isinstance(value, int):
                raise StrategyIdentityError(f"backtest parameter '{self.name}' must be an integer")
            return value
        if self.value_type is BacktestParameterType.BOOLEAN:
            if not isinstance(value, bool):
                raise StrategyIdentityError(f"backtest parameter '{self.name}' must be a boolean")
            return value
        if not isinstance(value, str) or not value.strip():
            raise StrategyIdentityError(f"backtest parameter '{self.name}' must be a non-blank string")
        return value.strip()

    def normalize(self, value: object) -> object:
        try:
            normalized = self._coerce(value)
        except StrategyIdentityError as exc:
            raise BacktestValidationError(
                BacktestErrorCode.INVALID_REQUEST,
                f"parameter_set.{self.name} has an invalid value",
            ) from exc
        if isinstance(normalized, (int, float)) and not isinstance(normalized, bool):
            numeric = float(normalized)
            if self.minimum is not None and numeric < self.minimum:
                raise BacktestValidationError(
                    BacktestErrorCode.INVALID_REQUEST,
                    f"parameter_set.{self.name} must be at least {self.minimum}",
                )
            if self.maximum is not None and numeric > self.maximum:
                raise BacktestValidationError(
                    BacktestErrorCode.INVALID_REQUEST,
                    f"parameter_set.{self.name} must be at most {self.maximum}",
                )
        if self.choices and _parameter_identity(normalized) not in {
            _parameter_identity(choice) for choice in self.choices
        }:
            raise BacktestValidationError(
                BacktestErrorCode.INVALID_REQUEST,
                f"parameter_set.{self.name} is not one of the manifest-declared choices",
            )
        return normalized

    def to_projection(self) -> dict[str, object]:
        return {
            "name": self.name,
            "type": self.value_type.value,
            "default": self.default,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "choices": list(self.choices),
        }


def _parameter_bound(value: object | None, name: str, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise StrategyIdentityError(f"backtest parameter '{name}' {field_name} must be a finite number")
    return float(value)


def _parameter_identity(value: object) -> tuple[str, str]:
    return (type(value).__name__, repr(value))


_DEFAULT_DATA_REQUIREMENT = DataRequirement(
    lookback_bars=60,
    future_label_horizon_days=0,
    requires_daily_bars=True,
)
_DEFAULT_BACKTEST_PARAMETERS = (
    BacktestParameterSpec(
        name="minimum_signal_score",
        value_type=BacktestParameterType.NUMBER,
        default=70.0,
        minimum=0.0,
        maximum=100.0,
    ),
)


@dataclass(frozen=True)
class StrategyDescriptor:
    """The sole immutable strategy identity and declared execution metadata."""

    strategy_id: str
    name: str
    mode: StrategyMode
    declared_version: str
    manifest_hash: str
    implementation_hash: str
    effective_version: str
    requirements: tuple[str, ...]
    capabilities: tuple[str, ...]
    status: StrategyStatus
    # Signal metadata is manifest-owned and injected by adapters. Implementations
    # cannot provide another mutable source for these values.
    signal_weight: float = 1.0
    strategy_independence: float = 0.85
    deprecation_reason: str | None = None
    replacement_strategy_id: str | None = None
    data_requirement: DataRequirement = field(default_factory=lambda: _DEFAULT_DATA_REQUIREMENT)
    backtest_parameters: tuple[BacktestParameterSpec, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.data_requirement, DataRequirement):
            raise StrategyIdentityError("StrategyDescriptor.data_requirement must be a DataRequirement")
        parameters = tuple(self.backtest_parameters)
        if not all(isinstance(item, BacktestParameterSpec) for item in parameters):
            raise StrategyIdentityError("StrategyDescriptor.backtest_parameters must contain BacktestParameterSpec values")
        names = tuple(item.name for item in parameters)
        if len(names) != len(set(names)):
            raise StrategyIdentityError("StrategyDescriptor.backtest_parameters must not repeat parameter names")
        object.__setattr__(self, "backtest_parameters", parameters)

    @property
    def display_name(self) -> str:
        return self.name

    @property
    def effective_fingerprint(self) -> str:
        return self.effective_version

    @property
    def deprecated(self) -> bool:
        return self.status is StrategyStatus.DEPRECATED

    @property
    def enabled(self) -> bool:
        return self.status is StrategyStatus.ACTIVE

    @property
    def available_for_execution(self) -> bool:
        return self.enabled and not self.deprecated

    def to_projection(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "skill_id": self.strategy_id,
            "name": self.name,
            "display_name": self.display_name,
            "mode": self.mode.value,
            "declared_version": self.declared_version,
            "manifest_hash": self.manifest_hash,
            "implementation_hash": self.implementation_hash,
            "effective_version": self.effective_version,
            "requirements": list(self.requirements),
            "capabilities": list(self.capabilities),
            "status": self.status.value,
            "enabled": self.enabled,
            "signal_metadata": {
                "weight": self.signal_weight,
                "strategy_independence": self.strategy_independence,
            },
            "deprecated": self.deprecated,
            "deprecation_reason": self.deprecation_reason,
            "replacement_strategy_id": self.replacement_strategy_id,
            "data_requirement": self.data_requirement.canonical_payload(),
            "backtest_parameters": [item.to_projection() for item in self.backtest_parameters],
        }


@dataclass(frozen=True)
class StrategyContext:
    """Read-only context for one per-stock strategy evaluation."""

    main_industries: list[str] = field(default_factory=list)
    skip_universe_check: bool = False


@runtime_checkable
class Strategy(Protocol):
    def evaluate(self, stock: dict[str, Any], ctx: StrategyContext) -> Signal:
        ...


class DeclarativeStrategy:
    """Descriptor-bound adapter for the existing declarative evaluator."""

    __slots__ = ("skill", "descriptor", "_runtime_skill")

    def __init__(self, skill: StrategySkill) -> None:
        self.skill = skill
        self.descriptor: StrategyDescriptor | None = None
        self._runtime_skill: StrategySkill | None = None

    @property
    def skill_id(self) -> str:
        return self.skill.skill_id

    def bind_descriptor(self, descriptor: StrategyDescriptor) -> None:
        if descriptor.strategy_id != self.skill_id:
            raise StrategyIdentityError(
                f"声明式策略 '{self.skill_id}' 不能绑定 id 为 '{descriptor.strategy_id}' 的 Descriptor。"
            )
        self.descriptor = descriptor
        self._runtime_skill = _frozen_skill_snapshot(self.skill)

    def evaluate(self, stock: dict[str, Any], ctx: StrategyContext) -> Signal:
        signal = run_strategy(
            self._runtime_skill or self.skill,
            stock,
            ctx.main_industries,
            skip_universe_check=ctx.skip_universe_check,
        )
        if self.descriptor is None:
            return signal
        if signal["strategy_id"] != self.descriptor.strategy_id:
            raise StrategyIdentityError(
                f"声明式策略 Signal id '{signal['strategy_id']}' 与 Descriptor "
                f"'{self.descriptor.strategy_id}' 不一致。"
            )
        projected = dict(signal)
        projected["strategy_id"] = self.descriptor.strategy_id
        projected["strategy_name"] = self.descriptor.name
        projected["version_id"] = self.descriptor.effective_version
        return cast(Signal, projected)


class _DescriptorBoundStrategy:
    """Wrap structural non-imperative implementations without a descriptor slot."""

    __slots__ = ("_implementation", "descriptor")

    def __init__(self, implementation: Strategy, descriptor: StrategyDescriptor) -> None:
        self._implementation = implementation
        self.descriptor = descriptor

    def evaluate(self, stock: dict[str, Any], ctx: StrategyContext) -> Signal:
        signal = dict(self._implementation.evaluate(stock, ctx))
        signal["strategy_id"] = self.descriptor.strategy_id
        signal["strategy_name"] = self.descriptor.name
        signal["version_id"] = self.descriptor.effective_version
        return cast(Signal, signal)


class StrategyRegistry:
    """Official registry keyed only by ``StrategyDescriptor.strategy_id``."""

    def __init__(self) -> None:
        self._strategies: dict[str, Strategy] = {}
        self._descriptors: dict[str, StrategyDescriptor] = {}
        self._frozen = False

    @property
    def is_frozen(self) -> bool:
        return self._frozen

    def freeze(self) -> None:
        self._frozen = True

    def _assert_mutable(self) -> None:
        if self._frozen:
            raise StrategyRegistryFrozenError()

    def register(self, descriptor: StrategyDescriptor, strategy: Strategy) -> Strategy:
        if not isinstance(descriptor, StrategyDescriptor):
            raise StrategyError("StrategyRegistry.register 必须接收 StrategyDescriptor，不能使用独立字符串 key。")
        self._assert_mutable()
        if descriptor.strategy_id in self._strategies:
            raise DuplicateStrategyError(descriptor.strategy_id)
        if not callable(getattr(strategy, "evaluate", None)):
            raise StrategyError(f"策略 '{descriptor.strategy_id}' 必须实现可调用的 evaluate 方法。")
        if descriptor.mode is StrategyMode.IMPERATIVE:
            from app.engine.imperative_strategy import DescriptorBoundImperativeStrategy

            if not isinstance(strategy, DescriptorBoundImperativeStrategy) or strategy.descriptor is not descriptor:
                raise StrategyError(
                    f"命令式策略 '{descriptor.strategy_id}' 必须注册为绑定同一 Descriptor 对象的 "
                    "DescriptorBoundImperativeStrategy；不接受兼容的 Signal-returning 实现。"
                )
        elif getattr(strategy, "descriptor", None) is not descriptor:
            strategy = _DescriptorBoundStrategy(strategy, descriptor)
        self._strategies[descriptor.strategy_id] = strategy
        self._descriptors[descriptor.strategy_id] = descriptor
        return strategy

    def unregister(self, strategy_id: str) -> None:
        self._assert_mutable()
        self._strategies.pop(strategy_id, None)
        self._descriptors.pop(strategy_id, None)

    def clear(self) -> None:
        self._assert_mutable()
        self._strategies.clear()
        self._descriptors.clear()

    def get(self, strategy_id: str) -> Strategy:
        try:
            return self._strategies[strategy_id]
        except KeyError:
            raise StrategyNotFoundError(strategy_id, self.ids()) from None

    def get_descriptor(self, strategy_id: str) -> StrategyDescriptor:
        try:
            return self._descriptors[strategy_id]
        except KeyError:
            raise StrategyNotFoundError(strategy_id, self.ids()) from None

    def resolve(
        self,
        strategy_id: str,
        *,
        effective_version: str | None = None,
        allow_unavailable: bool = False,
    ) -> tuple[StrategyDescriptor, Strategy]:
        descriptor = self.get_descriptor(strategy_id)
        if not allow_unavailable and not descriptor.available_for_execution:
            raise StrategyUnavailableError(descriptor)
        if effective_version is not None and effective_version != descriptor.effective_version:
            raise StrategyIdentityError(
                f"策略 '{strategy_id}' 当前有效版本为 '{descriptor.effective_version}'，"
                f"不能解析请求的 '{effective_version}'。"
            )
        return descriptor, self.get(strategy_id)

    def list_strategies(self) -> list[Strategy]:
        return list(self._strategies.values())

    def list_descriptors(self) -> tuple[StrategyDescriptor, ...]:
        return tuple(self._descriptors.values())

    def ids(self) -> tuple[str, ...]:
        return tuple(self._strategies)

    def __contains__(self, strategy_id: object) -> bool:
        return strategy_id in self._strategies

    def __len__(self) -> int:
        return len(self._strategies)


def build_strategy_descriptor(
    skill: StrategySkill,
    implementation: object | None = None,
) -> StrategyDescriptor:
    """Build the immutable descriptor from one validated StrategySkill instance."""
    mode = _strategy_mode(skill)
    _validate_skill_identity(skill, mode)
    manifest = skill.manifest
    status = _strategy_status(skill)
    implementation_target = implementation if implementation is not None else run_strategy
    manifest_hash = _manifest_fingerprint(skill, mode)
    implementation_hash = _implementation_fingerprint(implementation_target)
    effective_version = hashlib.sha256(
        f"{manifest_hash}:{implementation_hash}:{STRATEGY_DESCRIPTOR_CONTRACT_VERSION}".encode("utf-8")
    ).hexdigest()
    signal_weight, strategy_independence = _signal_metadata(manifest)
    data_requirement = _data_requirement_from_manifest(manifest)
    backtest_parameters = _backtest_parameters_from_manifest(manifest)
    return StrategyDescriptor(
        strategy_id=skill.skill_id,
        name=skill.name,
        mode=mode,
        declared_version=str(skill.version),
        manifest_hash=manifest_hash,
        implementation_hash=implementation_hash,
        effective_version=effective_version,
        requirements=_manifest_string_tuple(manifest, "requirements"),
        capabilities=_manifest_string_tuple(manifest, "capabilities"),
        status=status,
        signal_weight=signal_weight,
        strategy_independence=strategy_independence,
        deprecation_reason=_optional_manifest_string(manifest, "deprecation_reason"),
        replacement_strategy_id=_optional_manifest_string(manifest, "replacement_strategy_id"),
        data_requirement=data_requirement,
        backtest_parameters=backtest_parameters,
    )


def instantiate_strategy(skill: StrategySkill) -> Strategy:
    """Assemble an implementation and bind its sole descriptor at bootstrap."""
    impl_class = skill.impl_class
    if not impl_class:
        strategy = DeclarativeStrategy(skill)
        descriptor = build_strategy_descriptor(skill)
        strategy.bind_descriptor(descriptor)
        return strategy

    module_path, class_name = _parse_impl_class(skill, impl_class)
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise StrategyAssemblyError(
            f"策略 '{skill.skill_id}' 装配失败：无法 import 模块 '{module_path}'（impl_class='{impl_class}'）：{exc}"
        ) from exc
    try:
        strategy_cls = getattr(module, class_name)
    except AttributeError as exc:
        raise StrategyAssemblyError(
            f"策略 '{skill.skill_id}' 装配失败：模块 '{module_path}' 内找不到类 '{class_name}'（impl_class='{impl_class}'）。"
        ) from exc
    try:
        implementation = strategy_cls()
    except Exception as exc:  # noqa: BLE001
        raise StrategyAssemblyError(
            f"策略 '{skill.skill_id}' 装配失败：实例化 '{impl_class}' 时抛出异常：{exc}"
        ) from exc

    from app.engine.imperative_strategy import (
        DescriptorBoundImperativeStrategy,
        ImperativeStrategy,
        ImperativeStrategyProtocolError,
        validate_imperative_implementation,
    )

    if not isinstance(implementation, ImperativeStrategy):
        raise StrategyAssemblyError(
            f"策略 '{skill.skill_id}' 装配失败：'{impl_class}' 的实例不满足命令式 Strategy 协议"
            "（命令式策略 Protocol，缺可调用的 evaluate(stock, ctx) 方法）。"
        )
    try:
        validate_imperative_implementation(implementation)
    except ImperativeStrategyProtocolError as exc:
        message = str(exc)
        if "不得重复声明" in message:
            raise StrategyIdentityError(f"命令式策略 '{skill.skill_id}' 的{message}") from exc
        raise StrategyAssemblyError(
            f"策略 '{skill.skill_id}' 装配失败：'{impl_class}' 不满足命令式策略 Protocol（{message}）。"
        ) from exc
    descriptor = build_strategy_descriptor(skill, implementation)
    return DescriptorBoundImperativeStrategy(implementation, descriptor)


def build_registry_from_skills(skills: list[StrategySkill]) -> StrategyRegistry:
    """Perform one bootstrap pass and publish a frozen descriptor-backed registry."""
    registry = StrategyRegistry()
    for skill in skills:
        if skill.mode == StrategyMode.IMPERATIVE.value and not skill.impl_class:
            raise StrategyAssemblyError(
                f"命令式策略 '{skill.skill_id}' 缺少 manifest.impl_class，无法装配。"
            )
        if not skill.executable:
            continue
        strategy = instantiate_strategy(skill)
        descriptor = _descriptor_of(strategy)
        registry.register(descriptor, strategy)
    registry.freeze()
    return registry


def _descriptor_of(strategy: Strategy) -> StrategyDescriptor:
    descriptor = getattr(strategy, "descriptor", None)
    if not isinstance(descriptor, StrategyDescriptor):
        raise StrategyAssemblyError("策略装配成功但未绑定不可变 StrategyDescriptor。")
    return descriptor


def _frozen_skill_snapshot(skill: StrategySkill) -> StrategySkill:
    """Project manifest identity into the legacy YAML runtime contract once."""
    runtime_contract: dict[str, Any] | None = None
    if skill.contract is not None:
        runtime_contract = dict(skill.contract)
        runtime_contract["id"] = skill.skill_id
        runtime_contract["name"] = skill.name
    return StrategySkill(
        skill_id=skill.skill_id,
        name=skill.name,
        version=skill.version,
        path=skill.path,
        manifest=cast(dict[str, Any], _freeze_value(skill.manifest)),
        skill_frontmatter=cast(dict[str, Any], _freeze_value(skill.skill_frontmatter)),
        skill_body=skill.skill_body,
        skill_hash=skill.skill_hash,
        reference_files=cast(list[dict[str, Any]], _freeze_value(skill.reference_files)),
        contract=cast(dict[str, Any] | None, _freeze_value(runtime_contract)),
        contract_hash=skill.contract_hash,
        enabled=skill.enabled,
    )


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze_value(item) for item in value)
    return value


def _parse_impl_class(skill: StrategySkill, impl_class: str) -> tuple[str, str]:
    if ":" not in impl_class:
        raise StrategyAssemblyError(
            f"策略 '{skill.skill_id}' 的 impl_class 格式不合法：'{impl_class}'，期望格式 'module.path:ClassName'。"
        )
    module_path, _, class_name = impl_class.partition(":")
    module_path, class_name = module_path.strip(), class_name.strip()
    if not module_path or not class_name:
        raise StrategyAssemblyError(
            f"策略 '{skill.skill_id}' 的 impl_class 格式不合法：'{impl_class}'，模块路径与类名均不能为空。"
        )
    return module_path, class_name


def _strategy_mode(skill: StrategySkill) -> StrategyMode:
    declared = skill.manifest.get("mode") if isinstance(skill.manifest, Mapping) else None
    try:
        mode = StrategyMode(str(declared).lower())
    except ValueError as exc:
        raise StrategyIdentityError(
            f"策略 '{skill.skill_id}' 的 manifest.mode='{declared}' 无效，应为 declarative 或 imperative。"
        ) from exc
    inferred = StrategyMode.IMPERATIVE if skill.impl_class else StrategyMode.DECLARATIVE
    if mode is not inferred:
        raise StrategyIdentityError(
            f"策略 '{skill.skill_id}' 的 manifest.mode='{mode.value}' 与 impl_class 推导的 '{inferred.value}' 不一致。"
        )
    return mode


def _strategy_status(skill: StrategySkill) -> StrategyStatus:
    raw_status = skill.manifest.get("status") if isinstance(skill.manifest, Mapping) else None
    try:
        status = StrategyStatus(str(raw_status).lower())
    except ValueError as exc:
        raise StrategyIdentityError(
            f"策略 '{skill.skill_id}' 的 manifest.status='{raw_status}' 无效，应为 active、disabled 或 deprecated。"
        ) from exc
    if skill.enabled is not (status is StrategyStatus.ACTIVE):
        raise StrategyIdentityError(
            f"策略 '{skill.skill_id}' 的 legacy manifest.enabled 必须是 status='{status.value}' 的投影。"
        )
    if status is StrategyStatus.DEPRECATED and not _optional_manifest_string(skill.manifest, "deprecation_reason"):
        raise StrategyIdentityError(f"已弃用策略 '{skill.skill_id}' 必须提供 manifest.deprecation_reason。")
    return status


def _validate_skill_identity(skill: StrategySkill, mode: StrategyMode) -> None:
    manifest = skill.manifest
    if not isinstance(manifest, Mapping):
        raise StrategyIdentityError("StrategySkill 必须提供 manifest。")
    for key, expected in (
        ("id", skill.skill_id),
        ("name", skill.name),
        ("version", str(skill.version)),
    ):
        actual = manifest.get(key)
        if not isinstance(actual, str) or actual.strip() != expected:
            raise StrategyIdentityError(
                f"策略身份不一致：manifest.{key}='{actual}'，但 StrategySkill 对应值为 '{expected}'。"
            )
    for key in ("mode", "status", "requirements", "capabilities"):
        if key not in manifest:
            raise StrategyIdentityError(f"策略 '{skill.skill_id}' 的 manifest 缺少必填字段 '{key}'。")
    _manifest_string_tuple(manifest, "requirements")
    _manifest_string_tuple(manifest, "capabilities")
    _signal_metadata(manifest)
    _data_requirement_from_manifest(manifest)
    _backtest_parameters_from_manifest(manifest)
    _strategy_status(skill)
    for source_name, source in (("strategy.yaml", skill.contract or {}),):
        if not isinstance(source, Mapping):
            continue
        for key, expected in (("id", skill.skill_id), ("name", skill.name)):
            actual = source.get(key)
            if actual not in (None, "") and str(actual) != expected:
                raise StrategyIdentityError(
                    f"策略身份不一致：{source_name}.{key}='{actual}'，但 manifest.{key}='{expected}'。"
                )
    if mode is StrategyMode.DECLARATIVE and skill.contract is None:
        raise StrategyIdentityError(f"声明式策略 '{skill.skill_id}' 缺少 strategy.yaml contract。")


def _data_requirement_from_manifest(manifest: Mapping[str, Any]) -> DataRequirement:
    """Build an immutable read contract from an optional manifest declaration.

    Legacy manifests are upgraded to a conservative daily-bar requirement rather
    than being granted unbounded data access.  New manifests can declare stricter
    daily/minute/benchmark/auxiliary requirements explicitly.
    """

    raw = manifest.get("data_requirement")
    if raw is None:
        return _DEFAULT_DATA_REQUIREMENT
    if not isinstance(raw, Mapping):
        raise StrategyIdentityError("manifest.data_requirement must be an object")
    raw_auxiliary = raw.get("auxiliary_datasets", ())
    if isinstance(raw_auxiliary, (str, bytes)) or not isinstance(raw_auxiliary, (list, tuple)):
        raise StrategyIdentityError("manifest.data_requirement.auxiliary_datasets must be a list")
    try:
        auxiliary = tuple(
            AuxiliaryDataRequirement(
                key=str(item.get("key") or ""),
                minimum_coverage=item.get("minimum_coverage", 0.95),
                release_lag_days=item.get("release_lag_days", 0),
            )
            for item in raw_auxiliary
            if isinstance(item, Mapping)
        )
    except BacktestValidationError as exc:
        raise StrategyIdentityError(f"invalid manifest.data_requirement auxiliary dataset: {exc}") from exc
    if len(auxiliary) != len(raw_auxiliary):
        raise StrategyIdentityError("manifest.data_requirement.auxiliary_datasets items must be objects")
    try:
        return DataRequirement(
            lookback_bars=raw.get("lookback_bars", _DEFAULT_DATA_REQUIREMENT.lookback_bars),
            future_label_horizon_days=raw.get("future_label_horizon_days", 0),
            requires_daily_bars=raw.get("requires_daily_bars", True),
            requires_minute_bars=raw.get("requires_minute_bars", False),
            benchmark_code=raw.get("benchmark_code"),
            auxiliary_datasets=auxiliary,
            minimum_coverage=raw.get("minimum_coverage", 0.95),
        )
    except BacktestValidationError as exc:
        raise StrategyIdentityError(f"invalid manifest.data_requirement: {exc}") from exc


def _backtest_parameters_from_manifest(manifest: Mapping[str, Any]) -> tuple[BacktestParameterSpec, ...]:
    """Parse manifest-owned parameter specifications and reject implicit overrides."""

    raw = manifest.get("backtest_parameters")
    if raw is None:
        # This versioned compatibility schema is intentionally narrow.  It keeps
        # old manifests executable while accepting only the legacy public score
        # threshold; every new override must appear in the manifest itself.
        return _DEFAULT_BACKTEST_PARAMETERS
    if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
        raise StrategyIdentityError("manifest.backtest_parameters must be a list")
    try:
        parameters = tuple(
            BacktestParameterSpec(
                name=str(item.get("name") or ""),
                value_type=item.get("type", item.get("value_type", "")),
                default=item.get("default"),
                minimum=item.get("minimum"),
                maximum=item.get("maximum"),
                choices=tuple(item.get("choices") or ()),
            )
            for item in raw
            if isinstance(item, Mapping)
        )
    except (BacktestValidationError, StrategyIdentityError) as exc:
        raise StrategyIdentityError(f"invalid manifest.backtest_parameters: {exc}") from exc
    if len(parameters) != len(raw):
        raise StrategyIdentityError("manifest.backtest_parameters items must be objects")
    names = tuple(item.name for item in parameters)
    if len(names) != len(set(names)):
        raise StrategyIdentityError("manifest.backtest_parameters must not repeat names")
    return parameters


def validate_backtest_parameter_set(
    descriptor: StrategyDescriptor,
    parameter_set: Mapping[str, Any] | None,
) -> Mapping[str, object]:
    """Normalize only manifest-declared strategy parameters into a frozen mapping."""

    if not isinstance(descriptor, StrategyDescriptor):
        raise BacktestValidationError(BacktestErrorCode.INVALID_REQUEST, "a StrategyDescriptor is required")
    if parameter_set is None:
        values: Mapping[str, Any] = {}
    elif isinstance(parameter_set, Mapping):
        values = parameter_set
    else:
        raise BacktestValidationError(BacktestErrorCode.INVALID_REQUEST, "parameter_set must be an object")
    specs = {spec.name: spec for spec in descriptor.backtest_parameters}
    unexpected = sorted(str(key) for key in values if str(key) not in specs)
    if unexpected:
        raise BacktestValidationError(
            BacktestErrorCode.INVALID_REQUEST,
            f"parameter_set contains undeclared parameters: {', '.join(unexpected)}",
            details={"strategy_id": descriptor.strategy_id, "undeclared_parameters": unexpected},
        )
    normalized: dict[str, object] = {}
    for name, spec in specs.items():
        if name in values:
            normalized[name] = spec.normalize(values[name])
        elif spec.default is not None:
            normalized[name] = spec.default
    return MappingProxyType(dict(sorted(normalized.items())))


def _manifest_string_tuple(manifest: Mapping[str, Any], field_name: str) -> tuple[str, ...]:
    raw = manifest.get(field_name)
    if not isinstance(raw, (list, tuple)) or any(not isinstance(item, str) or not item.strip() for item in raw):
        raise StrategyIdentityError(f"manifest.{field_name} 必须为非空字符串组成的列表。")
    return tuple(str(item).strip() for item in raw)


def _signal_metadata(manifest: Mapping[str, Any]) -> tuple[float, float]:
    raw = manifest.get("signal_metadata", {})
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise StrategyIdentityError("manifest.signal_metadata 必须为 object。")
    try:
        weight = float(raw.get("weight", 1.0))
        independence = float(raw.get("strategy_independence", 0.85))
    except (TypeError, ValueError) as exc:
        raise StrategyIdentityError("manifest.signal_metadata 的 weight 和 strategy_independence 必须为数字。") from exc
    if not math.isfinite(weight) or weight < 0:
        raise StrategyIdentityError("manifest.signal_metadata.weight 必须为非负有限数。")
    if not math.isfinite(independence) or not 0 <= independence <= 1:
        raise StrategyIdentityError("manifest.signal_metadata.strategy_independence 必须位于 [0, 1]。")
    return weight, independence


def _optional_manifest_string(manifest: Mapping[str, Any], field_name: str) -> str | None:
    value = manifest.get(field_name)
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise StrategyIdentityError(f"manifest.{field_name} 必须为字符串。")
    return value.strip() or None


def _manifest_fingerprint(skill: StrategySkill, mode: StrategyMode) -> str:
    payload = {
        "strategy_id": skill.skill_id,
        "name": skill.name,
        "mode": mode.value,
        "declared_version": str(skill.version),
        "manifest": skill.manifest,
        "contract": skill.contract,
        "skill_hash": skill.skill_hash,
        "contract_hash": skill.contract_hash,
        "reference_files": skill.reference_files,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _implementation_fingerprint(implementation: object) -> str:
    targets = implementation.__class__.mro() if not inspect.isfunction(implementation) else [implementation]
    parts: list[bytes] = []
    seen_paths: set[Path] = set()
    for target in targets:
        if target is object:
            continue
        source_path = inspect.getsourcefile(target)
        if source_path:
            path = Path(source_path)
            if path not in seen_paths and path.exists():
                seen_paths.add(path)
                parts.append(path.read_bytes())
                continue
        module = getattr(target, "__module__", type(target).__module__)
        qualname = getattr(target, "__qualname__", type(target).__qualname__)
        parts.append(f"{module}:{qualname}".encode("utf-8"))
    return hashlib.sha256(b"\n--strategy-source--\n".join(parts)).hexdigest()


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
