"""market_pool 依赖的采集/辅助数据入口（供 `market_pool.py` 引用与
`test_market_pool.py` monkeypatch）。

## 任务 4.2 变更说明
本文件对外暴露的函数名/签名保持不变（`quote_sources`/`kline_sources`/
`fetch_quote_rows`/`fetch_kline_rows`），供 `market_pool.py` 的
`_quote_sources = pool_adapters.quote_sources` 等别名绑定与
`test_market_pool.py` 的 monkeypatch 目标不失效——**变的是内部实现**：
`fetch_quote_rows`/`fetch_kline_rows`（真正发起采集调用的两个函数）改为
经 `market_source.multi_source_adapter.TushareMarketSourceAdapter`（4.1/4.2
定义的 `MarketSourcePort` 生产实现）调用，不再直接调用
`market_data._fetch_quotes`/`_fetch_kline`。

`quote_sources`/`kline_sources` 保持直接调用 `market_data._quote_sources`/
`_kline_sources`——这两个函数只是返回一份硬编码的源名单
（`["tushare"]`），不发起任何采集调用，`MarketSourcePort` 协议里也没有
"列出源名单"这个方法（port 只覆盖 `fetch_quotes`/`fetch_daily_bars` 两件
事，见 `market_source/port.py`），所以它们不在本次"改为经 port 调用"的
范围内，照旧转发。

辅助数据（limit_up/concept_sector/capital_flow/dragon_tiger/news）不进
`MarketSourcePort`（设计 §6 决议），本文件里对应的函数保持现状不动。
"""
from __future__ import annotations

from typing import Any

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


def quote_sources(settings: Any) -> list[str]:
    return _quote_sources(settings)


def kline_sources(settings: Any) -> list[str]:
    return _kline_sources(settings)


def fetch_quote_rows(source: str, settings: Any) -> list[dict[str, Any]]:
    """经 `MarketSourcePort` 生产实现拉取一次报价快照。

    保留现状 `market_data._fetch_quotes` 的分支语义——只认识 `"tushare"`，
    其它 `source` 值抛 `MarketDataError`（不去伪造"port 已经多源"）。
    """
    if source == "tushare":
        return TushareMarketSourceAdapter(settings).fetch_quotes()
    raise MarketDataError(f"未知行情源: {source}")


def fetch_kline_rows(source: str, quote: dict[str, Any], settings: Any, start_date: str | None = None) -> list[dict[str, Any]]:
    """经 `MarketSourcePort` 生产实现拉取单只股票日K。

    保留现状 `market_data._fetch_kline` 的怪异行为——完全忽略 `source`
    参数，始终委托 tushare 路径（详见 `multi_source_adapter.py` 里
    `fetch_bars_for_quote` 的 docstring；不是本任务该修的"bug"，是如实
    迁移现状）。
    """
    return TushareMarketSourceAdapter(settings).fetch_bars_for_quote(quote, start_date=start_date)


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
