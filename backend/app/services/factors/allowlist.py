"""因子 ``output_fields`` → ``evaluator.ALLOWED_NAMES`` 自动注入（任务 2.2）。

闭环目标（需求 3.2）：一个因子声明的 ``output_fields`` 应自动成为策略 DSL 表达式
的合法引用名，免去过去「新增因子还得手工往 ``evaluator.ALLOWED_NAMES`` 补字段」
这一步——那是典型 shallow、易漏的重复编辑点。本模块把这一步闭合成一次调用。

**方向与循环 import**：注入由*因子侧* import evaluator 完成（factors → evaluator），
evaluator 不反向依赖 factors，天然无环。为进一步降低耦合、并避免「import factors
包就牵连 evaluator」，对 evaluator 采用**延迟 import**（仅在真正注入时才触碰）。

**调用时机（接线）**：注入必须在因子**全部注册之后**执行，否则会漏掉后注册的
因子。本任务只提供函数与显式 bootstrap 调用点；真实 6 因子要到任务 2.3 才注册，
届时在注册完成处调用一次 :func:`bootstrap_factor_allowlist` 即闭环（见其说明）。

**为何暂不删 evaluator 里的硬编码白名单**：6 因子尚未迁入因子框架（2.3），其
output_fields 目前仍只存在于 evaluator 的硬编码集合里。若现在就删，会在因子迁移
落地前打断策略对这些字段名的引用。清理留到 2.3 迁移完成、注入机制真正开始喂入
这些字段之后再做，避免出现「谁都不提供该字段名」的空窗。
"""
from __future__ import annotations

from .base import output_fields_of
from .registry import FactorRegistry, default_registry


def collect_factor_output_fields(registry: FactorRegistry) -> set[str]:
    """汇集一个注册表内**全部**因子声明的 ``output_fields``（并集）。

    对 tuple / list / None 一视同仁（经 ``output_fields_of`` 规整），不触碰因子实例
    本身；也无需先 ``resolve_order``——注入只关心「声明了哪些输出字段」，与依赖
    排序无关，故对含循环 / 悬空依赖的注册表同样能安全汇集。
    """
    fields: set[str] = set()
    for factor in registry.list_factors():
        fields.update(output_fields_of(factor))
    return fields


def inject_factor_output_fields(
    registry: FactorRegistry = default_registry,
    allowlist: set[str] | None = None,
) -> set[str]:
    """把 ``registry`` 中全部因子的 ``output_fields`` **并入** ``allowlist``（幂等）。

    参数
    ----
    registry: 因子来源。缺省进程级 ``default_registry``；测试可传独立实例避免污染。
    allowlist: 注入目标集合。缺省为 ``evaluator.ALLOWED_NAMES``（延迟 import 取得）；
        测试可传入一个新 ``set``，从而不动到全局白名单。

    语义
    ----
    - **并集、就地更新**：用 ``|=`` 原地合并，因此所有持有该集合引用者（如
      ``evaluate`` 内对模块级 ``ALLOWED_NAMES`` 的读取）都会立即看到新字段。
    - **幂等**：重复调用只是重复求并集，无副作用。
    - **只增不删**：绝不移除 ``allowlist`` 已有条目（保护 evaluator 现有硬编码项）。
    - 返回被就地更新后的同一个 ``allowlist`` 对象（便于断言 / 链式使用）。
    """
    if allowlist is None:
        # 延迟 import：仅注入时才触碰 evaluator；只 import factors 包不牵连它。
        from app.engine.evaluator import ALLOWED_NAMES

        allowlist = ALLOWED_NAMES
    allowlist |= collect_factor_output_fields(registry)
    return allowlist


def bootstrap_factor_allowlist(
    registry: FactorRegistry = default_registry,
) -> set[str]:
    """接线入口：因子注册完成后调用一次，把默认注册表的 ``output_fields`` 注入
    evaluator 全局白名单。

    任务 2.3 迁移 6 因子（``@register_factor`` 落入 ``default_registry``）后，只需在
    注册完成处调用本函数即闭环——此后新增因子仅需注册，其字段自动成为合法引用
    名，无需再手工编辑 evaluator 白名单。因幂等，重复调用亦安全。
    """
    return inject_factor_output_fields(registry=registry)
