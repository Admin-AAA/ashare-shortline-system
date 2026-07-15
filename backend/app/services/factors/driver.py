"""因子驱动：对每只股票按拓扑序执行因子、校验返回契约、按序合并字段。

``run_factors`` 是这个 deep module 面向调用方的另一半窄接口（另一半是注册）。
它把下面这串复杂度收进实现体，调用方只需给出 ``stocks`` 与 ``ctx``：

1. 向注册表要一次拓扑序（内含循环 / 悬空依赖检测，一次算好、全体股票复用）；
2. 对每只股票独立地按序执行因子；
3. 每个因子只拿到工作 stock 的**只读视图**（含上游已 merge 字段），从而其原地
   写会被拦截 —— 这让「返回 key ⊆ output_fields」的契约真正闭合、无法绕过；
4. 校验返回值是 Mapping 且未越界，再把它 merge 回工作 stock 供下游读取；
5. 返回带全部因子字段的**新** stock 列表，绝不改动入参。
"""
from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Optional, Sequence

from .base import (
    Factor,
    FactorContext,
    FactorContractError,
    output_fields_of,
)
from .registry import FactorRegistry, bootstrap_default_factor_registry, default_registry


def run_factors(
    stocks: Sequence[Mapping[str, Any]],
    ctx: Optional[FactorContext] = None,
    *,
    registry: Optional[FactorRegistry] = None,
) -> "list[dict[str, Any]]":
    """按依赖拓扑序对每只股票运行全部已注册因子，返回合并后的新 stock 列表。

    参数
    ----
    stocks: 每只股票是一个字段映射（原始行情 / 派生字段等）。不会被修改。
    ctx:    跨股上下文（快照 + 一次性聚合）。缺省用空 ``FactorContext``。
    registry: 因子来源，缺省用进程级 ``default_registry``；测试可注入独立实例。

    异常
    ----
    FactorDependencyError / FactorCycleError: 排序阶段（启动即抛）。
    FactorContractError: 某因子返回越界字段或非 Mapping。
    """
    reg = registry if registry is not None else default_registry
    # 生产默认 Registry 必须先经过显式 bootstrap 并冻结；保留此处的惰性兜底，
    # 是为了让历史入口首次调用时也只能执行同一份冻结 plan，而非可变注册表。
    if reg is default_registry and not reg.is_frozen:
        bootstrap_default_factor_registry()
    context = ctx if ctx is not None else FactorContext()

    # 冻结 Registry 从 ExecutionPlan 取顺序；局部测试 Registry 保持现有按需解析语义。
    ordered = reg.resolve_order()

    results: "list[dict[str, Any]]" = []
    for stock in stocks:
        working: "dict[str, Any]" = dict(stock)  # 拷贝入参，绝不原地改动
        for factor in ordered:
            # 只读视图：因子只能读（含上游已 merge 的字段），任何原地写都会
            # 被 MappingProxyType 拒绝，避免绕过 output_fields 契约偷偷写字段。
            produced = factor.compute(MappingProxyType(working), context)
            _validate_contract(factor, produced)
            if produced:
                working.update(produced)
        results.append(working)
    return results


def _validate_contract(factor: Factor, produced: Any) -> None:
    """校验因子返回值：必须是 Mapping，且 key 是 output_fields 的子集。"""
    factor_id = getattr(factor, "id", "<unknown>")
    if produced is None:
        return  # 允许返回 None：视作本次不产出任何字段
    if not isinstance(produced, Mapping):
        raise FactorContractError(
            factor_id,
            f"因子 '{factor_id}' 的 compute 必须返回 dict / Mapping，"
            f"实际返回了 {type(produced).__name__}。",
            output_fields=output_fields_of(factor),
        )
    declared = set(output_fields_of(factor))
    undeclared = tuple(sorted(k for k in produced.keys() if k not in declared))
    if undeclared:
        raise FactorContractError(
            factor_id,
            undeclared=undeclared,
            output_fields=output_fields_of(factor),
        )
