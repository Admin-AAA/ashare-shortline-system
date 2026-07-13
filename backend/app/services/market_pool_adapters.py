"""market_pool 依赖的采集/辅助数据入口（供 `market_pool.py` 引用与
`test_market_pool.py` 经 port 注入替身）。

## 任务 4.2 变更说明
本文件对外暴露的函数名/签名保持不变（`quote_sources`/`kline_sources`/
`fetch_quote_rows`/`fetch_kline_rows`），供 `market_pool.py` 的
`_quote_sources = pool_adapters.quote_sources` 等别名绑定不失效——**变的是
内部实现**：`fetch_quote_rows`/`fetch_kline_rows`（真正发起采集调用的两个
函数）改为经 `MarketSourcePort` 实现调用，不再直接调用
`market_data._fetch_quotes`/`_fetch_kline`。

`quote_sources`/`kline_sources` 保持直接调用 `market_data._quote_sources`/
`_kline_sources`——这两个函数只是返回一份硬编码的源名单
（`["tushare"]`），不发起任何采集调用，`MarketSourcePort` 协议里也没有
"列出源名单"这个方法（port 只覆盖 `fetch_quotes`/`fetch_daily_bars` 两件
事，见 `market_source/port.py`），所以它们不在本次"改为经 port 调用"的
范围内，照旧转发。

辅助数据（limit_up/concept_sector/capital_flow/dragon_tiger/news）不进
`MarketSourcePort`（设计 §6 决议），本文件里对应的函数保持现状不动。

## 任务 4.4：adapter 装配点注入（依赖注入 seam）

4.2 落地时 `fetch_quote_rows`/`fetch_kline_rows` 直接在函数体内 `new` 了一个
`TushareMarketSourceAdapter(settings)`——这个 adapter 实例不是全局单例、也
不可被外部替换，导致 `test_market_pool.py` 里想验证"报价/日K采集"行为的
测试，只能 monkeypatch `market_pool._fetch_quotes`/`_fetch_kline`（这两个
私有函数别名本身），而不是"换一个 port 实现"——这正是需求 8.1 明确禁止的
"monkeypatch 内部私有函数"。

本任务新增 `_get_market_source_adapter(settings)` 作为唯一的 adapter 装配点
（factory），`fetch_quote_rows`/`fetch_kline_rows` 改为调用它而不是直接构造
`TushareMarketSourceAdapter`；再新增 `set_market_source_adapter_factory` 作为
显式的注入/复位入口。测试经 `set_market_source_adapter_factory` 换成一个
返回 `InMemoryMarketSourceAdapter` 的工厂，调用方（`market_pool.py` 的
`_fetch_quotes`/`_fetch_kline` 别名、以及它们内部真正的采集逻辑）完全不知道
底层换了哪个 adapter——被替换的是"要用哪个 port 实现"这一个装配点，不是
"采集函数具体怎么做"，符合 ports & adapters 的注入方向。

没有采用"给 `market_pool.py` 的函数加 `adapter: MarketSourcePort | None = None`
参数"（设计文档里的备选方向 B）：那需要改 `refresh_quote_pool`/
`_load_or_refresh_quotes`/`_load_or_refresh_bars` 等多个函数签名及其调用链
（`get_market_universe_from_pool` 等 15+ 处上游调用点），改动面远大于在唯一
的采集装配处（本文件）开一个工厂注入口；而 `market_pool.py` 本就是通过
`_fetch_quotes = pool_adapters.fetch_quote_rows` 这层别名与本文件解耦，装配
点收在本文件是最小改动、且与现有分层一致。
"""
from __future__ import annotations

from typing import Any, Callable

from app.services.market_auxiliary import (
    _capital_flow_sources,
    _concept_sector_sources,
    _dragon_tiger_sources,
    _fetch_capital_flow,
    _fetch_concept_sectors,
    _fetch_dragon_tiger,
    _fetch_limit_up_board,
    _fetch_news,
    _fetch_tushare_moneyflow_batch,
    _limit_up_sources,
    _news_sources,
)
from app.services.market_data import MarketDataError, _kline_sources, _quote_sources
from app.services.market_source.multi_source_adapter import TushareMarketSourceAdapter
from app.services.market_source.port import MarketSourcePort


def quote_sources(settings: Any) -> list[str]:
    return _quote_sources(settings)


def kline_sources(settings: Any) -> list[str]:
    return _kline_sources(settings)


# adapter 装配点的注入工厂：`None` 表示使用默认生产实现（`TushareMarketSourceAdapter`）。
_adapter_factory: Callable[[Any], MarketSourcePort] | None = None


