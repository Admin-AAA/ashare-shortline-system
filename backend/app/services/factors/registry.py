"""因子注册表与依赖解析（拓扑排序 / 循环检测 / 悬空依赖检测）。

``FactorRegistry`` 是依赖图的持有者：``register`` 收集因子，``resolve_order``
把它们按 ``depends_on`` 解析为一个**确定性**的执行序，并在这一步拦截两类结构
错误——「引用未注册因子」（悬空依赖）与「循环依赖」。真正的执行（读 stock、
校验返回契约、按序 merge）交给 ``driver.run_factors``。

设计意图（deep module）：对外暴露 register / get / list_factors / resolve_order
这几个窄接口；Kahn 拓扑排序与 DFS 环提取的复杂度都收在实现体里。
"""
from __future__ import annotations

from typing import Union

from .base import (
    Factor,
    FactorCycleError,
    FactorDependencyError,
    FactorError,
    depends_on_of,
)

# 一个待注册项：可以是已构造好的因子实例，也可以是无参可实例化的因子类。
Registrable = Union[Factor, type]


class FactorRegistry:
    """进程内因子注册表。轻量、无外部依赖，可为每个用途/每次测试独立创建。"""

    def __init__(self) -> None:
        # dict 保持插入顺序 —— 作为拓扑排序在「多个因子同时就绪」时的确定性
        # tie-break 依据（先注册者先执行），从而保住迁移前的字段计算顺序。
        self._factors: "dict[str, Factor]" = {}

    # ── 注册 ──────────────────────────────────────────────────────
    def register(self, factor_or_cls: Registrable) -> Registrable:
        """注册一个因子。两种用法：

        - ``registry.register(instance)``：注册实例，返回该实例。
        - 作为类装饰器 ``@registry.register``：对无参可实例化的因子类，实例化后
          注册，并原样返回该类（不改写类，便于继续引用 / 继承）。
        """
        if isinstance(factor_or_cls, type):
            instance = factor_or_cls()  # 无参构造
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
        self._factors[fid] = factor

    def unregister(self, factor_id: str) -> None:
        """移除一个已注册因子（不存在则忽略）。"""
        self._factors.pop(factor_id, None)

    def clear(self) -> None:
        """清空注册表。"""
        self._factors.clear()

    # ── 查询 ──────────────────────────────────────────────────────
    def get(self, factor_id: str) -> Factor:
        try:
            return self._factors[factor_id]
        except KeyError:
            raise FactorError(f"未注册的因子：'{factor_id}'。") from None

    def list_factors(self) -> "list[Factor]":
        """按注册顺序列出全部因子实例。"""
        return list(self._factors.values())

    def ids(self) -> tuple[str, ...]:
        """按注册顺序列出全部因子 id。"""
        return tuple(self._factors)

    def __contains__(self, factor_id: object) -> bool:
        return factor_id in self._factors

    def __len__(self) -> int:
        return len(self._factors)

    # ── 依赖解析 ──────────────────────────────────────────────────
    def resolve_order(self) -> "list[Factor]":
        """把已注册因子按 ``depends_on`` 拓扑排序为确定性执行序。

        先做悬空依赖检查（引用未注册因子 → ``FactorDependencyError``），再做
        Kahn 拓扑排序；若存在环则抽取环上节点抛 ``FactorCycleError``。同时就绪
        的因子按注册顺序 tie-break，保证输出稳定可复现。
        """
        factors = self._factors
        known = set(factors)

        # 1) 悬空依赖：任一 depends_on 指向未注册因子 → 立即报错。
        for fid, factor in factors.items():
            missing = tuple(d for d in depends_on_of(factor) if d not in known)
            if missing:
                raise FactorDependencyError(fid, missing)

        # 2) Kahn 拓扑排序（确定性：ready 集合按注册序处理）。
        order_index = {fid: i for i, fid in enumerate(factors)}
        indegree = {fid: len(set(depends_on_of(factors[fid]))) for fid in factors}
        dependents: "dict[str, list[str]]" = {fid: [] for fid in factors}
        for fid, factor in factors.items():
            for dep in set(depends_on_of(factor)):
                dependents[dep].append(fid)

        ready = sorted(
            (fid for fid, deg in indegree.items() if deg == 0),
            key=order_index.__getitem__,
        )
        ordered: "list[str]" = []
        while ready:
            current = ready.pop(0)
            ordered.append(current)
            newly_ready = []
            for dependent in dependents[current]:
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    newly_ready.append(dependent)
            if newly_ready:
                ready.extend(newly_ready)
                ready.sort(key=order_index.__getitem__)

        # 3) 未能全部排序 → 图中有环，抽取一条环路径报清晰错误。
        if len(ordered) < len(factors):
            raise FactorCycleError(self._find_cycle())

        return [factors[fid] for fid in ordered]

    def _find_cycle(self) -> tuple[str, ...]:
        """DFS 抽取一条真实环路径（如 ``("B", "C", "B")``），用于报错信息。"""
        factors = self._factors
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {fid: WHITE for fid in factors}
        path: "list[str]" = []

        def dfs(node: str) -> tuple[str, ...] | None:
            color[node] = GRAY
            path.append(node)
            for dep in set(depends_on_of(factors[node])):
                if dep not in factors:
                    continue  # 悬空依赖已在 resolve_order 前拦截，此处防御性跳过
                if color[dep] == GRAY:  # 回边 → 找到环
                    start = path.index(dep)
                    return tuple(path[start:] + [dep])
                if color[dep] == WHITE:
                    found = dfs(dep)
                    if found:
                        return found
            path.pop()
            color[node] = BLACK
            return None

        for fid in factors:
            if color[fid] == WHITE:
                found = dfs(fid)
                if found:
                    return found
        return tuple(factors)  # 理论不可达：仅在确有环时才会调用本方法


# ── 进程级默认注册表 + 装饰器 ──────────────────────────────────────────
# 生产代码用 ``@register_factor`` 把因子登记到这个默认注册表（见任务 2.3 迁移
# 6 因子）。测试则各自 ``FactorRegistry()`` 独立实例，互不污染。
default_registry = FactorRegistry()


def register_factor(factor_or_cls: Registrable) -> Registrable:
    """把因子登记到进程级默认注册表。

    既可 ``@register_factor`` 装饰因子类，也可 ``register_factor(instance)``。
    """
    return default_registry.register(factor_or_cls)
