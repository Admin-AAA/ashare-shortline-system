from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.tables import DailyKbar, DataSourceConfig, StockQuote
from app.services.data_sources import enabled_sources, get_local_pool_status, sync_data_source_configs
from app.services import market_pool


class DummySettings:
    market_request_timeout_seconds = 5
    market_scan_size = 60
    market_kline_days = 90
    market_candidate_count = 2
    market_broad_recall_count = 8
    market_candidate_lookahead_multiplier = 2
    market_quote_cache_ttl_seconds = 90
    market_kline_cache_ttl_hours = 20
    market_pool_min_source_count = 1
    quote_sources = "tushare"
    kline_sources = "tushare"
    limit_up_sources = "tushare"
    concept_sector_sources = "tushare"
    capital_flow_sources = "tushare"
    dragon_tiger_sources = "tushare"
    news_sources = ""
    data_provider = "tushare"
    tushare_token = ""

@pytest.fixture
def db(clean_test_db) -> Session:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _quote(code: str, source: str, price: float, pct: float = 3.0) -> dict:
    return {
        "code": code,
        "name": f"测试{code}",
        "exchange": "SZ",
        "industry": "软件服务",
        "is_st": False,
        "price": price,
        "pct": pct,
        "volume": 1000000,
        "amount_yi": 8.0,
        "turnover_rate": 3.0,
        "pe_ttm": 30.0,
        "total_mv_yi": 120.0,
        "free_mv_yi": 80.0,
        "chg60": 20.0,
        "ytd_chg": 15.0,
        "main_net_inflow_yi": 0.4,
        "source": source,
    }


def _low_liquidity_quote(code: str, source: str, price: float = 10.0) -> dict:
    quote = _quote(code, source, price)
    quote["amount_yi"] = 0.2
    quote["turnover_rate"] = 0.05
    return quote


def _bars(code: str, days: int = 35, end: date | None = None) -> list[dict]:
    end = end or date.today()
    return [
        {
            "code": code,
            "trade_date": (end - timedelta(days=days - index - 1)).isoformat(),
            "open": 10 + index * 0.01,
            "high": 11 + index * 0.01,
            "low": 9 + index * 0.01,
            "close": 10.5 + index * 0.01,
            "volume": 1000 + index,
            "amount": 10000 + index,
            "pct_chg": 1,
            "turnover_rate": 2,
        }
        for index in range(days)
    ]


def test_refresh_quote_pool_persists_raw_and_unified_quotes(db, monkeypatch):
    settings = DummySettings()

    def fake_fetch_quotes(source, _settings):
        return [_quote("000001", source, 10.00), _quote("000002", source, 20.00)]

    monkeypatch.setattr(market_pool, "get_settings", lambda: settings)
    monkeypatch.setattr(market_pool, "_fetch_quotes", fake_fetch_quotes)

    quotes, meta = market_pool.refresh_quote_pool(db)

    assert meta["status"] == "refreshed"
    assert len(quotes) == 2
    assert {quote["source_count"] for quote in quotes} == {1}
    assert db.scalar(select(RawQuote).where(RawQuote.source == "tushare", RawQuote.code == "000001")) is not None
    unified = db.scalar(select(UnifiedQuote).where(UnifiedQuote.code == "000001"))
    assert unified is not None
    assert unified.source_count == 1
    assert unified.quality_score > 80


def test_quote_pool_records_no_conflicts_with_single_source():
    settings = DummySettings()

    merged = market_pool._merge_quote_rows(
        "000001",
        [_quote("000001", "tushare", 10.00, pct=3.0)],
        ["tushare"],
        settings,
    )

    assert merged["source_count"] == 1
    assert not merged["conflicts"]
    assert merged["quality_score"] >= 90


