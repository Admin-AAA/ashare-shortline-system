"""策略信号契约（typed ``Signal``）。

本模块只做一件事：把「一次 per-stock 策略评估的产出」收敛成一个**唯一的、带类型的
契约**，供上游生产者（声明式 ``run_strategy`` / 后续 S3 的 ``DeclarativeStrategy`` 与
``ImperativeStrategy``）与下游消费者（``aggregator``）之间的 seam 使用。

设计取舍——为什么用 ``TypedDict`` 而不是 ``dataclass``：
    ``Signal`` 定义为 ``TypedDict``，因此它在**运行时就是一个普通 ``dict``**。
    ``strategy_runner._signal(...)`` 返回普通 dict 即可获得静态类型，``aggregator`` 及
    其他消费者仍可直接使用 ``signal["xxx"]``。若改用 ``dataclass``，则必须在
    ``run_strategy`` 返回边界再 ``asdict`` 转回 dict 并保持键序，徒增一层转换与漂移
    风险。故这里选择运行时零成本的 ``TypedDict``。

字段顺序与 ``strategy_runner._signal(...)`` 的 dict 字面量严格一一对应（见
``SIGNAL_FIELDS`` 断言用的键序），任何新增/删除/改序都应两处同步。
"""
from __future__ import annotations

from typing import TypedDict


ENTRY_MODE_TARGET_TOUCH = "target_touch"
ENTRY_MODE_NEXT_OPEN = "next_open"
VALID_ENTRY_MODES = frozenset({ENTRY_MODE_TARGET_TOUCH, ENTRY_MODE_NEXT_OPEN})


class Signal(TypedDict):
    """一次 per-stock 策略评估的产出契约。

    运行时即 ``dict[str, Any]``；下方注解为「如实标注」的字段类型，仅供静态检查与
    文档，不在运行时强制。
    """

    # 身份 / 加权
    strategy_id: str
    strategy_name: str
    version_id: str
    strategy_independence: float
    weight: float
    # 阶段与打分
    phase: str
    score: float
    score_cap: float
    confidence: float
    # 买点（setup）
    setup_match: str
    entry_rule: str
    invalid_rule: str
    entry_mode: str
    setup_quality: float
    # 风控 / 否决
    hard_veto: bool
    veto_reason: str
    risk_flags: list[str]
    evidence: list[str]


# 契约的规范键序（= ``_signal`` dict 字面量的插入顺序）。
# 供等价对拍与结构不变量测试引用，避免把「期望键序」散落在测试里各写一份。
SIGNAL_FIELDS: tuple[str, ...] = tuple(Signal.__annotations__.keys())
