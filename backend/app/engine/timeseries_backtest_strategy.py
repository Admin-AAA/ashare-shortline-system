"""时间序列回测策略协议（``TimeSeriesBacktestStrategy``，任务 8.2，需求 9.2）。

落点说明——协议放在 ``engine/``，具体实现放在各自的 service 模块：
    与 ``strategy.py``（形状一 ``Strategy`` 协议）的落点决策一致：``engine/`` 是
    「策略评估原语」的深模块聚集地，协议本身不携带任何业务逻辑，只是给调用方一个
    统一的「怎么拿到一个可 ``run`` 的时间序列回测策略对象」的 seam。但与形状一不同，
    形状二（时间序列回测）目前只有 overnight-holding 一个具体实现，其内部数据流
    （分钟线/基准指数缓存/日K窗口）高度绑定 ``overnight_holding_service.py`` 自身，
    不是可复用的评估原语——所以具体实现类（``OvernightHoldingBacktestStrategy``）
    仍留在 ``overnight_holding_service.py`` 里，与它委托的私有函数
    （``_simulate_variant``/``_choose_variant``/``OvernightVariant``）保持 locality，
    不搬进 ``engine/``。

设计取舍（design §4.2/§10、需求 9.2：「对齐 Strategy 协议方向」——方向性对齐，
不要求与形状一逐字段同构）：
    - 形状一的 ``Strategy.evaluate(stock, ctx) -> Signal`` 是「同一个策略实例，
      被反复调用、每次评估一只股票」；形状二的调用形状完全不同——「同一个策略，
      针对一个日期区间，一次调用做完整套时间序列回测（grid search 多个参数变体 +
      选优 + 汇总报告）」。协议因此不追求方法签名与形状一同构，只追求同一种
      「协议 + 委托适配器」手法：把现有命令式函数包一层结构化契约，不重写任何
      业务逻辑（本任务的核心约束——见 design §10「命令式回测策略在此与 §4.2
      协议对齐」，以及需求 9.2「对齐...协议方向」的措辞本身就是方向性的，不是
      「实现同一协议」）。
    - ``run(db, start_date, end_date) -> dict[str, Any]``：形状二家族
      （``run_overnight_holding_backtest`` / ``run_backtest`` / ``run_stock_backtest``）
      共享的真实调用形状——db + 起止日期 → 完整结果 dict。协议只固化这一条，
      是三者已经在用、无需发明的公共面。
    - ``variants() -> Sequence[BacktestVariant]``：overnight-holding 内部用
      ``(BASELINE_VARIANT, CANDIDATE_VARIANT, DELAYED_EXIT_VARIANT, RELAXED_PCT_VARIANT)``
      做参数网格搜索，这是「形状二」区别于纯粹单次回测的特征性结构（另见
      ``backtest_validation._choose_backtest_variant`` 的 baseline vs candidate
      两变体对比，同一模式的另一处例证）。``BacktestVariant`` 只要求
      ``name``/``label`` 两个字段（structural typing）——这是 ``_choose_variant``/
      ``_build_metrics`` 实际读取的字段集合，不多要一个。variant 自身其余的
      调参字段（如 ``OvernightVariant.min_pct``/``exit_time``）是该策略私有的
      调参维度，不进协议。
    - ``choose_variant(signal_dates, comparisons) -> tuple[str, str]``：
      ``_choose_variant`` 本身是纯函数（只读 ``comparisons`` 里已经算好的
      ``split_metrics``/``walk_forward``，不碰 db/分钟线缓存），是「策略特有的
      选优策略」这一决策点的真实边界，故提升为协议方法，与形状一
      ``ImperativeStrategy.score()``「子类只写新逻辑，其余交给基类/驱动」的
      精神一致。
    - **不把 ``simulate_variant`` 提升为协议方法**：overnight-holding 的
      ``_simulate_variant`` 需要一整套跨变体共享、只加载一次的上下文
      （``daily_by_date``/``history_by_code``/``minute_cache``/``benchmark_cache``
      等，见 ``run_overnight_holding_backtest`` 内部——这些数据对 4 个变体只加载
      一次，循环内复用，避免 4 倍数据库往返）。若把 ``simulate_variant`` 做成
      「只传 variant 就能跑」的协议方法，要么强迫适配器每次重新加载整套上下文
      （产生与真实 ``run()`` 路径不同的性能特征，成为一条平行但更慢的影子路径），
      要么发明一个「跨变体共享上下文」的通用类型——本任务只有一个形状二实现，
      没有第二个实例可验证这个类型是否设计对，属于 design 明确要避免的
      「过度抽象」。故协议维持在 ``run``/``variants``/``choose_variant`` 三个已被
      现有代码验证过是「真实、独立、可复用」的边界上，不强行统一
      ``simulate_variant``。
    - **不做 ``TimeSeriesBacktestStrategyRegistry``**：目前只有 overnight-holding
      一个形状二实现，调用方（当前无编排层统一分发形状二策略，overnight/backtest/
      stock_backtest 三个入口各自被 API 层直接调用）没有「按 id 查找形状二策略」
      的真实需求。注册表价值需要至少两个具体实现才能验证「按 id 索引」是否是
      对的抽象边界——留给未来出现第二个形状二策略时再做（design §15 决议 2E 的
      同一条理由：「Speculative 阶段应该避免过度设计」）。
"""
from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence, TypedDict, runtime_checkable