def test_load_or_refresh_quotes_uses_fresh_cache(db, monkeypatch):
    settings = DummySettings()
    calls = {"fetch": 0}

    def fake_fetch_quotes(source, _settings):
        calls["fetch"] += 1
        return [_quote("000001", source, 10.00), _quote("000002", source, 20.00)]

    monkeypatch.setattr(market_pool, "_fetch_quotes", fake_fetch_quotes)

    first, first_meta = market_pool._load_or_refresh_quotes(db, settings)
    second, second_meta = market_pool._load_or_refresh_quotes(db, settings)

    assert first_meta["status"] == "refreshed"
    assert second_meta["status"] == "fresh_cache"
    assert len(first) == len(second) == 2
    assert calls["fetch"] == 1


def test_cached_quotes_are_enriched_with_local_bar_metrics(db, monkeypatch):
    settings = DummySettings()
    market_pool._persist_unified_quotes(
        db,
        [
            {
                **_quote("000001", "tushare", 16.0),
                "source_count": 1,
                "raw_sources": ["tushare"],
                "quality_score": 80.0,
                "freshness_status": "fresh",
                "chg60": 0.0,
                "ytd_chg": 0.0,
                "source_trace": {"raw_sources": ["tushare"], "source_count": 1, "field_sources": {}},
            }
        ],
    )
    for index in range(65):
        trade_date = date(2025, 12, 1) + timedelta(days=index)
        db.add(
            DailyKbar(
                code="000001",
                trade_date=trade_date.isoformat(),
                open=10 + index * 0.1,
                high=10 + index * 0.1,
                low=10 + index * 0.1,
                close=10 + index * 0.1,
                volume=1000,
                amount=10000,
                pct_chg=1,
                turnover_rate=2,
                ma5=14.1,
                ma10=13.8,
                ma20=13.1,
            )
        )
    db.commit()

    monkeypatch.setattr(market_pool, "_latest_allowed_bar_date", lambda today=None: "2026-02-03")

    quotes = market_pool._load_cached_quotes(db)

    assert quotes[0]["chg60"] != 0
    assert quotes[0]["ytd_chg"] != 0
    assert quotes[0]["ma5"] == 14.1
    assert quotes[0]["ma10"] == 13.8
    assert quotes[0]["ma20"] == 13.1
    assert quotes[0]["source_trace"]["field_sources"]["chg60"] == ["daily_bars_cache"]
    assert quotes[0]["source_trace"]["field_sources"]["ma5"] == ["daily_bars_cache"]


def test_load_or_refresh_quotes_does_not_use_stale_cache_after_refresh_error(db, monkeypatch):
    settings = DummySettings()
    market_pool._persist_unified_quotes(
        db,
        [
            market_pool._merge_quote_rows(
                "000001",
                [_quote("000001", "tushare", 10.00)],
                ["tushare"],
                settings,
            ),
            market_pool._merge_quote_rows(
                "000002",
                [_quote("000002", "tushare", 20.00)],
                ["tushare"],
                settings,
            ),
        ],
    )
    db.commit()
    for row in db.scalars(select(UnifiedQuote)).all():
        row.fetched_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=settings.market_quote_cache_ttl_seconds + 1)
    db.commit()

    def fake_fetch_quotes(source, _settings):
        raise RuntimeError(f"{source} down")

    monkeypatch.setattr(market_pool, "_fetch_quotes", fake_fetch_quotes)

    try:
        market_pool._load_or_refresh_quotes(db, settings)
    except market_pool.MarketDataError as exc:
        assert "统一行情池刷新失败" in str(exc)
    else:
        raise AssertionError("expected MarketDataError")


def test_disabled_quote_source_is_not_called(db, monkeypatch):
    settings = DummySettings()
    db.add(DataSourceConfig(source="tushare", source_type="quote", enabled=False, priority=0))
    db.commit()
    calls = []

    def fake_fetch_quotes(source, _settings):
        calls.append(source)
        return [_quote("000001", source, 10.00), _quote("000002", source, 20.00)]

    monkeypatch.setattr(market_pool, "_fetch_quotes", fake_fetch_quotes)

    quotes, meta = market_pool._load_or_refresh_quotes(db, settings)

    assert len(quotes) == 0
    assert meta["sources"] == []
    assert calls == []


