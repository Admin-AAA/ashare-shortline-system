"""行情采集 port：`MarketSourcePort` 协议定义（任务 4.1，仅接口，不含实现）。

## 边界（重要）
本文件只定义**接口**。生产实现（迁入 `_fetch_tushare_quotes` / `_fetch_tushare_kline`
等真实调用与未来可能的多源回退）与测试用的内存实现，是任务 4.2 的范围；
`market_pool_adapters.py` 改为指向本 port 同样是 4.2 的范围。本文件落地后，
`market_data.py` / `market_pool_adapters.py` 未做任何改动。

## 精读 market_data.py 后的真实情况核实（不为迎合设计文档编造多源）

**1. 源清单：现状是单源，不是多源。**
`_quote_sources(settings)` 与 `_kline_sources(settings)` 当前都硬编码
`return ["tushare"]`（`market_data.py` L1080-1084），与传入的 `settings` 参数
无关——即便 `Settings.quote_sources` / `kline_sources` 配置项（`core/config.py`，
默认值 `"tushare"`）和 README 里罗列的 `thsdk,eastmoney,akshare,tencent,sina`
候选列表，都**没有被这两个函数读取**。`_fetch_quotes(source, settings)` /
`_fetch_kline(source, quote, settings, ...)` 内部也是 `if source == "tushare":
... else: raise MarketDataError(f"未知行情源: {source}")`——只认识
`"tushare"` 一个分支。也就是说：
- 设计文档 §6 说的"多源+回退顺序藏在 implementation"，在当前代码里**没有
  对应的现状**；那是对 4.2 落地方向的预留描述，不是对现状的准确刻画。
- 唯一的"多源"痕迹在**辅助数据**（`market_auxiliary.py` 的
  limit_up/concept_sector/capital_flow/dragon_tiger/news，`news_sources`
  默认 `"tushare,eastmoney"`）——但辅助数据明确**不进本 port**（设计 §6：
  "辅助数据不进此 port"），本任务不覆盖。
- 报价还有一层"实时 vs 批量日线"的路径切换（见下），但那不是"多源"，是
  同一 tushare 源内部的两种取数方式，由交易日历状态（盘中/收盘）决定。

**2. `_fetch_first_kline` 的回退策略：只有一次尝试，没有真正的"下一个源"。**
`_fetch_first_kline(quote, sources, settings, disabled_sources=None)`
（L316-330）签名上保留了 `sources: list[str]` 参数（为将来多源回退让出位置），
但函数体只 `try: bars = _fetch_kline("tushare", quote, settings)`——**硬编码
调用 `"tushare"`，完全忽略传入的 `sources` 列表**。失败路径：
- 拉取到但 `len(bars) < 30`（不足 30 条日K）→ 记 `"tushare: 日K不足30条"`
  错误，返回 `([], "", errors)`，不重试、不换源。
- 拉取抛异常 → 记 `f"tushare: {exc}"`，同样返回 `([], "", errors)`。
上层 `get_market_universe` 对此的处理不是"换源"，而是"换股票"：先查本地
`daily_kbars` 缓存批量表（`_load_cached_bars_batch`，≥30 条才用），缓存不足
才回退到这里逐股票调 Tushare；这只是**候选池粒度的降级**（该股票拿不到
K线就跳过、换下一个候选），不是 `_fetch_first_kline` 内部有多源回退。
报价侧类似：`_fetch_tushare_quotes` 内部有"实时行情→批量日线（bak_daily→
daily，回溯最近 5 个交易日）"这一条**同源内部**的多层兜底（L662-730 附近），
本 port 不需要暴露这层细节——那正是设计 §6 说的"藏在 implementation 内"，
只是目前藏的是"同源多路径"而非"多源"。

**3. `_build_stock_from_bars` 依赖的采集结果字段（供 4.2/4.3 参考）。**
签名：`_build_stock_from_bars(quote: dict, bars: list[dict], quote_source: str,
kline_source: str) -> dict`（`market_data.py` L1088 起）。
- 从 `quote`（单条报价字典）读取：`code`/`name`/`exchange`/`industry`/
  `price`/`pct`/`amount_yi`/`turnover_rate`/`free_mv_yi`/`total_mv_yi`/
  `pe_ttm`（经 market_data_gaps 检查）/`main_net_inflow_yi`/`is_st`/
  `quote_time`；`price` 缺失时 fallback 到最新 bar 的 `close`。
- 从 `bars`（该股票的日K列表，每条含 `trade_date`/`open`/`high`/`low`/
  `close`/`volume`/`amount`/`pct_chg`/`turnover_rate`，见
  `_fetch_tushare_kline` 的返回形状）计算 MA/BIAS/RSI/MACD/回撤/位置/量比
  等衍生指标；若 bar 上带有本地缓存写入的 `ma5`/`ma10`/`ma20`/`ma60`/
  `volume_ratio_5d`/`_indicator`（内含 `bias_ma5/10/20`/`rsi_6/12/24`/
  `macd_dif`/`macd_dea`/`upper_shadow_count20`/`max_dd20`/`max_dd60`）、
  以及筹码字段 `chip_cost_85`/`chip_cost_15`/`chip_winner_rate`，则优先复用
  缓存值，缺失才现算——这些是"本地缓存旁路"（`market_pool._load_cached_bars*`
  写入），**不是** port 需要产出的字段，port 的 `fetch_daily_bars` 只需给出
  `_fetch_tushare_kline` 那一层的原始 OHLCV+turnover 字段即可，指标缓存旁路
  仍留在 `market_pool` 侧（S5 范围，非本 port 关注点）。

## Port 接口设计与方法签名理由

- `fetch_quotes(scope) -> list[dict]`：对应 `_fetch_quotes("tushare", settings)`
  的真实调用形状——**没有"只拉这些候选"的 source-level 缩域能力**；
  `_fetch_first_candidate_quotes` 拿到的也是全市场报价，候选筛选
  （`_select_recall_candidates` 等）在 market_data 业务层做，发生在
  `fetch_quotes` 返回之后。因此这里的 `scope` 如实地只承载一个当前真正
  存在的缩域旋钮——`market_scan_size`（仅在盘中 realtime 路径生效，
  `_fetch_tushare_quotes` 末尾 `return realtime_quotes[: settings.market_scan_size]`），
  而不是伪造一个"按 code 过滤"的参数。`scope=None` 表示不限制（当前
  批量日线兜底路径本就不做数量截断）。
- `fetch_daily_bars(codes, date_range) -> dict[str, list[dict]]`：现状
  `_fetch_kline`/`_fetch_tushare_kline` 是**逐股票**调用（`quote: dict` 单条，
  内部 `ts_code = f"{quote['code']}.{quote['exchange']}"`）；但
  `exchange` 完全可由 `code` 用 `_exchange_from_code` 推出（`market_data.py`
  L1536-1541，按前缀规则），不需要调用方传整条 quote。所以 port 把粒度
  提升为"批量 codes → {code: bars}"的字典契约，比逐股票更贴近
  `market_pool` 侧真实的批量需要（历史 K 线批量缓存 `_load_cached_bars_batch`
  已经是批量思路），而具体是否内部仍逐只循环调用 tushare，是 4.2 
  `multi_source_adapter` 的实现细节，port 不关心。
- 两个方法都**不接收 `settings`/`source` 参数**：现有函数把 `settings`
  在每次调用时当参数传入（`_fetch_quotes(source, settings)`），这是因为
  `market_data.py` 里没有"adapter 实例"概念、函数是纯粹的模块级函数。
  但 ports & adapters 的目标形态是"adapter 在构造时注入配置/客户端"
  （生产 adapter 持有 tushare client + 由 settings 派生的默认窗口/限流参数，
  in-memory adapter 持有测试夹具数据），port 协议方法只暴露领域相关的
  查询参数（scope / codes / date_range），配置细节收进 adapter 构造函数——
  这是把"要不要限速""市场扫描规模默认值"等 cross-cutting 配置，从每次
  调用的参数面里挪到 adapter 装配时刻，符合"port 窄接口、adapter 深实现"
  的目标（4.2 落地时据此判断是否需要微调，设计 §6 已标注【待核实】）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence, runtime_checkable


@dataclass(frozen=True)
class QuoteScope:
    """一次 `fetch_quotes` 调用的可选缩域提示。

    如实说明：现状 tushare 单源实现里，报价拉取没有"只拉这些候选股票"的
    source-level 能力——`_fetch_quotes` 每次都是全市场遍历
    （`_fetch_tushare_quotes` 走 `stock_basics` 全量）。目前唯一真实存在的
    缩域旋钮是 `max_quote_count`，对应 `settings.market_scan_size`，且只在
    盘中 realtime 报价路径生效（`_fetch_tushare_quotes` 末尾对
    `realtime_quotes` 做切片；收盘后走批量日线的分支不做此截断）。
    不要在这里加"候选 code 列表"之类的字段——那不是本 port 该抽象的东西，
    候选筛选是 market_data 业务层职责（4.3 `candidates/screening`）。
    """

    max_quote_count: int | None = None


@dataclass(frozen=True)
class DateRange:
    """一次 `fetch_daily_bars` 调用的日期区间。

    对应 `_fetch_tushare_kline(quote, settings, start_date=None, end_date=None)`
    的 `start_date`/`end_date` 参数（格式均为 `YYYYMMDD` 字符串）。两者都允许
    为 `None`：`start_date=None` 时现状实现会按
    `settings.market_kline_days * 3` 天回溯（保证滑窗计算指标有足够样本，
    再在结果里 `tail(market_kline_days)` 截断）；`end_date=None` 时现状用
    当天日期。这两个"按 settings 派生默认值"的行为，在 port 形态下应收进
    adapter 构造时的配置，而不是让调用方每次都传 `None` 去触发——本 dataclass
    仅表达调用方**明确要的**区间，留空即表示"由 adapter 决定默认区间"。
    """

    start_date: str | None = None
    end_date: str | None = None


@runtime_checkable
class MarketSourcePort(Protocol):
    """行情采集 port：只覆盖报价快照与逐股日K两件事。

    辅助数据（涨停板/概念板块/资金流/龙虎榜/新闻，见 `market_auxiliary.py`）
    不进此 port（设计 §6 决议），保持这个 port 窄——只服务
    `get_market_universe`/`get_market_snapshot` 真正需要采集的两类数据。
    """

    def fetch_quotes(self, scope: QuoteScope | None = None) -> list[dict[str, Any]]:
        """拉取一次全市场报价快照。

        对应现状 `_fetch_quotes("tushare", settings)` 的返回形状：每条报价字典
        至少含 `code`/`name`/`exchange`/`industry`/`price`/`pct`/`amount_yi`/
        `turnover_rate`/`pe_ttm`/`total_mv_yi`/`free_mv_yi`/`is_st`/
        `trade_date`/`quote_time`（见 `market_data._quote()` 的字段面）。
        `scope=None` 等价于不限制条数（不做任何截断）。
        """
        ...

    def fetch_daily_bars(
        self, codes: Sequence[str], date_range: DateRange | None = None
    ) -> dict[str, list[dict[str, Any]]]:
        """批量拉取一组股票代码各自的日K序列。

        返回 `{code: bars}`；某只股票取不到数据时，对应 value 应为空列表
        `[]`（现状 `_fetch_first_kline` 拉取失败/不足30条时的行为），而不是
        整体抛异常中断其它股票——保持与现状"逐股票独立降级"一致的语义。
        每条 bar 字典字段对应 `_fetch_tushare_kline` 的返回形状：`code`/
        `trade_date`/`open`/`high`/`low`/`close`/`volume`/`amount`/
        `pct_chg`/`turnover_rate`。`date_range=None` 时由 adapter 按自身
        默认配置回溯（详见 `DateRange` docstring）。
        """
        ...
