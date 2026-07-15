"""真实可 import 的命令式策略装配测试 fixture。"""
from __future__ import annotations

from typing import Any, ClassVar

from app.engine.imperative_strategy import StrategyDecision
from app.engine.strategy import StrategyContext


class FixtureImperativeStrategy:
    """最小命令式实现：只有计算行为，没有身份或声明元数据。"""

    def evaluate(self, stock: dict[str, Any], ctx: StrategyContext) -> StrategyDecision:
        return StrategyDecision(score=66.0, phase="fixture-phase")


class NotAStrategyAtAll:
    """不满足命令式协议：缺少 evaluate。"""

    def __init__(self) -> None:
        self.marker = "not-a-strategy"


class RaisesOnInit:
    """实例化时抛异常，覆盖 bootstrap 失败路径。"""

    def __init__(self) -> None:
        raise RuntimeError("模拟构造失败：缺少必需的运行时依赖")


class MismatchedIdentityStrategy(FixtureImperativeStrategy):
    """非法的实现层身份声明，bootstrap 必须拒绝。"""

    strategy_id: ClassVar[str] = "implementation-owned-id"
    strategy_name: ClassVar[str] = "实现层错误名称"


class MetadataDeclaringStrategy(FixtureImperativeStrategy):
    """非法声明权重，验证元数据同样只能来自 manifest/Descriptor。"""

    weight: ClassVar[float] = 2.0


class RunModeDeclaringStrategy(FixtureImperativeStrategy):
    """非法声明运行模式，验证该身份同样只能来自 manifest。"""

    run_mode: ClassVar[str] = "formal"


class PrefilterDeclaringStrategy(FixtureImperativeStrategy):
    """旧 prefilter hook 不属于精简后的命令式协议。"""

    prefilter: ClassVar[dict[str, Any]] = {"market": {"exclude": ["is_st == true"]}}


class WrongEvaluateSignatureStrategy(FixtureImperativeStrategy):
    """参数形状不兼容，必须在 bootstrap 而非运行时被拒绝。"""

    def evaluate(self, stock: dict[str, Any]) -> StrategyDecision:
        return StrategyDecision(score=0.0)