def test_all_quote_sources_disabled_raises_without_fetching(db, monkeypatch):
    settings = DummySettings()
    for index, source in enumerate(["tushare"]):
        db.add(DataSourceConfig(source=source, source_type="quote", enabled=False, priority=index))
    db.commit()

    def fake_fetch_quotes(source, _settings):
        raise AssertionError(f"disabled source should not be fetched: {source}")

    monkeypatch.setattr(market_pool, "_fetch_quotes", fake_fetch_quotes)

    try:
        market_pool._load_or_refresh_quotes(db, settings)
    except market_pool.MarketDataError as exc:
        assert "所有报价数据源都已关闭" in str(exc)
    else:
        raise AssertionError("expected MarketDataError")


def test_enabled_sources_respects_synced_config_without_extra_insert(db, monkeypatch):
    settings = DummySettings()
    monkeypatch.setattr("app.services.data_sources.get_settings", lambda: settings)

    sync_data_source_configs(db)
    row = db.get(DataSourceConfig, {"source": "tushare", "source_type": "quote"})
    assert row is not None
    row.enabled = False
    db.commit()

    active = enabled_sources(db, "quote", ["tushare"])
    count = db.query(DataSourceConfig).filter(
        DataSourceConfig.source == "tushare",
        DataSourceConfig.source_type == "quote",
    ).count()

    assert active == []
    assert count == 1


def test_load_quotes_from_pool_never_fetches_external_sources(db, monkeypatch):
    settings = DummySettings()

    def fake_fetch_quotes(source, _settings):
        raise AssertionError(f"local-only mode should not fetch {source}")

    monkeypatch.setattr(market_pool, "_fetch_quotes", fake_fetch_quotes)

    try:
        market_pool._load_quotes_from_pool(db, settings)
    except market_pool.MarketDataError as exc:
        assert "本地数据池可用报价不足" in str(exc)
    else:
        raise AssertionError("expected MarketDataError")


def test_local_only_skips_stale_bars_instead_of_running_with_them(db, monkeypatch):
    settings = DummySettings()
    settings.market_candidate_count = 1
    settings.market_candidate_lookahead_multiplier = 1

    merged = market_pool._merge_quote_rows(
        "000001",
        [_quote("000001", "tushare", 10.00)],
        ["tushare"],
        settings,
    )
    market_pool._persist_unified_quotes(db, [merged])
    market_pool._persist_normalized_bars(db, _bars("000001", days=35, end=date(2026, 5, 1)))
    db.commit()

    monkeypatch.setattr(market_pool, "get_settings", lambda: settings)

    try:
        market_pool.get_market_universe_from_pool(db, refresh=False)
    except market_pool.MarketDataError as exc:
        assert "本地日K未覆盖报价日期" in str(exc)
    else:
        raise AssertionError("expected MarketDataError")


def test_get_market_universe_local_pool_preferred_refreshes_when_pool_is_empty(db, monkeypatch):
    settings = DummySettings()
    calls = {"quotes": 0, "kline": 0}

    def fake_fetch_quotes(source, _settings):
        calls["quotes"] += 1
        return [_quote("000001", source, 10.00), _quote("000002", source, 20.00)]

    def fake_fetch_kline(_source, quote, _settings, **__):
        calls["kline"] += 1
        return _bars(quote["code"], days=35)

    monkeypatch.setattr(market_pool, "get_settings", lambda: settings)
    monkeypatch.setattr(market_pool, "_fetch_quotes", fake_fetch_quotes)
    monkeypatch.setattr(market_pool, "_fetch_kline", fake_fetch_kline)

    stocks, snapshot = market_pool.get_market_universe_from_pool(db, refresh=True)

    assert len(stocks) == 2
    assert calls["quotes"] == 1
    assert calls["kline"] == 2
    assert snapshot["breadth"]["quote_pool_status"] == "refreshed"