def set_market_source_adapter_factory(factory: Callable[[Any], MarketSourcePort] | None) -> None:
    """注入/复位 `MarketSourcePort` 装配点（任务 4.4 的唯一注入入口）。

    `factory` 接收 `settings`、返回一个 `MarketSourcePort` 实现；传 `None`
    恢复默认（每次调用惰性构造 `TushareMarketSourceAdapter(settings)`）。
    生产代码路径不会调用本函数——它是为测试准备的 seam，供
    `test_market_pool.py` 换入 `InMemoryMarketSourceAdapter`，而不是
    monkeypatch `fetch_quote_rows`/`fetch_kline_rows` 或 `market_pool` 里的
    `_fetch_quotes`/`_fetch_kline` 别名本体。
    """
    global _adapter_factory
    _adapter_factory = factory


def _get_market_source_adapter(settings: Any) -> MarketSourcePort:
    """返回当前生效的 `MarketSourcePort` 实现：默认惰性构造生产 adapter，
    经 `set_market_source_adapter_factory` 注入后返回替身 adapter。
    """
    if _adapter_factory is not None:
        return _adapter_factory(settings)
    return TushareMarketSourceAdapter(settings)


def fetch_quote_rows(source: str, settings: Any) -> list[dict[str, Any]]:
    """经 `MarketSourcePort` 装配点拉取一次报价快照。

    保留现状 `market_data._fetch_quotes` 的分支语义——只认识 `"tushare"`，
    其它 `source` 值抛 `MarketDataError`（不去伪造"port 已经多源"）。
    """
    if source == "tushare":
        return _get_market_source_adapter(settings).fetch_quotes()
    raise MarketDataError(f"未知行情源: {source}")


def fetch_kline_rows(source: str, quote: dict[str, Any], settings: Any, start_date: str | None = None) -> list[dict[str, Any]]:
    """经 `MarketSourcePort` 装配点拉取单只股票日K。

    保留现状 `market_data._fetch_kline` 的怪异行为——完全忽略 `source`
    参数，始终委托 tushare 路径（详见 `multi_source_adapter.py` 里
    `fetch_bars_for_quote` 的 docstring；不是本任务该修的"bug"，是如实
    迁移现状）。用 `fetch_bars_for_quote`（非 port 协议方法，见
    `multi_source_adapter.py`/`in_memory_adapter.py` 的兼容出口说明）而不是
    port 协议方法 `fetch_daily_bars`，因为调用方（`market_pool.
    _refresh_bars_for_quote`）传入的是完整 quote 字典（带 `turnover_rate`
    兜底信息），降级成 `fetch_daily_bars(codes, ...)` 只认 code 会丢失这层
    兜底行为。
    """
    return _get_market_source_adapter(settings).fetch_bars_for_quote(quote, start_date=start_date)


def limit_up_sources(settings: Any) -> list[str]:
    return _limit_up_sources(settings)


def concept_sector_sources(settings: Any) -> list[str]:
    return _concept_sector_sources(settings)


def capital_flow_sources(settings: Any) -> list[str]:
    return _capital_flow_sources(settings)


def dragon_tiger_sources(settings: Any) -> list[str]:
    return _dragon_tiger_sources(settings)


def news_sources(settings: Any) -> list[str]:
    return _news_sources(settings)


def fetch_limit_up_board_rows(source: str, settings: Any, trade_date: str | None = None) -> list[dict[str, Any]]:
    return _fetch_limit_up_board(source, settings, trade_date=trade_date)


def fetch_concept_sector_rows(source: str, settings: Any, trade_date: str | None = None) -> list[dict[str, Any]]:
    return _fetch_concept_sectors(source, settings, trade_date=trade_date)


def fetch_capital_flow_rows(source: str, code: str, settings: Any, trade_date: str | None = None) -> dict[str, Any]:
    return _fetch_capital_flow(source, code, settings, trade_date=trade_date)


def fetch_capital_flow_batch(source: str, codes: list[str], settings: Any, trade_date: str | None = None) -> dict[str, dict[str, Any]]:
    if source == "tushare":
        return _fetch_tushare_moneyflow_batch(codes, settings, trade_date=trade_date)
    return {}


def fetch_dragon_tiger_rows(source: str, settings: Any, trade_date: str | None = None) -> list[dict[str, Any]]:
    return _fetch_dragon_tiger(source, settings, trade_date=trade_date)


def fetch_news_rows(source: str, code: str, settings: Any, trade_date: str | None = None) -> list[dict[str, Any]]:
    return _fetch_news(source, code, settings, trade_date=trade_date)
