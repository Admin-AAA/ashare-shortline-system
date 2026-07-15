"""Typed market-source Port, DTOs, and deterministic test adapter."""
from __future__ import annotations

from .in_memory_adapter import InMemoryMarketSourceAdapter
from .local_pool_adapter import LocalPoolMarketSourceAdapter, load_legacy_market_universe
from .port import (
    BatchStatus,
    DailyBar,
    DailyBarBatch,
    DailyBarRequest,
    DateRange,
    FailurePolicy,
    ItemAudit,
    MarketSourceAudit,
    MarketSourceError,
    MarketSourceErrorCode,
    MarketSourceOperation,
    MarketSourcePort,
    MarketSourceRequestFailed,
    Quote,
    QuoteBatch,
    QuoteRef,
    QuoteRequest,
    QuoteScope,
)

__all__ = [
    "BatchStatus",
    "DailyBar",
    "DailyBarBatch",
    "DailyBarRequest",
    "DateRange",
    "FailurePolicy",
    "InMemoryMarketSourceAdapter",
    "LocalPoolMarketSourceAdapter",
    "load_legacy_market_universe",
    "ItemAudit",
    "MarketSourceAudit",
    "MarketSourceError",
    "MarketSourceErrorCode",
    "MarketSourceOperation",
    "MarketSourcePort",
    "MarketSourceRequestFailed",
    "Quote",
    "QuoteBatch",
    "QuoteRef",
    "QuoteRequest",
    "QuoteScope",
]
