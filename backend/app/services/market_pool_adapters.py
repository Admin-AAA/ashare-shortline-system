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
from app.services.market_data import (
    _fetch_kline,
    _fetch_quotes,
    _kline_sources,
    _quote_sources,
)


def quote_sources(settings: Any) -> list[str]:
    return _quote_sources(settings)


def kline_sources(settings: Any) -> list[str]:
    return _kline_sources(settings)


def fetch_quote_rows(source: str, settings: Any) -> list[dict[str, Any]]:
    return _fetch_quotes(source, settings)


def fetch_kline_rows(source: str, quote: dict[str, Any], settings: Any, start_date: str | None = None) -> list[dict[str, Any]]:
    return _fetch_kline(source, quote, settings, start_date=start_date)


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
