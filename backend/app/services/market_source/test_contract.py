"""Provider-independent contract tests for MarketSourcePort.

They deliberately use only public typed methods and an in-memory fake; no
network client, database, dynamic capability check, or legacy helper is used.
"""
from __future__ import annotations

import pytest

from app.services.market_source.in_memory_adapter import InMemoryMarketSourceAdapter
from app.services.market_source.port import (
    BatchStatus,
    DailyBarBatch,
    DailyBarRequest,
    FailurePolicy,
    MarketSourcePort,
    MarketSourceRequestFailed,
    QuoteBatch,
    QuoteRef,
    QuoteRequest,
)


def test_public_port_fake_satisfies_success_contract() -> None:
    adapter = InMemoryMarketSourceAdapter(
        quotes=[{"code": "000001", "exchange": "SZ", "price": 10.0}],
        bars_by_code={"000001": [{"trade_date": "20260527", "close": 10.0}]},
    )

    assert isinstance(adapter, MarketSourcePort)
    quote_batch = adapter.fetch_quotes(QuoteRequest())
    bar_batch = adapter.fetch_daily_bars(DailyBarRequest((QuoteRef("000001", "SZ"),)))

    assert isinstance(quote_batch, QuoteBatch)
    assert quote_batch.audit.status is BatchStatus.SUCCESS
    assert quote_batch.quotes[0].reference == QuoteRef("000001", "SZ")
    assert isinstance(bar_batch, DailyBarBatch)
    assert bar_batch.audit.status is BatchStatus.SUCCESS
    assert bar_batch.item_audits[QuoteRef("000001", "SZ")].returned_count == 1


def test_contract_reports_empty_results_without_errors() -> None:
    adapter = InMemoryMarketSourceAdapter()

    quote_batch = adapter.fetch_quotes(QuoteRequest())
    bar_batch = adapter.fetch_daily_bars(DailyBarRequest((QuoteRef("000001"),)))

    assert isinstance(quote_batch, QuoteBatch)
    assert quote_batch.audit.status is BatchStatus.EMPTY
    assert quote_batch.errors == ()
    assert isinstance(bar_batch, DailyBarBatch)
    assert bar_batch.audit.status is BatchStatus.EMPTY
    assert bar_batch.item_audits[QuoteRef("000001")].status is BatchStatus.EMPTY


def test_contract_reports_partial_per_instrument_failure_with_audit() -> None:
    success_ref = QuoteRef("000001", "SZ")
    failing_ref = QuoteRef("000002", "SZ")
    adapter = InMemoryMarketSourceAdapter(
        bars_by_code={"000001": [{"trade_date": "20260527", "close": 10.0}]},
        failing_codes=["000002"],
    )

    batch = adapter.fetch_daily_bars(DailyBarRequest((success_ref, failing_ref)))

    assert isinstance(batch, DailyBarBatch)
    assert batch.audit.status is BatchStatus.PARTIAL
    assert batch.audit.failed_count == 1
    assert batch.item_audits[failing_ref].status is BatchStatus.FAILURE
    assert batch.errors[0].reference == failing_ref


def test_contract_reports_fatal_batch_or_raises_when_requested() -> None:
    reference = QuoteRef("000001", "SZ")
    adapter = InMemoryMarketSourceAdapter(failing_codes=[reference.code])

    batch = adapter.fetch_daily_bars(DailyBarRequest((reference,)))
    assert isinstance(batch, DailyBarBatch)
    assert batch.audit.status is BatchStatus.FAILURE

    with pytest.raises(MarketSourceRequestFailed) as raised:
        adapter.fetch_daily_bars(
            DailyBarRequest((reference,), failure_policy=FailurePolicy.RAISE)
        )
    assert raised.value.error.reference == reference


class PortOnlyFake:
    """A test double deliberately lacking every legacy compatibility method."""

    def __init__(self) -> None:
        self._delegate = InMemoryMarketSourceAdapter(
            quotes=[{"code": "000001", "exchange": "SZ", "price": 10.0}],
            bars_by_code={"000001": [{"trade_date": "20260527", "close": 10.0}]},
        )

    def fetch_quotes(self, request: QuoteRequest) -> QuoteBatch:
        result = self._delegate.fetch_quotes(request)
        assert isinstance(result, QuoteBatch)
        return result

    def fetch_daily_bars(self, request: DailyBarRequest) -> DailyBarBatch:
        result = self._delegate.fetch_daily_bars(request)
        assert isinstance(result, DailyBarBatch)
        return result


def test_contract_requires_only_the_two_public_port_methods() -> None:
    source = PortOnlyFake()
    reference = QuoteRef("000001", "SZ")

    assert isinstance(source, MarketSourcePort)
    assert not hasattr(source, "fetch_bars_for_quote")
    assert source.fetch_quotes(QuoteRequest()).audit.status is BatchStatus.SUCCESS
    assert source.fetch_daily_bars(DailyBarRequest((reference,))).audit.status is BatchStatus.SUCCESS


def test_tushare_adapter_typed_contract_uses_a_monkeypatched_loader(monkeypatch) -> None:
    from app.adapters.market_source.tushare import TushareMarketSourceAdapter
    from app.services import market_data

    def fake_fetch_quotes(source: str, settings: object) -> list[dict[str, object]]:
        assert source == "tushare"
        return [{"code": "000001", "exchange": "SZ", "price": 10.0}]

    def fake_fetch_kline(
        source: str,
        quote: dict[str, object],
        settings: object,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, object]]:
        assert source == "tushare"
        assert quote == {"code": "000001", "exchange": "SZ"}
        assert start_date == "20260101"
        return [{"trade_date": "20260101", "close": 10.0}]

    monkeypatch.setattr(market_data, "_fetch_quotes", fake_fetch_quotes)
    monkeypatch.setattr(market_data, "_fetch_kline", fake_fetch_kline)
    adapter = TushareMarketSourceAdapter(settings=object())

    quotes = adapter.fetch_quotes(QuoteRequest(max_quote_count=1))
    bars = adapter.fetch_daily_bars(
        DailyBarRequest((QuoteRef("000001", "SZ"),), start_date="20260101")
    )

    assert isinstance(quotes, QuoteBatch)
    assert quotes.audit.status is BatchStatus.SUCCESS
    assert isinstance(bars, DailyBarBatch)
    assert bars.audit.status is BatchStatus.SUCCESS