def test_market_snapshot_uses_full_eligible_universe_instead_of_top_300(db, monkeypatch):
    settings = DummySettings()
    settings.market_candidate_count = 0
    quotes = []
    for index in range(320):
        pct = 5.0 if index < 300 else -4.5
        industry = "软件服务" if index < 300 else "煤炭"
        quote = {
            **_quote(f"{index + 1:06d}", "tushare", 10.0 + index * 0.01, pct=pct),
            "industry": industry,
        }
        quotes.append(
            {
                **quote,
                "source_count": 1,
                "raw_sources": ["tushare"],
                "quality_score": 80.0,
                "freshness_status": "fresh",
                "source_trace": {"raw_sources": ["tushare"], "source_count": 1, "field_sources": {}},
            }
        )
    market_pool._persist_unified_quotes(db, quotes)
    db.commit()

    monkeypatch.setattr(market_pool, "get_settings", lambda: settings)

    snapshot = market_pool.get_market_snapshot_from_pool(db, refresh=False)

    assert snapshot["sample_count"] == 320
    assert snapshot["down_count"] == 20
    assert snapshot["breadth"]["base_filter_eligible_count"] == 320
    assert snapshot["breadth"]["actual_scan_count"] == 320


def test_local_pool_status_summarizes_unified_quotes_and_bars(db, monkeypatch):
    settings = DummySettings()

    def fake_fetch_quotes(source, _settings):
        if source in {"tushare"}:
            return [_quote("000001", source, 10.00), _quote("000002", source, 20.00)]
        return []

    monkeypatch.setattr(market_pool, "_fetch_quotes", fake_fetch_quotes)
    market_pool._refresh_quote_pool(db, settings, active_sources=["tushare"])
    db.add(
        DailyKbar(
            code="000001",
            trade_date="2026-05-08",
            open=10,
            high=11,
            low=9,
            close=10.5,
            volume=1000,
            amount=10000,
            pct_chg=1,
            turnover_rate=2,
        )
    )
    db.commit()

    status = get_local_pool_status(db)

    assert status["summary"]["raw_quote_count"] == 4
    assert status["summary"]["active_unified_quote_count"] == 2
    assert status["summary"]["daily_bar_count"] == 1
    assert {row["source"] for row in status["quote_sources"]} == {"tushare"}
    assert status["unified_quotes"][0]["source_count"] == 1
    assert status["daily_bars"][0]["code"] == "000001"


def test_local_pool_status_reports_recommendation_readiness(db, monkeypatch):
    settings = DummySettings()

    def fake_fetch_quotes(source, _settings):
        if source in {"tushare"}:
            return [_quote("000001", source, 10.00), _quote("000002", source, 20.00)]
        return []

    monkeypatch.setattr(market_pool, "get_settings", lambda: settings)
    monkeypatch.setattr(market_pool, "_fetch_quotes", fake_fetch_quotes)
    market_pool._refresh_quote_pool(db, settings, active_sources=["tushare"])
    market_pool._persist_normalized_bars(db, _bars("000001", days=35))
    market_pool._persist_normalized_bars(db, _bars("000002", days=35))
    db.commit()

    status = get_local_pool_status(db)
    readiness = status["recommendation_readiness"]

    assert readiness["status"] == "ready"
    assert readiness["can_run_local_recommendation"] is True
    assert readiness["full_market_mode"] is False
    assert readiness["required_candidate_count"] == 2
    assert readiness["quote"]["candidate_count"] == 2
    assert readiness["quote"]["base_filter_eligible_count"] == 2
    assert readiness["kline"]["checked_count"] == 2
    assert readiness["kline"]["ready_count"] == 2
    assert readiness["scan"]["actual_scan_count"] == 2
    assert readiness["scan"]["skill_input_count"] == 2


