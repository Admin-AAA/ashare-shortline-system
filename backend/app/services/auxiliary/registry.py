"""Bootstrap-time validation and freezing for Auxiliary Dataset descriptors."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Any

from app.services.auxiliary.contracts import AuxiliaryDatasetDescriptor


class AuxiliaryRegistryError(RuntimeError):
    """Base error for invalid registry assembly."""


class AuxiliaryRegistryFrozenError(AuxiliaryRegistryError):
    pass


class AuxiliaryDatasetNotFoundError(AuxiliaryRegistryError, KeyError):
    def __init__(self, dataset_key: str) -> None:
        self.dataset_key = dataset_key
        super().__init__(f"未注册 Auxiliary Dataset：'{dataset_key}'")


class DuplicateAuxiliaryDatasetError(AuxiliaryRegistryError):
    def __init__(self, dataset_key: str) -> None:
        self.dataset_key = dataset_key
        super().__init__(f"Auxiliary Dataset key 重复：'{dataset_key}'")


class DuplicateAuxiliaryViewKeyError(AuxiliaryRegistryError):
    def __init__(self, view_key: str) -> None:
        self.view_key = view_key
        super().__init__(f"Auxiliary Dataset loader 输出 key 重复：'{view_key}'")


class AuxiliaryDependencyError(AuxiliaryRegistryError):
    pass


class AuxiliaryDatasetRegistry:
    """An explicitly bootstrapped registry that becomes immutable after validation."""

    def __init__(self) -> None:
        self._descriptors: dict[str, AuxiliaryDatasetDescriptor[Any, Any, Any]] = {}
        self._frozen_descriptors: Mapping[str, AuxiliaryDatasetDescriptor[Any, Any, Any]] | None = None

    @property
    def is_frozen(self) -> bool:
        return self._frozen_descriptors is not None

    def register(
        self,
        descriptor: AuxiliaryDatasetDescriptor[Any, Any, Any],
    ) -> AuxiliaryDatasetDescriptor[Any, Any, Any]:
        if self.is_frozen:
            raise AuxiliaryRegistryFrozenError("冻结后的 Auxiliary Dataset Registry 禁止注册")
        if not isinstance(descriptor, AuxiliaryDatasetDescriptor):
            raise AuxiliaryRegistryError("Auxiliary Dataset Registry 只接受 AuxiliaryDatasetDescriptor")
        descriptor.validate()
        if descriptor.key in self._descriptors:
            raise DuplicateAuxiliaryDatasetError(descriptor.key)
        if any(existing.view_key == descriptor.view_key for existing in self._descriptors.values()):
            raise DuplicateAuxiliaryViewKeyError(descriptor.view_key)
        self._descriptors[descriptor.key] = descriptor
        return descriptor

    def freeze(self) -> "AuxiliaryDatasetRegistry":
        """Validate all descriptors and publish an immutable bootstrap snapshot."""
        if self.is_frozen:
            return self
        self._validate_descriptor_contracts()
        self._validate_dependencies()
        self._frozen_descriptors = MappingProxyType(dict(self._descriptors))
        return self

    def get(self, dataset_key: str) -> AuxiliaryDatasetDescriptor[Any, Any, Any]:
        source = self._frozen_descriptors if self._frozen_descriptors is not None else self._descriptors
        try:
            return source[dataset_key]
        except KeyError:
            raise AuxiliaryDatasetNotFoundError(dataset_key) from None

    def descriptors(self) -> tuple[AuxiliaryDatasetDescriptor[Any, Any, Any], ...]:
        source = self._frozen_descriptors if self._frozen_descriptors is not None else self._descriptors
        return tuple(source.values())

    def resolve_with_dependencies(
        self,
        dataset_keys: Iterable[str],
    ) -> tuple[AuxiliaryDatasetDescriptor[Any, Any, Any], ...]:
        """Return requested datasets plus dependencies in deterministic topological order."""
        if not self.is_frozen:
            raise AuxiliaryRegistryFrozenError("Auxiliary Dataset Registry 必须冻结后才能解析")
        ordered: list[AuxiliaryDatasetDescriptor[Any, Any, Any]] = []
        visited: set[str] = set()

        def visit(dataset_key: str) -> None:
            if dataset_key in visited:
                return
            descriptor = self.get(dataset_key)
            for dependency in descriptor.dependencies:
                visit(dependency)
            visited.add(dataset_key)
            ordered.append(descriptor)

        for key in dataset_keys:
            visit(key)
        return tuple(ordered)

    def _validate_descriptor_contracts(self) -> None:
        """Re-check every declaration at bootstrap before publishing it.

        The registration-time checks provide early feedback, while this final pass
        guarantees that the frozen mapping contains unique dataset/view keys and
        descriptors whose natural keys, handlers, and freshness configuration still
        satisfy the public contract.
        """
        descriptor_keys: set[str] = set()
        view_keys: set[str] = set()
        for registered_key, descriptor in self._descriptors.items():
            descriptor.validate()
            if descriptor.key != registered_key:
                raise AuxiliaryRegistryError(
                    "注册 key 与 descriptor.key 不一致；descriptor 不得在 bootstrap 前变更"
                )
            if descriptor.key in descriptor_keys:
                raise DuplicateAuxiliaryDatasetError(descriptor.key)
            if descriptor.view_key in view_keys:
                raise DuplicateAuxiliaryViewKeyError(descriptor.view_key)
            descriptor_keys.add(descriptor.key)
            view_keys.add(descriptor.view_key)

    def _validate_dependencies(self) -> None:
        for descriptor in self._descriptors.values():
            for dependency in descriptor.dependencies:
                if dependency not in self._descriptors:
                    raise AuxiliaryDependencyError(
                        f"数据集 '{descriptor.key}' 依赖未注册数据集 '{dependency}'"
                    )

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(dataset_key: str, trail: tuple[str, ...]) -> None:
            if dataset_key in visiting:
                cycle = " -> ".join((*trail, dataset_key))
                raise AuxiliaryDependencyError(f"Auxiliary Dataset 依赖存在循环：{cycle}")
            if dataset_key in visited:
                return
            visiting.add(dataset_key)
            descriptor = self._descriptors[dataset_key]
            for dependency in descriptor.dependencies:
                visit(dependency, (*trail, dataset_key))
            visiting.remove(dataset_key)
            visited.add(dataset_key)

        for key in self._descriptors:
            visit(key, ())


def build_auxiliary_registry(
    descriptors: Iterable[AuxiliaryDatasetDescriptor[Any, Any, Any]],
) -> AuxiliaryDatasetRegistry:
    """Construct a local, frozen registry; tests never mutate a process singleton."""
    registry = AuxiliaryDatasetRegistry()
    for descriptor in descriptors:
        registry.register(descriptor)
    return registry.freeze()


__all__ = [
    "AuxiliaryDatasetNotFoundError",
    "AuxiliaryDatasetRegistry",
    "AuxiliaryDependencyError",
    "AuxiliaryRegistryError",
    "AuxiliaryRegistryFrozenError",
    "DuplicateAuxiliaryDatasetError",
    "DuplicateAuxiliaryViewKeyError",
    "build_auxiliary_registry",
]
