"""因子框架的核心契约：Factor 协议、只读跨股上下文、异常族。

这是这个 deep module 的接口面之一——对外只回答三件事：
- 「一个因子长什么样」→ ``Factor`` 协议（结构化契约）与 ``BaseFactor`` 便捷基类；
- 「跨股信息怎么传进来」→ ``FactorContext`` 载体（框架自身不产出聚合）；
- 「契约被违反时抛什么」→ 异常族（越界 / 悬空依赖 / 循环）。

拓扑排序、契约校验、字段合并等全部复杂度藏在 ``registry.py`` / ``driver.py``
的实现体里，调用方无需感知。因子实现只需纯 in-process 读入参、返回字段 dict。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


# ── 异常族 ────────────────────────────────────────────────────────────
class FactorError(Exception):
    """因子框架错误基类，便于调用方统一捕获。"""


class FactorContractError(FactorError):
    """因子 ``compute`` 违反输出契约：返回了 ``output_fields`` 之外的字段，
    或返回了非 Mapping 的值。"""

    def __init__(
        self,
        factor_id: str,
        message: str | None = None,
        *,
        undeclared: tuple[str, ...] = (),
        output_fields: tuple[str, ...] = (),
    ) -> None:
        self.factor_id = factor_id
        self.undeclared = tuple(undeclared)
        self.output_fields = tuple(output_fields)
        if message is None:
            message = (
                f"因子 '{factor_id}' 返回了未声明的字段 {list(self.undeclared)}；"
                f"其 output_fields 仅声明了 {list(self.output_fields)}。"
                "返回字段必须是 output_fields 的子集。"
            )
        super().__init__(message)


class FactorDependencyError(FactorError):
    """因子 ``depends_on`` 引用了未注册的因子。"""

    def __init__(self, factor_id: str, missing: tuple[str, ...]) -> None:
        self.factor_id = factor_id
        self.missing = tuple(missing)
        super().__init__(
            f"因子 '{factor_id}' 的 depends_on 引用了未注册的因子 {list(self.missing)}。"
        )


class FactorCycleError(FactorError):
    """因子依赖图中存在循环，拓扑排序无法进行。"""

    def __init__(self, cycle: tuple[str, ...]) -> None:
        self.cycle = tuple(cycle)
        super().__init__("因子依赖存在循环：" + " -> ".join(self.cycle))


# ── 跨股上下文载体 ─────────────────────────────────────────────────────
@dataclass
class FactorContext:
    """跨股聚合与市场快照的只读载体。

    因子 ``compute`` 只有两个信息来源：本只股票的字段（``stock`` 参数，含上游
    因子已 merge 的字段）与跨股信息（本 ``ctx``）。像 sector_stats /
    sentiment_phase / main_industries 这类需要扫全体股票才能算出的量，由调用方
    在每次运行前一次性算好塞进这里（见任务 2.3），避免每只股票重复计算，也让
    每个因子保持纯 in-process、可独立单测。

    框架本身不解释也不产出 ``aggregates`` 的具体内容——它只是个载体。
    """

    snapshot: Mapping[str, Any] = field(default_factory=dict)
    aggregates: Mapping[str, Any] = field(default_factory=dict)

    def aggregate(self, key: str, default: Any = None) -> Any:
        """读取一项跨股聚合；不存在时返回 ``default``。"""
        return self.aggregates.get(key, default)


# ── Factor 协议与便捷基类 ──────────────────────────────────────────────
@runtime_checkable
class Factor(Protocol):
    """一个因子的结构化契约（structural typing，实现类无需显式继承）。

    - ``id``: 全局唯一标识；注册、按 id 查询、被其它因子 ``depends_on`` 引用都用它。
    - ``output_fields``: 声明本因子会写出的字段全集。驱动据此校验返回值（返回
      key 必须 ⊆ 此集合），并在任务 2.2 自动注入 ``evaluator.ALLOWED_NAMES``。
    - ``depends_on``: 依赖的其它因子 id。驱动按此拓扑排序，保证上游先算、其输出
      先 merge 回工作 stock，供本因子读取（例如 leader 读 sector/capital）。
    - ``metadata``: 自描述（logic / data_sources / degradation 等），供任务 2.4
      通用生成因子解释；框架不解释其内容。
    - ``compute``: 读取 ``stock``（只读，含上游已 merge 字段）与 ``ctx``，返回
      「本因子新增字段」的 dict；不得原地修改 ``stock``。
    """

    id: str
    output_fields: tuple[str, ...]
    depends_on: tuple[str, ...]
    metadata: Mapping[str, Any]

    def compute(self, stock: Mapping[str, Any], ctx: FactorContext) -> dict[str, Any]:
        ...


class BaseFactor(ABC):
    """可选便捷基类：为常见因子省去样板。

    子类至少覆盖类属性 ``id`` 与 ``output_fields`` 并实现 ``compute``；
    ``depends_on`` 与 ``metadata`` 提供合理默认值。不想继承的实现，只要在结构上
    满足 ``Factor`` 协议同样被框架接受。
    """

    id: str = ""
    output_fields: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = {}

    @abstractmethod
    def compute(self, stock: Mapping[str, Any], ctx: FactorContext) -> dict[str, Any]:
        raise NotImplementedError


# ── 属性读取助手（对 tuple / list / None 一视同仁，且不修改因子本身）────────
def output_fields_of(factor: Any) -> tuple[str, ...]:
    """读取因子声明的 output_fields，规整为 tuple。"""
    return tuple(getattr(factor, "output_fields", ()) or ())


def depends_on_of(factor: Any) -> tuple[str, ...]:
    """读取因子声明的 depends_on，规整为 tuple。"""
    return tuple(getattr(factor, "depends_on", ()) or ())
