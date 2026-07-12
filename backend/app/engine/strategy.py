"""策略协议（``Strategy``）+ 声明式策略实现 + 策略注册表（任务 3.2）。

落点说明——为什么放在 ``engine/`` 而不是新建 ``services/strategies/`` 包：
    ``engine/`` 已经是「策略评估原语」的深模块聚集地——``evaluator.py``（表达式求值）、
    ``skill_loader.py``（``StrategySkill`` 契约与目录加载）、``strategy_runner.py``
    （声明式 YAML 解释器 + typed ``Signal`` 组装）、``signal.py``（3.1 产出的 ``Signal``
    契约）都在这里。``Strategy`` 协议本质上是这些原语之上再加一层「统一调用面」：
    它不引入新的业务逻辑，只是把「怎么拿到一个可 ``evaluate`` 的策略对象」这件事
    收敛成一个 seam，供 ``run_workbench`` / 回测预检等编排层调用。这与
    ``services/factors/`` 独立成包的理由（因子有自己的依赖图、跨股 ``FactorContext``、
    契约校验体系，是一个独立子域）不同——策略框架目前只有「协议 + 声明式适配器 +
    按 id 索引的注册表」三件套，体量与 ``signal.py``/``strategy_runner.py`` 相当，
    继续放在 ``engine/`` 保持 locality（调用方已经在 import ``app.engine.*``），
    不必为此新开一个包。若 3.3/3.4 落地 ``ImperativeStrategy``/manifest 装配后体量
    明显变大，可再考虑整包搬迁——但那是后续任务的判断，不在本任务范围内。

设计取舍：
    - ``Strategy`` 用 ``Protocol``（structural typing，非强制继承），与
      ``services/factors/base.py`` 的 ``Factor`` 协议手法一致。
    - ``StrategyContext`` 只搬运 ``run_strategy`` 现有调用点已经在用的两个参数
      （``main_industries`` / ``skip_universe_check``），不多不少——这是重构，不是
      新增行为。3.3 的 ``ImperativeStrategy`` 如需更多字段，再在这个 dataclass 上
      新增带默认值的字段，不破坏既有调用点。
    - ``DeclarativeStrategy`` 只做委托：``evaluate`` 内部原样调用既有
      ``run_strategy(skill, stock, ...)`` 并把返回值原样透传，不做任何字段改写。
      这是需求 4.4「声明式 YAML 路径行为逐字段不变」的落点。
    - ``StrategyRegistry`` 的设计感对齐 2.1 的 ``FactorRegistry``：进程内、无外部
      依赖、可各自独立实例化（测试互不污染）。与 ``FactorRegistry`` 不同的是，策略
      之间没有 ``depends_on`` 依赖图——每个策略独立对一只股票打分，互不依赖彼此的
      输出——所以本注册表只是一个按 ``skill_id`` 索引的容器，没有拓扑排序。
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.engine.signal import Signal
from app.engine.skill_loader import StrategySkill
from app.engine.strategy_runner import run_strategy


# ── 异常族 ──────────────────────────────────────────────────────────────
class StrategyError(Exception):
    """策略框架错误基类，便于调用方统一捕获。"""


class StrategyNotFoundError(StrategyError, KeyError):
    """按 ``skill_id`` 查找策略失败：注册表中不存在该 id。"""

    def __init__(self, skill_id: str) -> None:
        self.skill_id = skill_id
        super().__init__(f"未注册的策略：'{skill_id}'。")


class DuplicateStrategyError(StrategyError):
    """同一 ``skill_id`` 被重复注册。"""

    def __init__(self, skill_id: str) -> None:
        self.skill_id = skill_id
        super().__init__(f"策略 id 重复注册：'{skill_id}'。")


class StrategyAssemblyError(StrategyError):
    """``instantiate_strategy`` 装配命令式策略失败（任务 3.4，需求 4.1）。

    覆盖三类清晰可诊断的失败原因：``impl_class`` 格式不合法、模块 / 类找不到、
    类不满足 ``Strategy`` 协议（含实例化本身抛异常）。统一成一个异常类型，
    便于调用方（``build_registry_from_skills`` / 上层装配点）用一个 ``except``
    分支捕获所有装配期错误，而不必分别处理 ``ImportError``/``AttributeError``/
    ``TypeError``。
    """


# ── 调用上下文 ────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class StrategyContext:
    """一次 ``Strategy.evaluate`` 调用的只读上下文。

    字段与 ``run_strategy(skill, stock, main_industries, skip_universe_check)``
    现有调用点逐一对应：

    - ``main_industries``：判定 ``is_main_theme`` 需要的当日主线行业列表。
    - ``skip_universe_check``：是否跳过 universe 校验（prefilter 已放行时复用其
      结论，避免同一 stock 在 universe 与 prefilter 两处重复求值）。

    调用方（``run_workbench`` / 回测预检）对同一批 ``selected_skills`` 里的不同
    skill，``skip_universe_check`` 取值可能不同（取决于该 skill 是否命中
    prefilter 白名单），因此每次 ``evaluate`` 调用应携带一个按需构造的
    ``StrategyContext``，而不是把它绑死在 ``Strategy`` 实例上。
    """

    main_industries: list[str] = field(default_factory=list)
    skip_universe_check: bool = False


# ── Strategy 协议 ─────────────────────────────────────────────────────────
@runtime_checkable
class Strategy(Protocol):
    """一次 per-stock 策略评估的结构化契约（structural typing，实现类无需显式继承）。

    命令式（``ImperativeStrategy``，3.3）与声明式（``DeclarativeStrategy``，本任务）
    都实现同一个 ``evaluate`` 方法，返回同一个 typed ``Signal`` 契约——
    ``StrategyRegistry`` 与调用方无需区分策略来源（需求 4.4）。
    """

    def evaluate(self, stock: dict[str, Any], ctx: StrategyContext) -> Signal:
        ...


# ── DeclarativeStrategy：委托现有 run_strategy，YAML 路径零改动 ────────────────
class DeclarativeStrategy:
    """声明式（``strategy.yaml``）策略的 ``Strategy`` 协议实现。

    不重新实现任何评分 / 风控逻辑——只是把既有 ``run_strategy(skill, stock, ...)``
    包一层 ``Strategy`` 协议的壳，返回值原样透传（键集合、键序、每个字段的值均
    不变）。本类本身不产出新行为，只是「策略分发经 registry 统一取实例」这个
    seam 的声明式一侧实现。
    """

    __slots__ = ("skill",)

    def __init__(self, skill: StrategySkill) -> None:
        self.skill = skill

    @property
    def skill_id(self) -> str:
        return self.skill.skill_id

    def evaluate(self, stock: dict[str, Any], ctx: StrategyContext) -> Signal:
        return run_strategy(
            self.skill,
            stock,
            ctx.main_industries,
            skip_universe_check=ctx.skip_universe_check,
        )


# ── StrategyRegistry ──────────────────────────────────────────────────────
class StrategyRegistry:
    """进程内策略注册表：按 ``skill_id`` 注册 / 查找 / 列出策略实例。

    轻量、无外部依赖，可为每个用途 / 每次测试独立创建一个实例，互不污染
    （与 ``FactorRegistry`` 相同的用法约定，见 ``services/factors/registry.py``）。
    """

    def __init__(self) -> None:
        # dict 保持插入顺序，list_strategies()/ids() 按注册顺序输出，便于确定性遍历。
        self._strategies: dict[str, Strategy] = {}

    # ── 注册 ──────────────────────────────────────────────────────
    def register(self, skill_id: str, strategy: Strategy) -> Strategy:
        if not isinstance(skill_id, str) or not skill_id:
            raise StrategyError("策略必须绑定非空字符串 skill_id 才能注册。")
        if skill_id in self._strategies:
            raise DuplicateStrategyError(skill_id)
        if not callable(getattr(strategy, "evaluate", None)):
            raise StrategyError(f"策略 '{skill_id}' 必须实现可调用的 evaluate 方法。")
        self._strategies[skill_id] = strategy
        return strategy

    def unregister(self, skill_id: str) -> None:
        """移除一个已注册策略（不存在则忽略）。"""
        self._strategies.pop(skill_id, None)

    def clear(self) -> None:
        """清空注册表。"""
        self._strategies.clear()

    # ── 查询 ──────────────────────────────────────────────────────
    def get(self, skill_id: str) -> Strategy:
        try:
            return self._strategies[skill_id]
        except KeyError:
            raise StrategyNotFoundError(skill_id) from None

    def list_strategies(self) -> list[Strategy]:
        """按注册顺序列出全部策略实例。"""
        return list(self._strategies.values())

    def ids(self) -> tuple[str, ...]:
        """按注册顺序列出全部策略 id。"""
        return tuple(self._strategies)

    def __contains__(self, skill_id: object) -> bool:
        return skill_id in self._strategies

    def __len__(self) -> int:
        return len(self._strategies)


def instantiate_strategy(skill: StrategySkill) -> Strategy:
    """按 ``skill.manifest`` 是否声明 ``impl_class`` 装配对应的 ``Strategy`` 实现
    （任务 3.4，需求 4.1 / 4.2）。

    分派规则：
      - 声明了 ``impl_class``（格式 ``"module.path:ClassName"``）→ 动态 import
        该模块、取类、实例化，返回命令式实现（预期是 ``ImperativeStrategy`` 子类，
        但只做 structural typing 校验——满足 ``Strategy`` 协议即可，不强制
        ``isinstance(ImperativeStrategy)``，保持与协议本身「结构化而非继承」的
        设计一致）。
      - 未声明 → 返回 ``DeclarativeStrategy(skill)``（3.2 现状，零改动）。

    失败即报 ``StrategyAssemblyError``，三类原因均给出清晰、可定位到 ``skill_id`` /
    ``impl_class`` 字面值的错误信息：
      1. ``impl_class`` 格式不合法（缺冒号分隔的模块:类名）。
      2. 模块 import 失败（``ModuleNotFoundError``/``ImportError``）或类在模块内
         找不到（``AttributeError``）。
      3. 目标类实例化后不满足 ``Strategy`` 协议（缺 ``evaluate`` 方法），或类本身
         实例化过程抛异常（如缺 ``strategy_id``/``strategy_name`` 等必填类属性）。
    """
    impl_class = skill.impl_class
    if not impl_class:
        return DeclarativeStrategy(skill)

    if ":" not in impl_class:
        raise StrategyAssemblyError(
            f"策略 '{skill.skill_id}' 的 impl_class 格式不合法：'{impl_class}'，"
            "期望格式 'module.path:ClassName'。"
        )
    module_path, _, class_name = impl_class.partition(":")
    module_path = module_path.strip()
    class_name = class_name.strip()
    if not module_path or not class_name:
        raise StrategyAssemblyError(
            f"策略 '{skill.skill_id}' 的 impl_class 格式不合法：'{impl_class}'，"
            "模块路径与类名均不能为空。"
        )

    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise StrategyAssemblyError(
            f"策略 '{skill.skill_id}' 装配失败：无法 import 模块 '{module_path}'（impl_class='{impl_class}'）：{exc}"
        ) from exc

    try:
        strategy_cls = getattr(module, class_name)
    except AttributeError as exc:
        raise StrategyAssemblyError(
            f"策略 '{skill.skill_id}' 装配失败：模块 '{module_path}' 内找不到类 '{class_name}'（impl_class='{impl_class}'）。"
        ) from exc

    try:
        instance = strategy_cls()
    except Exception as exc:  # noqa: BLE001 - 需要把子类实例化失败的任何原因都归入统一的装配错误
        raise StrategyAssemblyError(
            f"策略 '{skill.skill_id}' 装配失败：实例化 '{impl_class}' 时抛出异常：{exc}"
        ) from exc

    if not isinstance(instance, Strategy):
        raise StrategyAssemblyError(
            f"策略 '{skill.skill_id}' 装配失败：'{impl_class}' 的实例不满足 Strategy 协议"
            "（缺可调用的 evaluate(stock, ctx) 方法）。"
        )
    return instance


def build_registry_from_skills(skills: list[StrategySkill]) -> StrategyRegistry:
    """从 ``load_skills(...)`` 结果批量构建并注册 ``Strategy`` 实例。

    3.2 时本函数只处理声明式路径：每个 skill 无条件包一层 ``DeclarativeStrategy``
    后注册。3.4 起改为调用 ``instantiate_strategy(skill)`` 决定具体实现——声明式
    skill（无 ``impl_class``）路径**零行为变化**（``instantiate_strategy`` 对这类
    skill 的返回值就是 ``DeclarativeStrategy(skill)``，与 3.2 逐字段一致，已有
    ``test_strategy_registry.py`` 等价对拍覆盖）；命令式 skill（声明了
    ``impl_class``）则装配为对应的命令式实现。调用方（``run_workbench`` / 回测
    预检）无需改动，仍是「拿到 skills 列表 → 建 registry →
    registry.get(id).evaluate(...)」这一套统一分发方式（需求 4.4）。
    """
    registry = StrategyRegistry()
    for skill in skills:
        registry.register(skill.skill_id, instantiate_strategy(skill))
    return registry