def test_local_pool_readiness_treats_zero_candidate_count_as_full_market(db, monkeypatch):
    settings = DummySettings()
    settings.market_candidate_count = 0

    quotes = [_quote("000001", "tushare", 10.00), _quote("000002", "tushare", 20.00), _quote("000003", "tushare", 30.00)]
    market_pool._persist_unified_quotes(
        db,
        [
            {
                **quote,
                "source_count": 1,
                "raw_sources": ["tushare"],
                "quality_score": 80.0,
                "freshness_status": "fresh",
                "source_trace": {"raw_sources": ["tushare"], "source_count": 1, "field_sources": {}},
            }
            for quote in quotes
        ],
    )
    for quote in quotes:
        market_pool._persist_normalized_bars(db, _bars(quote["code"], days=35))
    db.commit()

    monkeypatch.setattr(market_pool, "get_settings", lambda: settings)

    status = get_local_pool_status(db)
    readiness = status["recommendation_readiness"]

    assert readiness["status"] == "ready"
    assert readiness["can_run_local_recommendation"] is True
    assert readiness["full_market_mode"] is True
    assert readiness["required_candidate_count"] == 3
    assert readiness["quote"]["available_count"] == 3
    assert readiness["quote"]["candidate_count"] == 3
    assert readiness["quote"]["base_filter_eligible_count"] == 3
    assert readiness["kline"]["checked_count"] == 3
    assert readiness["kline"]["ready_count"] == 3
    assert readiness["scan"]["actual_scan_count"] == 3
    assert readiness["scan"]["skill_input_count"] == 3
    assert "全市场模式" in readiness["messages"][0]


def test_latest_allowed_bar_date_uses_previous_session_before_close(monkeypatch):
    class FakeDateTime(datetime):
        @classmethod
        def now(cls):
            return cls(2026, 5, 13, 0, 30)

    monkeypatch.setattr(market_pool, "datetime", FakeDateTime)

    assert market_pool._latest_allowed_bar_date() == "2026-05-12"


def test_local_pool_status_reports_missing_kline_gap(db, monkeypatch):
    settings = DummySettings()

    def fake_fetch_quotes(source, _settings):
        if source in {"tushare"}:
            return [_quote("000001", source, 10.00), _quote("000002", source, 20.00)]
        return []

    monkeypatch.setattr(market_pool, "get_settings", lambda: settings)
    monkeypatch.setattr(market_pool, "_fetch_quotes", fake_fetch_quotes)
    market_pool._refresh_quote_pool(db, settings, active_sources=["tushare"])

    status = get_local_pool_status(db)
    readiness = status["recommendation_readiness"]

    assert readiness["status"] == "blocked"
    assert readiness["can_run_local_recommendation"] is False
    assert readiness["kline"]["missing_count"] == 2
    assert readiness["kline"]["examples"]


def test_local_pool_status_separates_pool_coverage_from_demo_scan_limit(db, monkeypatch):
    settings = DummySettings()
    settings.market_candidate_count = 1
    settings.market_candidate_lookahead_multiplier = 1
    quotes = [_quote("000001", "tushare", 10.00), _quote("000002", "tushare", 20.00), _quote("000003", "tushare", 30.00)]
    market_pool._persist_unified_quotes(
        db,
        [
            {
                **quote,
                "source_count": 1,
                "raw_sources": ["tushare"],
                "quality_score": 80.0,
                "freshness_status": "fresh",
                "source_trace": {"raw_sources": ["tushare"], "source_count": 1, "field_sources": {}},
            }
            for quote in quotes
        ],
    )
    for quote in quotes:
        market_pool._persist_normalized_bars(db, _bars(quote["code"], days=35))
    db.commit()

    monkeypatch.setattr(market_pool, "get_settings", lambda: settings)

    status = get_local_pool_status(db)

    assert status["summary"]["eligible_quote_count"] == 3
    assert status["summary"]["eligible_daily_bar_count"] == 3
    assert status["summary"]["daily_bar_missing_count"] == 0
    assert status["summary"]["daily_bar_short_history_count"] == 0
    assert status["summary"]["actual_recommend_scan_count"] == 1
    assert status["summary"]["skill_input_count"] == 1
    assert status["recommendation_readiness"]["scan"]["demo_limited"] is True


