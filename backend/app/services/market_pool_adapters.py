"""Typed market-source composition seam for the local market pool.

Quote and daily-bar collection are exposed as typed batches.  Provider source
selection remains local to this boundary; the market-pool core consumes request
DTOs, batch results, and their audits without calling adapter compatibility
helpers.  Auxiliary-data functions below are intentionally unchanged.
"""
from __future__ import annotations

from typing import Any, Callable

from app.adapters.market_source import XiaoshiMarketSourceAdapter, TushareMarketSourceAdapter
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
from app.services.market_source.port import (
    DailyBarBatch,
    DailyBarRequest,
    MarketSourcePort,
    QuoteBatch,
    QuoteRequest,
)


def quote_sources(settings: Any) -> list[str]:
    return _quote_sources(settings)


def kline_sources(settings: Any) -> list[str]:
    return _kline_sources(settings)


# ``None`` selects the default production adapter.  Tests replace this one
# composition seam with a Port-only fake rather than patching collection logic.
_adapter_factory: Callable[[Any], MarketSourcePort] | None = None


def set_market_source_adapter_factory(factory: Callable[[Any], MarketSourcePort] | None) -> None:
    """Inject or reset the sole ``MarketSourcePort`` composition seam."""

    global _adapter_factory
    _adapter_factory = factory


def _get_market_source_adapter(settings: Any) -> MarketSourcePort:
    if _adapter_factory is not None:
        return _adapter_factory(settings)
    return TushareMarketSourceAdapter(settings)


def fetch_quote_batch(source: str, settings: Any, request: QuoteRequest) -> QuoteBatch:
    """Fetch a typed quote batch from the configured provider implementation."""

    if source == "tushare":
        return _get_market_source_adapter(settings).fetch_quotes(request)
    if source == "xiaoshi":
        return XiaoshiMarketSourceAdapter(settings=settings).fetch_quotes(request)
    raise MarketDataError(f"未知行情源: {source}")


def fetch_daily_bar_batch(
    source: str,
    settings: Any,
    request: DailyBarRequest,
) -> DailyBarBatch:
    """Fetch typed daily bars from the configured provider implementation."""

    if source == "tushare":
        return _get_market_source_adapter(settings).fetch_daily_bars(request)
    if source == "xiaoshi":
        return XiaoshiMarketSourceAdapter(settings=settings).fetch_daily_bars(request)
    raise MarketDataError(f"未知行情源: {source}")


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
