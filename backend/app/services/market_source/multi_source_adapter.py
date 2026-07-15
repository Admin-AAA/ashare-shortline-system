"""Compatibility import for the relocated production market-source adapter.

New composition code imports ``app.adapters.market_source.TushareMarketSourceAdapter``.
This module preserves the former import path while callers migrate.
"""
from app.adapters.market_source.tushare import TushareMarketSourceAdapter

__all__ = ["TushareMarketSourceAdapter"]
