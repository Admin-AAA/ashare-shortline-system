"""Factor Registry：显式 bootstrap、图校验、确定性 ExecutionPlan 与冻结边界。"""
from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
from typing import Any, Union

from app.engine.factor_plan import FactorExecutionPlan, FactorPlanEntry

from .base import (
    Factor,
    FactorCycleError,
    FactorDependencyError,
    FactorError,
    FactorOutputOwnershipError,
    FactorRegistryFrozenError,
    depends_on_of,
    output_fields_of,
)


Registrable = Union[Factor, type]


class FactorRegistry:
    """可局部构建的 Factor Registry；成功 bootstrap 后不可再变更。"""

    def __init__(self) -> None:
        self._factors: dict[str, Factor] = {}
        self._execution_plan: FactorExecutionPlan | None = None
        self._frozen = False

    # ── 生命周期 ──────────────────────────────────────────────────
    @property
    def is_frozen(self) -> bool:
        return self._frozen

    @property
    def execution_plan(self) -> FactorExecutionPlan:
        if self._execution_plan is None:
            raise FactorError("FactorRegistry 尚未 bootstrap；没有可执行的冻结 ExecutionPlan。")
        return self._execution_plan

    def bootstrap(self) -> FactorExecutionPlan:
        """验证整个图、生成确定性 plan，并冻结此 Registry。"""
        if self._frozen:
            return self.execution_plan
        ordered = self._resolve_order_unfrozen()
        self._validate_output_ownership(ordered)
        entries = tuple(
            FactorPlanEntry(
                factor_id=factor.id,
                output_fields=output_fields_of(factor),
                depends_on=depends_on_of(factor),
                implementation_hash=_factor_implementation_hash(factor),
            )
            for factor in ordered
        )
        self._execution_plan = FactorExecutionPlan(entries=entries)
        self._frozen = True
        return self._execution_plan

    def freeze(self) -> FactorExecutionPlan:
        """``bootstrap`` 的语义化别名。"""
        return self.bootstrap()

    # ── 注册 ──────────────────────────────────────────────────────
    def register(self, factor_or_cls: Registrable) -> Registrable:
        self._assert_mutable()
        if isinstance(factor_or_cls, type):
            instance = factor_or_cls()
            self._add(instance)
            return factor_or_cls
        self._add(factor_or_cls)
        return factor_or_cls

    def _add(self, factor: Factor) -> None:
        fid = getattr(factor, "id", None)
        if not isinstance(fid, str) or not fid:
            raise FactorError("因子必须声明非空字符串属性 id。")
        if fid in self._factors:
            raise FactorError(f"因子 id 重复注册：'{fid}'。")
        if not callable(getattr(factor, "compute", None)):
            raise FactorError(f"因子 '{fid}' 必须实现可调用的 compute 方法。")
        fields = output_fields_of(factor)
        invalid = tuple(field for field in fields if not isinstance(field, str) or not field)
        if invalid:
            raise FactorError(f"因子 '{fid}' 的 output_fields 必须都是非空字符串：{list(invalid)}。")
        self._factors[fid] = factor

    def unregister(self, factor_id: str) -> None:
        self._assert_mutable()
        self._factors.pop(factor_id, None)

    def clear(self) -> None:
        self._assert_mutable()
        self._factors.clear()

    # ── 查询 ──────────────────────────────────────────────────────
    def get(self, factor_id: str) -> Factor:
        try:
            return self._factors[factor_id]
        except KeyError:
            raise FactorError(f"未注册的因子：'{factor_id}'。") from None

    def list_factors(self) -> list[Factor]:
        return list(self._factors.values())

    def ids(self) -> tuple[str, ...]:
        return tuple(self._factors)

    def __contains__(self, factor_id: object) -> bool:
        return factor_id in self._factors

    def __len__(self) -> int:
        return len(self._factors)

    # ── 图解析 ────────────────────────────────────────────────────
    def resolve_order(self) -> list[Factor]:
        """返回确定性执行序；冻结后只从已验证 ExecutionPlan 读取。"""
        if self._frozen:
            return [self._factors[factor_id] for factor_id in self.execution_plan.factor_ids]
        return self._resolve_order_unfrozen()

    def _resolve_order_unfrozen(self) -> list[Factor]:
        factors = self._factors
        known = set(factors)
        for fid, factor in factors.items():
            missing = tuple(dep for dep in depends_on_of(factor) if dep not in known)
            if missing:
                raise FactorDependencyError(fid, missing)

        order_index = {fid: index for index, fid in enumerate(factors)}
        indegree = {fid: len(set(depends_on_of(factors[fid]))) for fid in factors}
        dependents: dict[str, list[str]] = {fid: [] for fid in factors}
        for fid, factor in factors.items():
            for dependency in set(depends_on_of(factor)):
                dependents[dependency].append(fid)

        ready = sorted(
            (fid for fid, degree in indegree.items() if degree == 0),
            key=order_index.__getitem__,
        )
        ordered_ids: list[str] = []
        while ready:
            current = ready.pop(0)
            ordered_ids.append(current)
            newly_ready: list[str] = []
            for dependent in dependents[current]:
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    newly_ready.append(dependent)
            ready.extend(newly_ready)
            ready.sort(key=order_index.__getitem__)

        if len(ordered_ids) < len(factors):
            raise FactorCycleError(self._find_cycle())
        return [factors[fid] for fid in ordered_ids]

    def _validate_output_ownership(self, ordered: list[Factor]) -> None:
        owners: dict[str, str] = {}
        for factor in ordered:
            for output_field in output_fields_of(factor):
                owner = owners.get(output_field)
                if owner is not None and owner != factor.id:
                    raise FactorOutputOwnershipError(output_field, (owner, factor.id))
                owners[output_field] = factor.id

    def _find_cycle(self) -> tuple[str, ...]:
        factors = self._factors
        white, gray, black = 0, 1, 2
        color = {fid: white for fid in factors}
        path: list[str] = []

        def dfs(node: str) -> tuple[str, ...] | None:
            color[node] = gray
            path.append(node)
            for dependency in set(depends_on_of(factors[node])):
                if dependency not in factors:
                    continue
                if color[dependency] == gray:
                    start = path.index(dependency)
                    return tuple(path[start:] + [dependency])
                if color[dependency] == white:
                    found = dfs(dependency)
                    if found:
                        return found
            path.pop()
            color[node] = black
            return None

        for fid in factors:
            if color[fid] == white:
                found = dfs(fid)
                if found:
                    return found
        return tuple(factors)

    def _assert_mutable(self) -> None:
        if self._frozen:
            raise FactorRegistryFrozenError()


