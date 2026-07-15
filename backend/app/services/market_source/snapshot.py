"""行情快照构建（任务 4.3）：市场快照/个股集合快照/大盘广度/行业排行。

## 迁移说明
本文件是从 `market_data.py` 原地搬迁的**权威实现**（module extraction，
不改变任何函数逻辑/参数/返回值）：`_apply_relative_features` /
`_build_quote_snapshot` / `_build_snapshot` / `_rank_industries` /
`_top_industries_from_quotes` / `_breadth_from_counts` /
`_latest_trade_date_from_quotes`。`market_data.py` 改为从本模块 re-export
这些名字（保持 `from app.services.market_data import _build_quote_snapshot`
等既有导入路径不失效），不再保留重复实现。

这些函数与"采集"（拉取报价/K线）无关——它们只是对**已经拿到的**报价/个股
数据做汇总统计（大盘涨跌家数、行业热度排行、broaden 市场情绪档位）与结构化
包装（snapshot dict 的字段面），因此按设计 §6 归入 `market_source/snapshot`，
移出 market_data 的采集职责范围。

## 避免循环 import 的说明
`_latest_trade_date_from_quotes` 需要 `_format_yyyymmdd`（单一权威实现仍在
`market_data.py`，因为它被大量采集函数直接使用，不属于"快照构建"职责，
不随本次搬迁移动）。为避免 `market_source.snapshot` 与 `market_data` 之间
出现模块级双向 import 循环（`market_data` 需要从本模块 re-export
`_build_quote_snapshot` 等名字），这里对 `_format_yyyymmdd` 使用**函数内
延迟 import**，只在调用时才解析，不在模块加载时建立循环依赖。
"""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from typing import Any

from app.engine.trading_calendar import latest_allowed_bar_date as _latest_allowed_bar_date


def _apply_relative_features(stocks: list[dict[str, Any]]) -> None:
    if not stocks:
        return
    market_chg20 = statistics.fmean(float(item.get("chg20") or 0) for item in stocks)
    market_chg60 = statistics.fmean(float(item.get("chg60") or 0) for item in stocks)
    industry_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for stock in stocks:
        industry_rows[str(stock.get("industry") or "未分类")].append(stock)
    ranked_industries = _rank_industries(stocks)
    main_industries = {item for item, _score in ranked_industries[:3] if item != "未分类"}
    for stock in stocks:
        peers = industry_rows[str(stock.get("industry") or "未分类")]
        stock["primary_excess20"] = round(float(stock.get("chg20") or 0) - market_chg20, 3)
        stock["primary_excess60"] = round(float(stock.get("chg60") or 0) - market_chg60, 3)
        stock["industry_capacity_like_count"] = sum(1 for peer in peers if float(peer.get("amount_yi") or 0) >= 3)
        stock["industry_positive_excess20_count"] = sum(1 for peer in peers if float(peer.get("chg20") or 0) > market_chg20)
        stock["is_main_theme"] = stock.get("industry") in main_industries and stock.get("industry") != "未分类"
        profile = dict(stock.get("dsa_profile") or {})
        if stock["is_main_theme"]:
            profile["sector_fund_rank"] = min(8, profile.get("sector_fund_rank", 99))
        stock["dsa_profile"] = profile


def _build_quote_snapshot(
    quotes: list[dict[str, Any]],
    data_source: str,
    quote_source: str,
    quote_errors: list[str],
) -> dict[str, Any]:
    trade_date = _latest_trade_date_from_quotes(quotes)
    up_count = sum(1 for item in quotes if item.get("pct", 0) > 0)
    down_count = sum(1 for item in quotes if item.get("pct", 0) < 0)
    limit_up_count = sum(1 for item in quotes if item.get("pct", 0) >= 9.8)
    limit_down_count = sum(1 for item in quotes if item.get("pct", 0) <= -9.8)
    main_industries = _top_industries_from_quotes(quotes)
    breadth = _breadth_from_counts(len(quotes), up_count, down_count, limit_up_count, limit_down_count)
    breadth.update(
        {
            "data_source": data_source,
            "quote_source": quote_source,
            "quote_errors": quote_errors,
            "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "hot_theme": " / ".join(main_industries[:2]) if main_industries else "未知",
        }
    )
    return {
        "snapshot_id": f"snapshot_{data_source}_{quote_source}_{trade_date}_{datetime.now(timezone.utc).replace(tzinfo=None).strftime('%H%M%S')}",
        "trade_date": trade_date,
        "sample_count": len(quotes),
        "up_count": up_count,
        "down_count": down_count,
        "limit_up_count": limit_up_count,
        "limit_down_count": limit_down_count,
        "main_industries": main_industries,
        "breadth": breadth,
    }


