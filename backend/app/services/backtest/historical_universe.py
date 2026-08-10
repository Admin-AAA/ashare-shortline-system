"""历史回放 universe 加载器：从历史数据池直接构建候选池（幸存者偏差防护）。

不读 stock_quotes 实时报价池（其历史仅覆盖近 ~56 天），而是从：
- daily_kbars（OHLCV/pct_chg/turnover_rate，已全覆盖 + 本地指标）
- stock_metadata_history（时点 industry/is_st/name/list_date，消除幸存者偏差 + ST 时点过滤）
- daily_basic（pe_ttm/total_mv/circ_mv，估值口径）

合成与生产 stock_quotes 完全同构的 quote dict，再交给
``get_market_universe_from_pool(quotes_override=...)`` 走与生产一致的
候选过滤/召回/快照流程，保证回测口径 == 生产口径。

研究专用；生产 LIVE 推荐路径不经过这里。
"""
from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import DailyBasic, DailyKbar
from app.services.backtest.stock_metadata import (
    POINT_IN_TIME,
    StockMetadataProvider,
)


def build_historical_quotes(
    db: Session,
    as_of_date: str,
    *,
    stock_metadata_provider: StockMetadataProvider | None = None,
) -> list[dict[str, Any]]:
    """为 as_of_date 合成全市场 quote dict（与 stock_quotes 同构）。

    universe = 该日 daily_kbars 有数据的股票（含已退市股，幸存者偏差防护）。
    industry/is_st/name 用时点元数据覆盖；缺失 code 保留 daily_kbars 当日行
    （下游 ST/停牌硬过滤处理）。
    """
    bar_rows = db.scalars(
        select(DailyKbar).where(DailyKbar.trade_date == as_of_date)
    ).all()
    if not bar_rows:
        return []

    codes = [str(b.code) for b in bar_rows]

    # 时点元数据
    meta_by_code: dict[str, dict[str, Any]] = {}
    if stock_metadata_provider is not None:
        try:
            resolution = stock_metadata_provider.resolve(codes, as_of_date)
        except Exception:
            resolution = None
        if resolution is not None and resolution.metadata_by_code:
            meta_by_code = {
                str(code): dict(meta)
                for code, meta in resolution.metadata_by_code.items()
            }

    # 估值（daily_basic，万元）
    basic_by_code: dict[str, DailyBasic] = {
        str(row.code): row
        for row in db.scalars(
            select(DailyBasic).where(DailyBasic.trade_date == as_of_date)
        ).all()
    }

    # 前收（用于 preclose；当日 daily_kbars 已有 open/high/low/close）
    quotes: list[dict[str, Any]] = []
    for bar in bar_rows:
        code = str(bar.code)
        meta = meta_by_code.get(code, {})
        basic = basic_by_code.get(code)
        total_mv = float(basic.total_mv) if basic and basic.total_mv is not None else None
        circ_mv = float(basic.circ_mv) if basic and basic.circ_mv is not None else None
        close = float(bar.close or 0)
        preclose = close / (1 + float(bar.pct_chg or 0) / 100) if bar.pct_chg else None
        name = str(meta.get("name") or code)
        quotes.append(
            {
                "code": code,
                "name": name,
                "trade_date": as_of_date,
                "price": close,
                "pct": float(bar.pct_chg or 0),
                "volume": float(bar.volume or 0),
                "amount_yi": float(bar.amount or 0) / 1e8 if bar.amount else 0.0,
                "turnover_rate": (
                    float(basic.turnover_rate)
                    if basic and basic.turnover_rate is not None
                    else float(bar.turnover_rate or 0)
                ),
                "pe_ttm": (float(basic.pe_ttm) if basic and basic.pe_ttm is not None else None),
                "total_mv_yi": (total_mv / 1e4 if total_mv is not None else None),
                "free_mv_yi": (circ_mv / 1e4 if circ_mv is not None else None),
                "chg60": None,
                "high": float(bar.high or 0),
                "low": float(bar.low or 0),
                "open": float(bar.open or 0),
                "preclose": preclose,
                "industry": str(meta.get("industry") or "未分类"),
                "is_st": bool(meta.get("is_st", False)),
                "source_count": 1,
                "quality_score": 1.0,
                "freshness_status": "historical_replay",
                "fetched_at": "",
                "raw_sources": ["historical_pool"],
                "conflicts": [],
                "exchange": "SH" if code.startswith(("6", "9")) else "SZ",
            }
        )
    return quotes


def load_historical_universe(
    db: Session,
    as_of_date: str,
    *,
    stock_metadata_provider: StockMetadataProvider | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """构建历史回放 universe（stocks + snapshot），与生产口径一致。"""
    from app.services.market_pool.core import get_market_universe_from_pool

    quotes = build_historical_quotes(
        db, as_of_date, stock_metadata_provider=stock_metadata_provider
    )
    return get_market_universe_from_pool(
        db, refresh=False, as_of_date=as_of_date, quotes_override=quotes
    )


__all__ = ["build_historical_quotes", "load_historical_universe"]
