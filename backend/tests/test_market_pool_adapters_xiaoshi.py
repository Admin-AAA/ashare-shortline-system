from types import SimpleNamespace

from app.services import market_pool_adapters
from app.services.market_source.port import (
    BatchStatus,
    DailyBarBatch,
    DailyBarRequest,
    MarketSourceAudit,
    MarketSourceOperation,
    QuoteBatch,
    QuoteRef,
    QuoteRequest,
)


class _FakeXiaoshiAdapter:
    def __init__(self, *, settings):
        self.settings = settings

    def fetch_quotes(self, request):
        return QuoteBatch(
            quotes=(),
            errors=(),
            audit=MarketSourceAudit(
                source="xiaoshi",
                operation=MarketSourceOperation.QUOTES,
                failure_policy=request.failure_policy,
                status=BatchStatus.EMPTY,
                requested_count=1,
                returned_count=0,
                empty_count=1,
                failed_count=0,
            ),
        )

    def fetch_daily_bars(self, request):
        return DailyBarBatch(
            bars_by_instrument={},
            item_audits={},
            errors=(),
            audit=MarketSourceAudit(
                source="xiaoshi",
                operation=MarketSourceOperation.DAILY_BARS,
                failure_policy=request.failure_policy,
                status=BatchStatus.EMPTY,
                requested_count=len(request.instruments),
                returned_count=0,
                empty_count=len(request.instruments),
                failed_count=0,
            ),
        )


def test_xiaoshi_is_supported_by_typed_market_pool_boundary(monkeypatch):
    monkeypatch.setattr(market_pool_adapters, "XiaoshiMarketSourceAdapter", _FakeXiaoshiAdapter)
    settings = SimpleNamespace()

    quotes = market_pool_adapters.fetch_quote_batch("xiaoshi", settings, QuoteRequest())
    bars = market_pool_adapters.fetch_daily_bar_batch(
        "xiaoshi",
        settings,
        DailyBarRequest(instruments=(QuoteRef("000001", "SZ"),)),
    )

    assert quotes.audit.source == "xiaoshi"
    assert bars.audit.source == "xiaoshi"
