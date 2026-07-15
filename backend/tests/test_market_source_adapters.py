"""任务 4.2：`market_source` 两个 adapter 的最小单测。

- `InMemoryMarketSourceAdapter`：直接驱动夹具数据，验证 port 契约。
- `TushareMarketSourceAdapter`：monkeypatch 掉 `market_data` 里真正发起
  网络/Tushare 调用的函数（`_fetch_quotes`/`_fetch_kline`），只验证
  "委托而不是重写"这层结构——不发起任何真实网络请求。
"""
from __future__ import annotations

from app.services.market_source.in_memory_adapter import InMemoryMarketSourceAdapter
from app.services.market_source.multi_source_adapter import TushareMarketSourceAdapter
from app.services.market_source.port import DateRange, QuoteScope


class DummySettings:
    market_scan_size = 60
    market_kline_days = 90


def test_in_memory_adapter_fetch_quotes_returns_fixture():
    quotes = [{"code": "000001"}, {"code": "000002"}]
    adapter = InMemoryMarketSourceAdapter(quotes=quotes)

    assert adapter.fetch_quotes() == quotes


def test_in_memory_adapter_fetch_quotes_respects_max_quote_count():
    quotes = [{"code": "000001"}, {"code": "000002"}, {"code": "000003"}]
    adapter = InMemoryMarketSourceAdapter(quotes=quotes)

    result = adapter.fetch_quotes(QuoteScope(max_quote_count=2))

    assert result == quotes[:2]


def test_in_memory_adapter_fetch_daily_bars_looks_up_by_code():
    bars_by_code = {"000001": [{"trade_date": "2026-05-27", "close": 10.0}]}
    adapter = InMemoryMarketSourceAdapter(bars_by_code=bars_by_code)

    result = adapter.fetch_daily_bars(["000001", "000002"])

    assert result == {"000001": bars_by_code["000001"], "000002": []}


def test_in_memory_adapter_fetch_daily_bars_simulates_failing_codes():
    bars_by_code = {"000001": [{"trade_date": "2026-05-27", "close": 10.0}]}
    adapter = InMemoryMarketSourceAdapter(bars_by_code=bars_by_code, failing_codes=["000001"])

    result = adapter.fetch_daily_bars(["000001"])

    assert result == {"000001": []}


def test_tushare_adapter_fetch_quotes_delegates_to_market_data(monkeypatch):
    from app.services import market_data
    from app.services.market_source import multi_source_adapter

    calls: list[tuple[str, object]] = []

    def fake_fetch_quotes(source, settings):
        calls.append((source, settings))
        return [{"code": "000001"}]

    monkeypatch.setattr(market_data, "_fetch_quotes", fake_fetch_quotes)

    adapter = TushareMarketSourceAdapter(DummySettings())
    result = adapter.fetch_quotes()

    assert result == [{"code": "000001"}]
    assert calls and calls[0][0] == "tushare"


def test_tushare_adapter_fetch_quotes_scope_overrides_market_scan_size(monkeypatch):
    from app.services import market_data

    seen_scan_sizes: list[int] = []

    def fake_fetch_quotes(source, settings):
        seen_scan_sizes.append(settings.market_scan_size)
        return []

    monkeypatch.setattr(market_data, "_fetch_quotes", fake_fetch_quotes)

    adapter = TushareMarketSourceAdapter(DummySettings())
    adapter.fetch_quotes(QuoteScope(max_quote_count=5))

    assert seen_scan_sizes == [5]


def test_tushare_adapter_fetch_daily_bars_delegates_per_code_and_degrades_on_failure(monkeypatch):
    from app.services import market_data

    def fake_fetch_kline(source, quote, settings, start_date=None, end_date=None):
        if quote["code"] == "000002":
            raise RuntimeError("boom")
        return [{"code": quote["code"], "trade_date": "2026-05-27"}]

    monkeypatch.setattr(market_data, "_fetch_kline", fake_fetch_kline)

    adapter = TushareMarketSourceAdapter(DummySettings())
    result = adapter.fetch_daily_bars(["000001", "000002"], DateRange(start_date="20260101"))

    assert result["000001"] == [{"code": "000001", "trade_date": "2026-05-27"}]
    assert result["000002"] == []


def test_tushare_adapter_fetch_bars_for_quote_propagates_exception(monkeypatch):
    from app.services import market_data

    def fake_fetch_kline(source, quote, settings, start_date=None, end_date=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(market_data, "_fetch_kline", fake_fetch_kline)

    adapter = TushareMarketSourceAdapter(DummySettings())
    try:
        adapter.fetch_bars_for_quote({"code": "000001", "exchange": "SZ"})
    except RuntimeError as exc:
        assert str(exc) == "boom"
    else:
        raise AssertionError("expected RuntimeError to propagate")