def test_local_pool_status_reports_short_history_separately(db, monkeypatch):
    settings = DummySettings()
    settings.market_candidate_count = 0
    quotes = [_quote("000001", "tushare", 10.00), _quote("000002", "tushare", 20.00)]
    market_pool._persist_unified_quotes(
        db,
        [
            {
                **quote,
                "source_count": 1,
                "raw_sources": ["tushare"],
                "quality_score": 80.0,
                "freshness_status": "fresh",
                "source_trace": {"raw_sources": ["tushare"], "source_count": 1, "field_sources": {}},
            }
            for quote in quotes
        ],
    )
    market_pool._persist_normalized_bars(db, _bars("000001", days=35))
    market_pool._persist_normalized_bars(db, _bars("000002", days=12))
    db.commit()

    monkeypatch.setattr(market_pool, "get_settings", lambda: settings)

    status = get_local_pool_status(db)

    assert status["summary"]["eligible_quote_count"] == 2
    assert status["summary"]["eligible_daily_bar_count"] == 1
    assert status["summary"]["daily_bar_short_history_count"] == 1
    assert status["summary"]["daily_bar_missing_count"] == 0
    assert status["summary"]["eligible_daily_bar_coverage_rate"] == 50.0
    assert status["recommendation_readiness"]["kline"]["missing_count"] == 0
    assert status["recommendation_readiness"]["kline"]["short_history_count"] == 1
    assert "短历史 1 只" in status["recommendation_readiness"]["messages"][1]


def test_local_pool_status_counts_low_liquidity_as_available_universe(db, monkeypatch):
    settings = DummySettings()
    settings.market_candidate_count = 0
    quotes = [
        _quote("000001", "tushare", 10.00),
        _low_liquidity_quote("000002", "tushare", 20.00),
    ]
    market_pool._persist_unified_quotes(
        db,
        [
            {
                **quote,
                "source_count": 1,
                "raw_sources": ["tushare"],
                "quality_score": 80.0,
                "freshness_status": "fresh",
                "source_trace": {"raw_sources": ["tushare"], "source_count": 1, "field_sources": {}},
            }
            for quote in quotes
        ],
    )
    for quote in quotes:
        market_pool._persist_normalized_bars(db, _bars(quote["code"], days=35))
    db.commit()

    monkeypatch.setattr(market_pool, "get_settings", lambda: settings)

    status = get_local_pool_status(db)
    filter_summary = status["summary"]["candidate_filter"]

    assert status["summary"]["eligible_quote_count"] == 2
    assert status["recommendation_readiness"]["quote"]["candidate_count"] == 2
    assert status["summary"]["eligible_daily_bar_count"] == 2
    assert filter_summary["high_liquidity_count"] == 1
    assert filter_summary["low_liquidity_count"] == 1
    assert filter_summary["excluded_count"] == 0


def test_local_pool_status_uses_quote_date_for_coverage_reference(db, monkeypatch):
    settings = DummySettings()
    settings.market_candidate_count = 0
    quote = _quote("000001", "tushare", 10.00)
    market_pool._persist_unified_quotes(
        db,
        [
            {
                **quote,
                "trade_date": "2026-05-13",
                "source_count": 1,
                "raw_sources": ["tushare"],
                "quality_score": 80.0,
                "freshness_status": "fresh",
                "source_trace": {"raw_sources": ["tushare"], "source_count": 1, "field_sources": {}},
            }
        ],
    )
    market_pool._persist_normalized_bars(db, _bars("000001", days=35, end=date(2026, 5, 13)))
    db.commit()

    monkeypatch.setattr(market_pool, "get_settings", lambda: settings)
    monkeypatch.setattr("app.services.data_sources._latest_allowed_bar_date", lambda today=None: "2026-05-15")

    status = get_local_pool_status(db)

    assert status["summary"]["coverage_trade_date"] == "2026-05-13"
    assert status["summary"]["eligible_daily_bar_count"] == 1
    assert status["summary"]["daily_bar_stale_count"] == 0