def _factor_implementation_hash(factor: Factor) -> str:
    """包含实现类及其基类源码；源码不可读时稳定回退为逻辑名。"""
    parts: list[bytes] = []
    seen_paths: set[Path] = set()
    for cls in factor.__class__.mro():
        if cls is object:
            continue
        source_path = inspect.getsourcefile(cls)
        if source_path:
            path = Path(source_path)
            if path.exists() and path not in seen_paths:
                seen_paths.add(path)
                parts.append(path.read_bytes())
                continue
        parts.append(f"{cls.__module__}:{cls.__qualname__}".encode("utf-8"))
    return hashlib.sha256(b"\n--factor-source--\n".join(parts)).hexdigest()


# 默认注册表只由显式 bootstrap 填充并立即冻结；测试应创建独立 FactorRegistry。
default_registry = FactorRegistry()


def bootstrap_default_factor_registry() -> FactorExecutionPlan:
    """显式注册内置因子、校验、白名单注入并冻结生产 Registry（幂等）。"""
    if default_registry.is_frozen:
        return default_registry.execution_plan

    # 导入只取得声明的因子类；shortline 模块本身不再以装饰器修改全局 Registry。
    from .shortline import BUILTIN_FACTORS

    for factor_cls in BUILTIN_FACTORS:
        default_registry.register(factor_cls)
    plan = default_registry.bootstrap()

    # 只有计划验证成功后才开放 DSL 输出字段，避免坏图污染 evaluator allowlist。
    from .allowlist import bootstrap_factor_allowlist

    bootstrap_factor_allowlist(default_registry)
    return plan


def register_factor(factor_or_cls: Registrable) -> Registrable:
    """兼容性注册入口；生产默认 Registry 冻结后会明确拒绝动态扩展。"""
    return default_registry.register(factor_or_cls)
