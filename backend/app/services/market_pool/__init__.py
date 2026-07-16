"""行情池：公开契约 re-export 层。

核心报价池 + 日K池 + candidate readiness + universe/snapshot 组装
在 ``core.py`` 中实现；辅助数据集在 ``auxiliary.py`` 中实现。

本文件只 re-export 7 个公开契约函数和 ``MarketDataError``。
内部实现细节（``_`` 前缀函数）不再 re-export；调用方应直接从
``core.py`` 或 ``trading_calendar`` 导入。

``history_maturity.py`` 不受影响，``from app.services.market_pool.history_maturity
import ...`` 路径保持不变。
"""
from __future__ import annotations

from app.services.market_pool import core as core
from app.services.market_pool.core import (
    MarketDataError,
    get_market_universe_from_pool,
    get_market_universe_from_pool_with_realtime,
    get_market_snapshot_from_pool,
    refresh_quote_pool,
    local_pool_recommendation_readiness,
    enrich_quote_with_local_metrics,
)

__all__ = [
    "core",
    "auxiliary",
    # core 对外契约
    "get_market_universe_from_pool",
    "get_market_universe_from_pool_with_realtime",
    "get_market_snapshot_from_pool",
    "refresh_quote_pool",
    "local_pool_recommendation_readiness",
    "enrich_quote_with_local_metrics",
    "MarketDataError",
]
