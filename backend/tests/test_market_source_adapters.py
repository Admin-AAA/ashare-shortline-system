"""No-network typed-contract tests for the market-source adapters."""
from __future__ import annotations

import pytest

from app.adapters.market_source import TushareMarketSourceAdapter
from app.services.market_source.in_memory_adapter import InMemoryMarketSourceAdapter
from app.services.market_source.port import (
    BatchStatus,
    DailyBarBatch,
    DailyBarRequest,
    FailurePolicy,
    MarketSourceRequestFailed,
    QuoteBatch,
    QuoteRef,
    QuoteRequest,
)


class DummySettings:
    market_scan_size = 60
    market_kline_days = 90


def test_in_memory_adapter_returns_typed_quote_batch_with_request_limit() -> None:
    adapter = InMemoryMarketSourceAdapter(
        quotes=[{"code": "000001"}, {"code": "000002"}, {"code": "000003"}]
    )

    batch = adapter.fetch_quotes(QuoteRequest(max_quote_count=2))

    assert isinstance(batch, QuoteBatch)
    assert batch.audit.status is BatchStatus.SUCCESS
    assert batch.audit.returned_count == 2
    assert [quote.reference.code for quote in batch.quotes] == ["000001", "000002"]
    assert not hasattr(adapter, "fetch_bars_for_quote")


def test_in_memory_adapter_reports_empty_partial_failure_and_raise() -> None:
    success_ref = QuoteRef("000001", "SZ")
    missing_ref = QuoteRef("000002", "SZ")
    failing_ref = QuoteRef("000003", "SZ")
    adapter = InMemoryMarketSourceAdapter(
        bars_by_code={"000001": [{"trade_date": "20260527", "close": 10.0}]},
        failing_codes=[failing_ref.code],
    )

    empty = adapter.fetch_daily_bars(DailyBarRequest((missing_ref,)))
    partial = adapter.fetch_daily_bars(DailyBarRequest((success_ref, failing_ref)))
    failure = adapter.fetch_daily_bars(DailyBarRequest((failing_ref,)))

    assert isinstance(empty, DailyBarBatch)
    assert empty.audit.status is BatchStatus.EMPTY
    assert empty.audit.empty_count == 1
    assert partial.audit.status is BatchStatus.PARTIAL
    assert partial.audit.returned_count == 1
    assert partial.audit.failed_count == 1
    assert partial.item_audits[failing_ref].status is BatchStatus.FAILURE
    assert failure.audit.status is BatchStatus.FAILURE
    assert failure.errors[0].reference == failing_ref
    with pytest.raises(MarketSourceRequestFailed) as raised:
        adapter.fetch_daily_bars(
            DailyBarRequest((failing_ref,), failure_policy=FailurePolicy.RAISE)
        )
    assert raised.value.error.reference == failing_ref


def test_tushare_adapter_delegates_typed_requests_without_network(monkeypatch) -> None:
    from app.services import market_data

    quote_calls: list[tuple[str, object]] = []
    bar_calls: list[tuple[str, dict[str, object], str | None]] = []

    def fake_fetch_quotes(source: str, settings: object) -> list[dict[str, object]]:
        quote_calls.append((source, settings))
        return [{"code": "000001", "exchange": "SZ", "price": 10.0}]

    def fake_fetch_kline(
        source: str,
        quote: dict[str, object],
        settings: object,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, object]]:
        bar_calls.append((source, quote, start_date))
        return [{"code": quote["code"], "trade_date": "20260527", "close": 10.0}]

    monkeypatch.setattr(market_data, "_fetch_quotes", fake_fetch_quotes)
    monkeypatch.setattr(market_data, "_fetch_kline", fake_fetch_kline)
    settings = DummySettings()
    adapter = TushareMarketSourceAdapter(settings)

    quotes = adapter.fetch_quotes(QuoteRequest(max_quote_count=1))
    bars = adapter.fetch_daily_bars(
        DailyBarRequest((QuoteRef("000001", "SZ"),), start_date="20260101")
    )

    assert quotes.audit.status is BatchStatus.SUCCESS
    assert bars.audit.status is BatchStatus.SUCCESS
    assert quote_calls == [("tushare", settings)]
    assert bar_calls == [("tushare", {"code": "000001", "exchange": "SZ"}, "20260101")]
    assert not hasattr(adapter, "fetch_bars_for_quote")


