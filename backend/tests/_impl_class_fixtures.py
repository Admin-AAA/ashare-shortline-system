"""任务 3.4 测试专用命令式策略实现（真实可 import 的模块）。

放在 ``tests/`` 目录顶层，与 ``_shortline_equiv_fixtures.py`` 同一手法：pytest 在无
``__init__.py`` 的目录下做 rootless collection 时，会把测试文件所在目录插入
``sys.path``（见 ``conftest.py`` 把 ``BACKEND_ROOT`` 插入 ``sys.path`` 的同一思路，
``tests/`` 目录本身也会被 pytest 隐式加入可 import 路径——``test_shortline_factors_equivalence.py``
里 ``from _shortline_equiv_fixtures import ...`` 就是先例）。

因此这里用**真实的模块路径**（``_impl_class_fixtures:ClassName``）供
``app.engine.strategy.instantiate_strategy`` 动态 import，而不是用 ``sys.modules``
打桩伪造一个模块——更贴近生产环境「skill 目录里的 Python 类通过真实模块路径被
import」的实际场景，也顺带验证「动态 import 任意可达模块路径」这条链路本身没问题。
"""
from __future__ import annotations

from typing import Any, ClassVar

from app.engine.imperative_strategy import ImperativeScoreResult, ImperativeStrategy
from app.engine.strategy import StrategyContext


class FixtureImperativeStrategy(ImperativeStrategy):
    """最小可用命令式策略：固定打分，供 ``instantiate_strategy`` 成功路径测试。"""

    strategy_id: ClassVar[str] = "impl-class-fixture"
    strategy_name: ClassVar[str] = "impl_class 装配测试策略"
    version: ClassVar[str] = "1.0.0"
    weight: ClassVar[float] = 1.2

    def score(self, stock: dict[str, Any], ctx: StrategyContext) -> ImperativeScoreResult:
        return ImperativeScoreResult(score=66.0, phase="fixture-phase")


class NotAStrategyAtAll:
    """不满足 ``Strategy`` 协议的类（无 ``evaluate`` 方法）。

    供「``impl_class`` 指向的类不满足 Strategy 协议」报错路径测试。
    """

    def __init__(self) -> None:
        self.marker = "not-a-strategy"


class RaisesOnInit:
    """实例化过程本身抛异常的类，供「装配失败」报错路径测试覆盖非协议类原因。"""

    def __init__(self) -> None:
        raise RuntimeError("模拟构造失败：缺少必需的运行时依赖")
