"""因子框架（in-process）。

窄接口，深实现：对外只有「注册」与「运行」两组入口，排序 / 契约校验 / 合并
的复杂度都藏在实现体里。

- 定义因子：实现 ``Factor`` 协议或继承 ``BaseFactor``（id / output_fields /
  depends_on / metadata / compute(stock, ctx) -> dict）。
- 注册因子：``@register_factor`` 登记到进程级默认注册表，或用独立
  ``FactorRegistry`` 实例（测试友好）。
- 运行因子：``run_factors(stocks, ctx)`` 按 depends_on 拓扑序逐因子执行，返回值
  按序 merge 回工作 stock 供下游读取；对每只股票独立、不改动入参。
- 跨股聚合：由调用方一次性算好放进 ``FactorContext`` 注入。

- 白名单闭环：``output_fields`` 由 ``inject_factor_output_fields`` 自动并入
  ``evaluator.ALLOWED_NAMES``（在因子注册完成后经 ``bootstrap_factor_allowlist``
  调用一次），新增因子无需手工编辑白名单。
- 解释通用生成：``build_factor_explanations(stock)`` 遍历 metadata 声明了解释规格
  （``metadata["explanation"]``）的因子，复用其描述文本 + 从 stock 取产出值，拼出
  ``shortline_factor_explanations``，无需再硬编码逐因子拼装（见 ``explanations.py``）。
"""
from __future__ import annotations

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
)
from .driver import run_factors
from .explanations import (
    EXPLANATION_METADATA_KEY,
    build_factor_explanations,
    iter_explainable_factors,
)
from .registry import FactorRegistry, default_registry, register_factor

__all__ = [
    # 契约
    "Factor",
    "BaseFactor",
    "FactorContext",
    # 注册
    "FactorRegistry",
    "register_factor",
    "default_registry",
    # 运行
    "run_factors",
    # 解释通用生成（任务 2.4）
    "build_factor_explanations",
    "iter_explainable_factors",
    "EXPLANATION_METADATA_KEY",
    # 白名单注入（任务 2.2）
    "collect_factor_output_fields",
    "inject_factor_output_fields",
    "bootstrap_factor_allowlist",
    # 异常
    "FactorError",
    "FactorContractError",
    "FactorDependencyError",
    "FactorCycleError",
]