def test_tushare_quote_empty_failure_and_raise_follow_typed_audit(monkeypatch) -> None:
    from app.services import market_data

    adapter = TushareMarketSourceAdapter(DummySettings())
    monkeypatch.setattr(market_data, "_fetch_quotes", lambda _source, _settings: [])
    empty = adapter.fetch_quotes(QuoteRequest())

    def fail_quotes(_source: str, _settings: object) -> list[dict[str, object]]:
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(market_data, "_fetch_quotes", fail_quotes)
    failure = adapter.fetch_quotes(QuoteRequest())

    assert empty.audit.status is BatchStatus.EMPTY
    assert empty.audit.empty_count == 1
    assert failure.audit.status is BatchStatus.FAILURE
    assert failure.audit.failed_count == 1
    with pytest.raises(MarketSourceRequestFailed):
        adapter.fetch_quotes(QuoteRequest(failure_policy=FailurePolicy.RAISE))


def test_tushare_daily_bars_partial_failure_and_raise_follow_typed_audit(monkeypatch) -> None:
    from app.services import market_data

    def fake_fetch_kline(
        _source: str,
        quote: dict[str, object],
        _settings: object,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, object]]:
        if quote["code"] == "000002":
            raise RuntimeError("provider unavailable")
        return [{"code": quote["code"], "trade_date": "20260527", "close": 10.0}]

    monkeypatch.setattr(market_data, "_fetch_kline", fake_fetch_kline)
    adapter = TushareMarketSourceAdapter(DummySettings())
    success_ref = QuoteRef("000001", "SZ")
    failing_ref = QuoteRef("000002", "SZ")

    partial = adapter.fetch_daily_bars(DailyBarRequest((success_ref, failing_ref)))
    failure = adapter.fetch_daily_bars(DailyBarRequest((failing_ref,)))

    assert partial.audit.status is BatchStatus.PARTIAL
    assert partial.audit.returned_count == 1
    assert partial.audit.failed_count == 1
    assert failure.audit.status is BatchStatus.FAILURE
    assert failure.item_audits[failing_ref].status is BatchStatus.FAILURE
    with pytest.raises(MarketSourceRequestFailed) as raised:
        adapter.fetch_daily_bars(
            DailyBarRequest((failing_ref,), failure_policy=FailurePolicy.RAISE)
        )
    assert raised.value.error.reference == failing_ref


def test_tushare_adapter_fetches_a_trade_date_batch_once_with_typed_audits(monkeypatch) -> None:
    from app.services import tushare_client

    calls: list[str] = []

    def fake_fetch_trade_date(trade_date: str) -> list[dict[str, object]]:
        calls.append(trade_date)
        return [
            {"code": "000001", "trade_date": "2026-05-27", "close": 10.0},
            {"code": "000002", "trade_date": "2026-05-27", "close": 11.0},
            {"code": "000004", "trade_date": "2026-05-27", "close": 12.0},
        ]

    monkeypatch.setattr(tushare_client, "fetch_daily_kbars_for_trade_date", fake_fetch_trade_date)
    adapter = TushareMarketSourceAdapter(DummySettings())
    requested = (QuoteRef("000001", "SZ"), QuoteRef("000003", "SZ"))

    batch = adapter.fetch_daily_bars(
        DailyBarRequest(instruments=requested, trade_date="2026-05-27")
    )

    assert calls == ["2026-05-27"]
    assert batch.audit.status is BatchStatus.SUCCESS
    assert batch.audit.requested_count == 2
    assert batch.audit.returned_count == 3
    assert batch.item_audits[requested[0]].status is BatchStatus.SUCCESS
    assert batch.item_audits[requested[1]].status is BatchStatus.EMPTY
    assert set(batch.as_legacy_rows_by_code()) == {"000001", "000002", "000003", "000004"}


def test_daily_bar_request_rejects_ambiguous_or_unscoped_requests() -> None:
    with pytest.raises(ValueError, match="requires instruments or trade_date"):
        DailyBarRequest()
    with pytest.raises(ValueError, match="cannot be combined"):
        DailyBarRequest(
            instruments=(QuoteRef("000001"),),
            trade_date="2026-05-27",
            start_date="2026-05-01",
        )