from sqlalchemy.orm import Session


@runtime_checkable
class BacktestVariant(Protocol):
    """一个时间序列回测的参数变体（structural typing）。

    只要求 ``_choose_variant``/``_build_metrics`` 实际读取的两个字段——
    ``name``（如 ``"baseline"``，用于按 key 索引 ``comparisons``）与
    ``label``（人类可读展示名，用于选优理由文案）。变体自身的调参字段
    （如 ``OvernightVariant.min_pct``/``exit_time``）是策略私有的，不在此协议中。
    """

    name: str
    label: str


class BacktestVariantResult(TypedDict):
    """单个变体一次完整模拟的产出契约。

    字段与 ``overnight_holding_service._simulate_variant`` 的返回值逐一对应
    （见该函数末尾的 dict 字面量），是当前唯一形状二实现已经在用、经受过
    ``_choose_variant``/``_build_metrics``/响应组装消费验证的真实契约，不是
    臆造字段。
    """

    variant: BacktestVariant
    metrics: dict[str, Any]
    split_metrics: dict[str, Any]
    walk_forward: dict[str, Any]
    date_summaries: list[dict[str, Any]]
    trades: list[dict[str, Any]]
    trade_samples: list[dict[str, Any]]
    false_positive_samples: list[dict[str, Any]]
    notes: list[str]


@runtime_checkable
class TimeSeriesBacktestStrategy(Protocol):
    """一次完整时间序列回测（形状二）的结构化契约（structural typing）。

    与形状一 ``Strategy`` 协议同为「协议 + 委托适配器」手法，但方法签名反映
    形状二自身的真实调用形状（db + 起止日期 → 完整结果 dict；参数网格搜索 +
    选优），不强行与形状一同构（需求 9.2 是方向性对齐，见模块顶部落点说明）。
    """

    strategy_id: str
    strategy_name: str

    def variants(self) -> Sequence[BacktestVariant]:
        """本策略参与网格搜索/对比的全部参数变体，按既定顺序返回。"""
        ...

    def run(self, db: Session, start_date: str, end_date: str) -> dict[str, Any]:
        """对 ``[start_date, end_date]`` 区间执行一次完整时间序列回测，返回汇总结果。"""
        ...

    def choose_variant(
        self,
        signal_dates: list[str],
        comparisons: Mapping[str, BacktestVariantResult],
    ) -> tuple[str, str]:
        """从 ``run()`` 内部产出的各变体对比结果中选出采用的变体及理由。"""
        ...