def _build_snapshot(
    stocks: list[dict[str, Any]],
    quote_source: str,
    quote_errors: list[str],
    kline_sources: list[str],
    kline_usage: Counter[str],
    kline_errors: dict[str, list[str]],
    quote_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if quote_snapshot is not None:
        trade_date = str(quote_snapshot.get("trade_date") or date.today().isoformat())
        up_count = int(quote_snapshot.get("up_count") or 0)
        down_count = int(quote_snapshot.get("down_count") or 0)
        limit_up_count = int(quote_snapshot.get("limit_up_count") or 0)
        limit_down_count = int(quote_snapshot.get("limit_down_count") or 0)
        main_industries = list(quote_snapshot.get("main_industries") or [])
        breadth = dict(quote_snapshot.get("breadth") or {})
        snapshot_id = str(quote_snapshot.get("snapshot_id") or "")
        sample_count = int(quote_snapshot.get("sample_count") or 0)
    else:
        trade_date = max(str(item.get("latest_trade_date") or date.today().isoformat()) for item in stocks)
        up_count = sum(1 for item in stocks if item.get("pct", 0) > 0)
        down_count = sum(1 for item in stocks if item.get("pct", 0) < 0)
        limit_up_count = sum(1 for item in stocks if item.get("pct", 0) >= 9.8)
        limit_down_count = sum(1 for item in stocks if item.get("pct", 0) <= -9.8)
        main_industries = [name for name, _score in _rank_industries(stocks)[:3] if name != "未分类"]
        breadth = _breadth_from_counts(len(stocks), up_count, down_count, limit_up_count, limit_down_count)
        snapshot_id = f"snapshot_real_{quote_source}_{trade_date}_{datetime.now(timezone.utc).replace(tzinfo=None).strftime('%H%M%S')}"
        sample_count = len(stocks)
    breadth.update(
        {
            "data_source": breadth.get("data_source") or "real_market",
            "quote_source": quote_source,
            "kline_sources": dict(kline_usage),
            "configured_kline_sources": kline_sources,
            "quote_errors": quote_errors,
            "kline_error_count": len(kline_errors),
            "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "hot_theme": breadth.get("hot_theme") or (" / ".join(main_industries[:2]) if main_industries else "未知"),
        }
    )
    return {
        "snapshot_id": snapshot_id,
        "trade_date": trade_date,
        "sample_count": sample_count,
        "up_count": up_count,
        "down_count": down_count,
        "limit_up_count": limit_up_count,
        "limit_down_count": limit_down_count,
        "main_industries": main_industries,
        "breadth": breadth,
    }


def _rank_industries(stocks: list[dict[str, Any]]) -> list[tuple[str, float]]:
    scores: dict[str, float] = defaultdict(float)
    for stock in stocks:
        industry = str(stock.get("industry") or "未分类")
        amount = max(0.0, float(stock.get("amount_yi") or 0))
        pct = float(stock.get("pct") or 0)
        chg20 = float(stock.get("chg20") or 0)
        scores[industry] += amount * (1 + max(-0.2, min(0.5, pct / 20))) + max(0.0, chg20) * 0.8
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


def _top_industries_from_quotes(quotes: list[dict[str, Any]]) -> list[str]:
    scores: dict[str, float] = defaultdict(float)
    for quote in quotes:
        industry = str(quote.get("industry") or "未分类")
        if industry == "未分类":
            continue
        amount = max(0.0, float(quote.get("amount_yi") or 0))
        pct = float(quote.get("pct") or 0)
        scores[industry] += amount * (1 + max(-0.2, min(0.5, pct / 20)))
    return [name for name, _score in sorted(scores.items(), key=lambda item: item[1], reverse=True)[:3]]


def _breadth_from_counts(
    sample_count: int,
    up_count: int,
    down_count: int,
    limit_up_count: int,
    limit_down_count: int,
) -> dict[str, Any]:
    sample = max(1, sample_count)
    up_ratio = up_count / sample
    limit_ratio = limit_up_count / sample
    if up_ratio >= 0.62 and limit_ratio >= 0.03:
        regime = "强势"
        risk = "中"
    elif up_ratio >= 0.55:
        regime = "结构性强势"
        risk = "中"
    elif up_ratio >= 0.42:
        regime = "震荡"
        risk = "中高"
    else:
        regime = "弱势"
        risk = "高"
    return {
        "regime": regime,
        "risk": risk,
        "up_ratio": round(up_ratio, 3),
        "down_ratio": round(down_count / sample, 3),
        "up_limit_ratio": round(limit_ratio, 3),
        "limit_down_ratio": round(limit_down_count / sample, 3),
        "leadership_height": None,
        "broken_board_rate": None,
    }


def _latest_trade_date_from_quotes(quotes: list[dict[str, Any]]) -> str:
    from app.services.market_data import _format_yyyymmdd  # 延迟 import，避免模块级循环依赖

    trade_dates: list[str] = []
    for quote in quotes:
        raw = str(quote.get("trade_date") or quote.get("date") or "").strip()
        if not raw:
            continue
        if len(raw) == 8 and raw.isdigit():
            raw = _format_yyyymmdd(raw)
        if len(raw) >= 10:
            trade_dates.append(raw[:10])
    if trade_dates:
        return max(trade_dates)
    return _latest_allowed_bar_date()
