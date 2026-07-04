import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.tables import (
    CapitalFlow,
    ConceptSector,
    ConceptSectorStock,
    DailyKbar,
    DataSourceConfig,
    DragonTiger,
    LimitUpBoard,
    NewsAnnouncement,
    StockQuote,
)
from app.services.data_sources import (
    _coverage_reference_date,
    enabled_sources,
    get_local_pool_status,
    sync_data_source_configs,
)
from app.services import market_pool
from app.services import market_pool_adapters


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
    news_sources = "tushare,eastmoney"
    data_provider = "tushare"
    tushare_token = ""


class NewsSettings:
    enable_news_fetch = True
    news_sources = "tushare,eastmoney"
    news_cache_ttl_seconds = 172800


class ConceptSettings:
    enable_concept_sector_fetch = True
    concept_sector_sources = "tushare"
    concept_member_fetch_timeout_seconds = 1.0


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
        "trade_date": "2026-05-27",
    }


def _codes(count: int, prefix: str = "09") -> list[str]:
    suffix_digits = max(1, 6 - len(prefix))
    modulus = 10 ** suffix_digits
    base = uuid.uuid4().int % modulus
    return [f"{prefix}{(base + index) % modulus:0{suffix_digits}d}" for index in range(count)]


def _set_quote_source_enabled(db, monkeypatch, enabled: bool) -> None:
    monkeypatch.setattr("app.services.data_sources.get_settings", lambda: DummySettings())
    sync_data_source_configs(db)
    row = db.get(DataSourceConfig, {"source": "tushare", "source_type": "quote"})
    assert row is not None
    row.enabled = enabled
    db.commit()


@pytest.fixture(autouse=True)
def _enabled_quote_source_by_default(request, monkeypatch):
    if "db" not in request.fixturenames:
        yield
        return
    db = request.getfixturevalue("db")
    _set_quote_source_enabled(db, monkeypatch, True)
    yield


def _persist_quotes(db, quotes: list[dict], trade_date: str = "2026-05-27") -> None:
    rows = []
    for quote in quotes:
        source = str(quote.get("source") or "tushare")
        rows.append(
            {
                **quote,
                "trade_date": quote.get("trade_date") or trade_date,
                "source_count": int(quote.get("source_count") or 1),
                "raw_sources": quote.get("raw_sources") or [source],
                "quality_score": float(quote.get("quality_score") or 88.0),
                "freshness_status": quote.get("freshness_status") or "fresh",
                "source_trace": quote.get("source_trace") or {
                    "raw_sources": [source],
                    "source_count": 1,
                    "field_sources": {},
                },
            }
        )
    market_pool._persist_stock_quotes(db, rows)


def _persist_bars(db, bars: list[dict]) -> None:
    market_pool._persist_kbars(db, bars)


def _bar_summaries(codes: list[str], bar_count: int = 35, latest_trade_date: str = "2026-05-27") -> dict[str, dict]:
    return {code: {"bar_count": bar_count, "latest_trade_date": latest_trade_date} for code in codes}


def _readiness(db, monkeypatch, settings: DummySettings, quotes: list[dict], summaries: dict[str, dict]):
    monkeypatch.setattr(market_pool, "get_settings", lambda: settings)
    return market_pool.local_pool_recommendation_readiness(
        db,
        cached_quotes=quotes,
        cached_bar_summaries=summaries,
    )


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


def test_refresh_news_for_codes_fetches_and_filters_items(monkeypatch):
    db = SimpleNamespace(commit=lambda: None)
    persisted: list[list[dict]] = []
    calls: list[tuple[str, str, str]] = []

    def fake_fetch_news(source, code, _settings, trade_date=None):
        calls.append((source, code, trade_date))
        suffix = "ann" if source == "tushare" else "news"
        return [
            {
                "source": source,
                "article_id": f"{code}_{suffix}",
                "code": code,
                "announce_date": "2026-05-23" if source == "tushare" else "2026-05-27",
                "title": "业绩预增公告" if source == "tushare" else "签约项目新闻",
                "category": "announcement" if source == "tushare" else "news",
                "sentiment": "positive",
                "is_risk_warning": False,
            },
            {
                "source": source,
                "article_id": f"{code}_{suffix}_future",
                "code": code,
                "announce_date": "2026-05-28",
                "title": "未来公告",
                "category": "announcement" if source == "tushare" else "news",
                "sentiment": "neutral",
                "is_risk_warning": False,
            },
        ]

    monkeypatch.setattr(market_pool, "get_settings", lambda: NewsSettings())
    monkeypatch.setattr(market_pool, "enabled_sources", lambda _db, _source_type, sources: sources)
    monkeypatch.setattr(market_pool, "_fetch_news", fake_fetch_news)
    monkeypatch.setattr(market_pool, "_persist_normalized_news", lambda _db, rows: persisted.append(rows))

    result = market_pool.refresh_news_for_codes(db, ["000001", "000002"], trade_date="2026-05-27")

    assert calls == [
        ("tushare", "000001", "2026-05-27"),
        ("eastmoney", "000001", "2026-05-27"),
        ("tushare", "000002", "2026-05-27"),
        ("eastmoney", "000002", "2026-05-27"),
    ]
    assert set(result) == {"000001", "000002"}
    assert result.checked_codes == ["000001", "000002"]
    assert all(len(items) == 2 for items in result.values())
    assert {row["article_id"] for batch in persisted for row in batch} == {
        "000001_ann",
        "000001_news",
        "000002_ann",
        "000002_news",
    }


def test_refresh_news_for_codes_tracks_checked_codes_without_failed_codes(monkeypatch):
    db = SimpleNamespace(commit=lambda: None)

    def fake_fetch_news(source, code, _settings, trade_date=None):
        if source == "tushare":
            raise RuntimeError("source timeout")
        return []

    monkeypatch.setattr(market_pool, "get_settings", lambda: NewsSettings())
    monkeypatch.setattr(market_pool, "enabled_sources", lambda _db, _source_type, sources: sources)
    monkeypatch.setattr(market_pool, "_fetch_news", fake_fetch_news)

    result = market_pool.refresh_news_for_codes(db, ["000001", "000002"], trade_date="2026-05-27")

    assert result == {}
    assert result.checked_codes == ["000001", "000002"]


def test_refresh_news_for_codes_excludes_code_when_all_sources_fail(monkeypatch):
    db = SimpleNamespace(commit=lambda: None)

    def fake_fetch_news(source, code, _settings, trade_date=None):
        if code == "000002":
            raise RuntimeError("source timeout")
        return []

    monkeypatch.setattr(market_pool, "get_settings", lambda: NewsSettings())
    monkeypatch.setattr(market_pool, "enabled_sources", lambda _db, _source_type, sources: sources)
    monkeypatch.setattr(market_pool, "_fetch_news", fake_fetch_news)

    result = market_pool.refresh_news_for_codes(db, ["000001", "000002"], trade_date="2026-05-27")

    assert result == {}
    assert result.checked_codes == ["000001"]


def test_auxiliary_adapters_forward_trade_date(monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_limit(source, _settings, trade_date=None):
        calls.append(("limit", trade_date))
        return [{"source": source, "trade_date": trade_date}]

    def fake_concept(source, _settings, trade_date=None):
        calls.append(("concept", trade_date))
        return [{"source": source, "trade_date": trade_date}]

    monkeypatch.setattr(market_pool_adapters, "_fetch_limit_up_board", fake_limit)
    monkeypatch.setattr(market_pool_adapters, "_fetch_concept_sectors", fake_concept)

    assert market_pool_adapters.fetch_limit_up_board_rows("tushare", object(), trade_date="2026-06-04")[0]["trade_date"] == "2026-06-04"
    assert market_pool_adapters.fetch_concept_sector_rows("tushare", object(), trade_date="2026-06-04")[0]["trade_date"] == "2026-06-04"
    assert calls == [("limit", "2026-06-04"), ("concept", "2026-06-04")]


def test_refresh_concept_sectors_times_out_member_fetch(monkeypatch):
    class FakeFuture:
        def cancel(self):
            return True

    class FakePool:
        def __init__(self, max_workers):
            self.max_workers = max_workers
            self.shutdown_args = None

        def submit(self, *_args, **_kwargs):
            return FakeFuture()

        def shutdown(self, **kwargs):
            self.shutdown_args = kwargs

    fake_pool = FakePool(max_workers=6)
    persisted: list[list[dict]] = []
    logs: list[tuple[str, float, int, int]] = []
    db = SimpleNamespace(
        scalar=lambda *_args, **_kwargs: 0,
        commit=lambda: None,
    )

    def fake_as_completed(_futures, timeout=None):
        raise TimeoutError()

    monkeypatch.setattr(market_pool, "get_settings", lambda: ConceptSettings())
    monkeypatch.setattr(market_pool, "enabled_sources", lambda _db, _source_type, sources: sources)
    monkeypatch.setattr(
        market_pool,
        "_refresh_aux_data",
        lambda *_args, **_kwargs: [
            {"sector_code": "885001", "sector_name": "机器人", "heat_rank": 1, "trade_date": "2026-05-27"}
        ],
    )
    monkeypatch.setattr(market_pool, "ThreadPoolExecutor", lambda max_workers: fake_pool)
    monkeypatch.setattr(market_pool, "as_completed", fake_as_completed)
    monkeypatch.setattr(market_pool, "_persist_concept_sector_stocks", lambda _db, rows: persisted.append(rows))
    monkeypatch.setattr(market_pool.logger, "warning", lambda msg, *args: logs.append((msg, *args)))

    rows = market_pool.refresh_concept_sectors(db, trade_date="2026-05-27")

    assert rows[0]["sector_code"] == "885001"
    assert persisted == []
    assert fake_pool.shutdown_args == {"wait": False, "cancel_futures": True}
    assert logs and "概念板块成分股拉取超过" in logs[0][0]


def test_load_auxiliary_for_stocks_uses_same_trade_date_rows_after_ttl(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            CapitalFlow.__table__,
            ConceptSector.__table__,
            ConceptSectorStock.__table__,
            DragonTiger.__table__,
            LimitUpBoard.__table__,
            NewsAnnouncement.__table__,
        ],
    )
    Session = sessionmaker(bind=engine)
    db = Session()
    monkeypatch.setattr(market_pool, "_load_auction_for_stocks", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(market_pool, "_load_pledge_for_stocks", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(market_pool, "_load_broker_recs_for_stocks", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(market_pool, "_load_share_float_upcoming", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(market_pool, "_load_margin_detail_for_stocks", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(market_pool, "_load_block_trade_for_stocks", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(market_pool, "_load_hsgt_top10_for_stocks", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(market_pool, "_load_hsgt_flow", lambda *_args, **_kwargs: None)
    stale_created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=3)
    code = "000001"
    trade_date = "2026-05-27"
    sector_code = "885001"
    try:
        db.add_all(
            [
                CapitalFlow(
                    code=code,
                    trade_date=trade_date,
                    main_net_inflow_yi=1.2,
                    super_large_net_inflow_yi=0.7,
                    large_net_inflow_yi=0.5,
                    created_at=stale_created_at,
                ),
                DragonTiger(
                    code=code,
                    trade_date=trade_date,
                    name="平安银行",
                    net_amount_yi=0.8,
                    created_at=stale_created_at,
                ),
                NewsAnnouncement(
                    source="eastmoney",
                    article_id="same_trade_date_stale_news",
                    code=code,
                    announce_date=trade_date,
                    title="同日已落库公告",
                    created_at=stale_created_at,
                ),
                ConceptSector(
                    sector_code=sector_code,
                    trade_date=trade_date,
                    sector_name="金融科技",
                    heat_rank=3,
                    created_at=stale_created_at,
                ),
                ConceptSectorStock(
                    sector_code=sector_code,
                    code=code,
                    trade_date=trade_date,
                    name="平安银行",
                    created_at=stale_created_at,
                ),
            ]
        )
        db.commit()

        aux = market_pool.load_auxiliary_for_stocks(db, [code], trade_date=trade_date)

        assert aux["capital_flows"][code]["main_net_inflow_yi"] == 1.2
        assert aux["dragon_tigers"][code]["net_amount_yi"] == 0.8
        assert aux["news"][code][0]["title"] == "同日已落库公告"
        assert aux["concept_sectors"][code][0]["sector_name"] == "金融科技"
        assert aux["coverage"]["dragon_tiger_trade_date_covered"] is True
        assert aux["coverage"]["concept_member_trade_date_covered"] is True
        assert aux["coverage"]["capital_flow_checked_codes"] == [code]
    finally:
        db.close()


def test_refresh_quote_pool_persists_raw_and_unified_quotes(db, monkeypatch):
    settings = DummySettings()
    codes = _codes(2)

    def fake_fetch_quotes(source, _settings):
        return [_quote(codes[0], source, 10.00), _quote(codes[1], source, 20.00)]

    monkeypatch.setattr(market_pool, "get_settings", lambda: settings)
    monkeypatch.setattr(market_pool, "_fetch_quotes", fake_fetch_quotes)

    quotes, meta = market_pool.refresh_quote_pool(db)

    assert meta["status"] == "refreshed"
    assert len(quotes) == 2
    assert {quote["source_count"] for quote in quotes} == {1}
    stored = db.scalar(select(StockQuote).where(StockQuote.source == "tushare", StockQuote.code == codes[0]))
    assert stored is not None
    unified = stored
    assert unified is not None
    assert unified.source_count == 1
    assert unified.quality_score > 80


def test_quote_pool_records_no_conflicts_with_single_source():
    settings = DummySettings()

    merged = market_pool._merge_quote_rows(
        "000001",
        [{**_quote("000001", "tushare", 10.00, pct=3.0), "trade_date": "2026-05-27"}],
        ["tushare"],
        settings,
    )

    assert merged["source_count"] == 1
    assert not merged["conflicts"]
    assert merged["quality_score"] >= 90
    assert merged["trade_date"] == "2026-05-27"


def test_dedupe_latest_quote_rows_keeps_latest_trade_date_per_code():
    rows = [
        SimpleNamespace(code="000001", trade_date="2026-05-22", snapped_at="2026-05-24T15:29:39", quality_score=80.0),
        SimpleNamespace(code="000001", trade_date="2026-05-26", snapped_at="2026-05-26T15:40:06", quality_score=70.0),
        SimpleNamespace(code="000002", trade_date="2026-05-26", snapped_at="2026-05-26T15:40:06", quality_score=90.0),
    ]

    deduped = market_pool._dedupe_latest_quote_rows(rows)

    assert [(row.code, row.trade_date) for row in deduped] == [
        ("000002", "2026-05-26"),
        ("000001", "2026-05-26"),
    ]


def test_persist_stock_quotes_uses_bulk_upsert_and_batch_inactivation():
    executed = []

    class FakeDB:
        def execute(self, stmt):
            executed.append(stmt)
            return None

        def scalar(self, stmt):
            raise AssertionError("bulk upsert path 不应逐条 scalar 查重")

        def add(self, obj):
            raise AssertionError("bulk upsert path 不应逐条 add ORM 实体")

    market_pool._persist_stock_quotes(
        FakeDB(),
        [
            {
                **_quote("000001", "tushare", 10.0),
                "trade_date": "2026-05-27",
                "raw_sources": ["tushare"],
                "source_count": 1,
                "quality_score": 88.0,
                "freshness_status": "fresh",
            },
            {
                **_quote("000002", "tushare", 20.0),
                "trade_date": "2026-05-27",
                "raw_sources": ["tushare"],
                "source_count": 1,
                "quality_score": 90.0,
                "freshness_status": "fresh",
            },
            {
                **_quote("000003", "tushare", 30.0),
                "trade_date": "2026-05-27",
                "raw_sources": ["tushare"],
                "source_count": 1,
                "quality_score": 92.0,
                "freshness_status": "fresh",
            },
        ],
        batch_size=2,
    )

    assert len(executed) == 3
    rendered = [str(stmt) for stmt in executed]
    assert rendered[0].startswith("UPDATE stock_quotes")
    assert "INSERT INTO stock_quotes" in rendered[1]
    assert "INSERT INTO stock_quotes" in rendered[2]


def test_persist_stock_quotes_inactivates_stale_active_codes_from_prior_refresh(db):
    old_code, new_code = _codes(2)
    old_trade_date = "2026-05-22"
    new_trade_date = "2026-05-27"
    db.add(
        StockQuote(
            code=old_code,
            trade_date=old_trade_date,
            name=f"测试{old_code}",
            price=10.0,
            pct=3.0,
            volume=1000000,
            amount_yi=8.0,
            turnover_rate=3.0,
            industry="软件服务",
            source="tushare",
            source_count=1,
            quality_score=88.0,
            freshness_status="fresh",
            snapped_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
    )
    db.flush()

    _persist_quotes(
        db,
        [
            {
                **_quote(new_code, "tushare", 20.0),
                "trade_date": new_trade_date,
                "raw_sources": ["tushare"],
                "source_count": 1,
                "quality_score": 90.0,
                "freshness_status": "fresh",
            }
        ],
        trade_date=new_trade_date,
    )

    old_row = db.scalar(select(StockQuote).where(StockQuote.code == old_code, StockQuote.trade_date == old_trade_date))
    new_row = db.scalar(select(StockQuote).where(StockQuote.code == new_code, StockQuote.trade_date == new_trade_date))
    assert old_row.freshness_status == "inactive"
    assert new_row.freshness_status == "fresh"


def test_load_cached_quotes_allows_realtime_quote_date_before_bar_close(db, monkeypatch):
    code = _codes(1, prefix="91")[0]
    db.add(
        StockQuote(
            code=code,
            trade_date="2026-06-10",
            name=f"测试{code}",
            price=10.0,
            pct=2.0,
            volume=1000000,
            amount_yi=5.0,
            turnover_rate=3.0,
            industry="软件服务",
            source="tushare",
            source_count=1,
            quality_score=90.0,
            freshness_status="fresh",
            snapped_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
    )
    db.flush()
    monkeypatch.setattr(market_pool, "_latest_allowed_bar_date", lambda today=None: "2026-06-09")
    monkeypatch.setattr(market_pool, "_latest_allowed_quote_date", lambda today=None: "2026-06-10")

    quotes = market_pool._load_cached_quotes(db, skip_metrics=True)

    loaded = [quote for quote in quotes if quote["code"] == code]
    assert loaded
    assert loaded[0]["trade_date"] == "2026-06-10"


def test_load_or_refresh_quotes_uses_fresh_cache(db, monkeypatch):
    settings = DummySettings()
    codes = _codes(2)
    calls = {"fetch": 0}

    def fake_fetch_quotes(source, _settings):
        calls["fetch"] += 1
        return [_quote(codes[0], source, 10.00), _quote(codes[1], source, 20.00)]

    monkeypatch.setattr(market_pool, "_fetch_quotes", fake_fetch_quotes)
    monkeypatch.setattr(market_pool, "_latest_allowed_quote_date", lambda today=None: "2026-05-27")

    first, first_meta = market_pool._load_or_refresh_quotes(db, settings)
    second, second_meta = market_pool._load_or_refresh_quotes(db, settings)

    assert first_meta["status"] == "refreshed"
    assert second_meta["status"] == "fresh_cache"
    assert len(first) == len(second) == 2
    assert calls["fetch"] == 1


def test_load_or_refresh_quotes_refreshes_when_cached_trade_date_lags(db, monkeypatch):
    settings = DummySettings()
    stale_code, fresh_code = _codes(2)
    _persist_quotes(
        db,
        [
            {
                **_quote(stale_code, "tushare", 10.0),
                "trade_date": "2026-06-09",
                "raw_sources": ["tushare"],
                "source_count": 1,
                "quality_score": 90.0,
                "freshness_status": "fresh",
            }
        ],
        trade_date="2026-06-09",
    )
    db.flush()
    calls = {"fetch": 0}

    def fake_fetch_quotes(source, _settings):
        calls["fetch"] += 1
        return [{**_quote(fresh_code, source, 20.0), "trade_date": "2026-06-10"}]

    monkeypatch.setattr(market_pool, "_fetch_quotes", fake_fetch_quotes)
    monkeypatch.setattr(market_pool, "_latest_allowed_quote_date", lambda today=None: "2026-06-10")

    quotes, meta = market_pool._load_or_refresh_quotes(db, settings)

    assert meta["status"] == "refreshed"
    assert calls["fetch"] == 1
    assert {quote["trade_date"] for quote in quotes} == {"2026-06-10"}


def test_get_market_universe_from_pool_backfills_latest_quote_trade_date_when_refreshing(monkeypatch):
    settings = DummySettings()
    db = SimpleNamespace()
    quotes = [
        {
            **_quote("000001", "tushare", 10.0),
            "trade_date": "2026-05-27",
            "raw_sources": ["tushare"],
            "source_count": 1,
            "quality_score": 90.0,
            "freshness_status": "fresh",
        }
    ]
    backfill_calls = []

    monkeypatch.setattr(market_pool, "get_settings", lambda: settings)
    monkeypatch.setattr(market_pool, "_load_or_refresh_quotes", lambda _db, _settings: (quotes, {"status": "refreshed", "errors": []}))
    monkeypatch.setattr(market_pool, "_eligible_market_quotes", lambda rows: rows)
    monkeypatch.setattr(market_pool, "_candidate_filter_summary", lambda rows: {"total_count": len(rows)})
    monkeypatch.setattr(market_pool, "_candidate_recall_plan", lambda total, eligible, _settings: {
        "full_market_mode": True,
        "explicit_scan_limit": 0,
        "lookahead_multiplier": 4,
        "broad_recall_count": eligible,
        "scan_target_count": eligible,
        "broad_recall_limited": False,
    })
    monkeypatch.setattr(market_pool, "_select_recall_candidates", lambda rows, _count: rows)
    monkeypatch.setattr(market_pool, "_latest_allowed_bar_date", lambda today=None: "2026-05-27")
    monkeypatch.setattr(
        market_pool,
        "_load_cached_bars_summary",
        lambda _db, _codes, _min_bar_count: {
            "000001": {"bar_count": 35, "latest_trade_date": "2026-05-26"}
        },
    )
    monkeypatch.setattr(
        market_pool,
        "_backfill_latest_trade_date_bars",
        lambda _db, candidates, _settings, target_trade_date: backfill_calls.append((len(candidates), target_trade_date)) or 1,
    )
    monkeypatch.setattr(
        market_pool,
        "_load_or_refresh_bars",
        lambda _db, quote, _settings: (_bars(quote["code"], 35, end=date(2026, 5, 27)), {"selected_source": "daily_bars_cache", "freshness_status": "refreshed"}, []),
    )
    monkeypatch.setattr(
        market_pool,
        "_build_quote_snapshot",
        lambda *_args, **_kwargs: {
            "snapshot_id": "snapshot_test",
            "trade_date": "2026-05-27",
            "sample_count": 1,
            "up_count": 1,
            "down_count": 0,
            "limit_up_count": 0,
            "limit_down_count": 0,
            "main_industries": ["软件服务"],
            "breadth": {},
        },
    )
    monkeypatch.setattr(
        market_pool,
        "_build_snapshot",
        lambda **kwargs: {
            "snapshot_id": "snapshot_test",
            "trade_date": "2026-05-27",
            "sample_count": len(kwargs["stocks"]),
            "up_count": 1,
            "down_count": 0,
            "limit_up_count": 0,
            "limit_down_count": 0,
            "main_industries": ["软件服务"],
            "breadth": {},
        },
    )
    monkeypatch.setattr(market_pool, "_apply_relative_features", lambda _stocks: None)

    stocks, snapshot = market_pool.get_market_universe_from_pool(db, refresh=True)

    assert len(stocks) == 1
    assert backfill_calls == [(1, "2026-05-27")]
    assert snapshot["breadth"]["latest_trade_date_backfill_count"] == 1
    assert snapshot["breadth"]["latest_quote_trade_date"] == "2026-05-27"


def test_get_market_universe_does_not_backfill_current_realtime_quote_date(monkeypatch):
    settings = DummySettings()
    db = SimpleNamespace()
    quotes = [
        {
            **_quote("000001", "tushare", 10.0),
            "trade_date": "2026-06-10",
            "raw_sources": ["tushare"],
            "source_count": 1,
            "quality_score": 90.0,
            "freshness_status": "fresh",
        }
    ]
    backfill_calls = []

    monkeypatch.setattr(market_pool, "get_settings", lambda: settings)
    monkeypatch.setattr(market_pool, "_load_or_refresh_quotes", lambda _db, _settings: (quotes, {"status": "refreshed", "errors": []}))
    monkeypatch.setattr(market_pool, "_eligible_market_quotes", lambda rows: rows)
    monkeypatch.setattr(market_pool, "_candidate_filter_summary", lambda rows: {"total_count": len(rows)})
    monkeypatch.setattr(market_pool, "_candidate_recall_plan", lambda total, eligible, _settings: {
        "full_market_mode": True,
        "explicit_scan_limit": 0,
        "lookahead_multiplier": 4,
        "broad_recall_count": eligible,
        "scan_target_count": eligible,
        "broad_recall_limited": False,
    })
    monkeypatch.setattr(market_pool, "_select_recall_candidates", lambda rows, _count: rows)
    monkeypatch.setattr(market_pool, "_latest_allowed_quote_date", lambda today=None: "2026-06-10")
    monkeypatch.setattr(market_pool, "_latest_allowed_bar_date", lambda today=None: today.isoformat() if today else "2026-06-10")
    monkeypatch.setattr(
        market_pool,
        "_load_cached_bars_summary",
        lambda _db, _codes, _min_bar_count: {
            "000001": {"bar_count": 35, "latest_trade_date": "2026-06-09"}
        },
    )
    monkeypatch.setattr(
        market_pool,
        "_backfill_latest_trade_date_bars",
        lambda _db, candidates, _settings, target_trade_date: backfill_calls.append((len(candidates), target_trade_date)) or 1,
    )
    monkeypatch.setattr(
        market_pool,
        "_load_or_refresh_bars",
        lambda _db, quote, _settings: (_bars(quote["code"], 35, end=date(2026, 6, 9)), {"selected_source": "daily_bars_cache", "freshness_status": "fresh_cache"}, []),
    )
    monkeypatch.setattr(
        market_pool,
        "_build_quote_snapshot",
        lambda *_args, **_kwargs: {
            "snapshot_id": "snapshot_test",
            "trade_date": "2026-06-10",
            "sample_count": 1,
            "up_count": 1,
            "down_count": 0,
            "limit_up_count": 0,
            "limit_down_count": 0,
            "main_industries": ["软件服务"],
            "breadth": {},
        },
    )
    monkeypatch.setattr(
        market_pool,
        "_build_snapshot",
        lambda **kwargs: {
            "snapshot_id": "snapshot_test",
            "trade_date": "2026-06-10",
            "sample_count": len(kwargs["stocks"]),
            "up_count": 1,
            "down_count": 0,
            "limit_up_count": 0,
            "limit_down_count": 0,
            "main_industries": ["软件服务"],
            "breadth": {},
        },
    )
    monkeypatch.setattr(market_pool, "_apply_relative_features", lambda _stocks: None)

    stocks, snapshot = market_pool.get_market_universe_from_pool(db, refresh=True)

    assert len(stocks) == 1
    assert backfill_calls == []
    assert snapshot["breadth"]["latest_trade_date_backfill_count"] == 0
    assert snapshot["breadth"]["latest_quote_trade_date"] == "2026-06-10"


def test_backfill_latest_trade_date_bars_builds_from_quotes_without_tushare(monkeypatch):
    settings = DummySettings()
    persisted_batches: list[list[dict]] = []

    class FakeScalarResult:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return list(self._rows)

    class FakeDB:
        def scalars(self, _stmt):
            rows = []
            for bar in _bars("000001", days=34, end=date(2026, 5, 26)):
                rows.append(
                    SimpleNamespace(
                        code=bar["code"],
                        trade_date=bar["trade_date"],
                        open=bar["open"],
                        high=bar["high"],
                        low=bar["low"],
                        close=bar["close"],
                        volume=bar["volume"],
                        amount=bar["amount"],
                        pct_chg=bar["pct_chg"],
                        turnover_rate=bar["turnover_rate"],
                    )
                )
            return FakeScalarResult(rows)

        def commit(self):
            return None

    db = FakeDB()

    def unexpected_retry(*_args, **_kwargs):
        raise AssertionError("回补最新交易日日线时不应再访问 Tushare")

    monkeypatch.setattr(market_pool, "_call_tushare_with_retry", unexpected_retry, raising=False)
    monkeypatch.setattr(market_pool, "_persist_kbars", lambda _db, bars: persisted_batches.append(bars))

    count = market_pool._backfill_latest_trade_date_bars(
        db,
        [
            {
                **_quote("000001", "tushare", 12.3, pct=2.6),
                "trade_date": "2026-05-27",
                "open": 12.0,
                "high": 12.5,
                "low": 11.9,
                "turnover_rate": 5.1,
                "amount_yi": 9.8,
                "volume": 345678,
            }
        ],
        settings,
        "2026-05-27",
    )

    assert count == 1
    assert len(persisted_batches) == 1
    latest = persisted_batches[0][0]
    assert latest["close"] == pytest.approx(12.3)
    assert latest["amount"] == pytest.approx(980000000.0)
    assert latest["ma5"] is not None
    assert latest["ma20"] is not None
    assert latest["volume_ratio_5d"] is not None
    assert latest["bias_ma5"] is not None
    assert latest["breakout20"] in {0, 1}
    assert latest["max_dd20"] is not None


def test_persist_kbars_writes_computed_indicator_columns():
    executed = []

    class FakeDB:
        def execute(self, stmt):
            executed.append(stmt)
            return None

    market_pool._persist_kbars(
        FakeDB(),
        [
            {
                **_bars("000001", days=1, end=date(2026, 5, 27))[0],
                "ma5": 11.1,
                "ma10": 10.8,
                "ma20": 10.2,
                "ma60": 9.7,
                "ma250": 8.5,
                "volume_ratio_5d": 1.35,
                "bias_ma5": 2.1,
                "bias_ma10": 3.2,
                "bias_ma20": 4.3,
                "breakout20": 1,
                "breakout60": 0,
                "upper_shadow_count20": 2,
                "max_dd20": -4.5,
                "max_dd60": -8.1,
                "macd_dif": 0.2,
                "macd_dea": 0.1,
                "macd_bar": 0.2,
            }
        ],
    )

    assert len(executed) == 1
    rendered = str(executed[0])
    assert "ma5" in rendered
    assert "ma60" in rendered
    assert "ma250" in rendered
    assert "bias_5" in rendered
    assert "vol_ratio_5" in rendered
    assert "breakout20" in rendered
    assert "upper_shadow_count20" in rendered
    assert "max_dd60" in rendered


def test_cached_quotes_are_enriched_with_local_bar_metrics(db, monkeypatch):
    code = _codes(1)[0]
    trade_date = "2026-02-03"
    _persist_quotes(
        db,
        [
            {
                **_quote(code, "tushare", 16.0),
                "trade_date": trade_date,
                "source_count": 1,
                "raw_sources": ["tushare"],
                "quality_score": 80.0,
                "freshness_status": "fresh",
                "chg60": 0.0,
                "ytd_chg": 0.0,
                "source_trace": {"raw_sources": ["tushare"], "source_count": 1, "field_sources": {}},
            }
        ],
        trade_date=trade_date,
    )
    bars = []
    for index in range(65):
        bar_date = date(2025, 12, 1) + timedelta(days=index)
        bars.append(
            {
                "code": code,
                "trade_date": bar_date.isoformat(),
                "open": 10 + index * 0.1,
                "high": 10 + index * 0.1,
                "low": 10 + index * 0.1,
                "close": 10 + index * 0.1,
                "volume": 1000,
                "amount": 10000,
                "pct_chg": 1,
                "turnover_rate": 2,
                "ma5": 14.1,
                "ma10": 13.8,
                "ma20": 13.1,
            }
        )
    _persist_bars(db, bars)
    db.commit()

    monkeypatch.setattr(market_pool, "_latest_allowed_bar_date", lambda today=None: "2026-02-03")

    quotes = market_pool._load_cached_quotes(db)
    quote = next(item for item in quotes if item["code"] == code)

    assert quote["chg60"] != 0
    assert quote["ytd_chg"] != 0
    assert quote["ma5"] == 14.1
    assert quote["ma10"] == 13.8
    assert quote["ma20"] == 13.1
    assert quote["source_trace"]["field_sources"]["chg60"] == ["daily_bars_cache"]
    assert quote["source_trace"]["field_sources"]["ma5"] == ["daily_bars_cache"]


def test_cached_quotes_ignore_inactive_rows_even_on_allowed_trade_date(db, monkeypatch):
    active_code, inactive_code = _codes(2)
    trade_date = "2026-05-27"
    db.add_all(
        [
            StockQuote(
                code=active_code,
                trade_date=trade_date,
                name=f"测试{active_code}",
                price=10.0,
                pct=3.0,
                volume=1000000,
                amount_yi=8.0,
                turnover_rate=3.0,
                industry="软件服务",
                source="tushare",
                source_count=1,
                quality_score=88.0,
                freshness_status="fresh",
                snapped_at=datetime.now(timezone.utc).replace(tzinfo=None),
            ),
            StockQuote(
                code=inactive_code,
                trade_date=trade_date,
                name=f"测试{inactive_code}",
                price=20.0,
                pct=4.0,
                volume=1000000,
                amount_yi=8.0,
                turnover_rate=3.0,
                industry="软件服务",
                source="tushare",
                source_count=1,
                quality_score=99.0,
                freshness_status="inactive",
                snapped_at=datetime.now(timezone.utc).replace(tzinfo=None),
            ),
        ]
    )
    db.commit()
    monkeypatch.setattr(market_pool, "_latest_allowed_bar_date", lambda today=None: trade_date)

    quotes = market_pool._load_cached_quotes(db, skip_metrics=True)
    codes = {item["code"] for item in quotes}

    assert active_code in codes
    assert inactive_code not in codes


def test_load_or_refresh_quotes_does_not_use_stale_cache_after_refresh_error(db, monkeypatch):
    settings = DummySettings()
    codes = _codes(2)
    _persist_quotes(
        db,
        [
            market_pool._merge_quote_rows(
                codes[0],
                [_quote(codes[0], "tushare", 10.00)],
                ["tushare"],
                settings,
            ),
            market_pool._merge_quote_rows(
                codes[1],
                [_quote(codes[1], "tushare", 20.00)],
                ["tushare"],
                settings,
            ),
        ],
    )
    db.commit()
    stale_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=settings.market_quote_cache_ttl_seconds + 1)
    for row in db.scalars(select(StockQuote).where(StockQuote.code.in_(codes))).all():
        row.snapped_at = stale_at
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
    _set_quote_source_enabled(db, monkeypatch, False)
    calls = []

    def fake_fetch_quotes(source, _settings):
        calls.append(source)
        return [_quote("000001", source, 10.00), _quote("000002", source, 20.00)]

    monkeypatch.setattr(market_pool, "_fetch_quotes", fake_fetch_quotes)
    monkeypatch.setattr(market_pool, "_load_cached_quotes", lambda *_args, **_kwargs: [])

    with pytest.raises(market_pool.MarketDataError, match="本地数据池没有可用缓存"):
        market_pool._load_or_refresh_quotes(db, settings)
    assert calls == []


def test_all_quote_sources_disabled_raises_without_fetching(db, monkeypatch):
    settings = DummySettings()
    _set_quote_source_enabled(db, monkeypatch, False)

    def fake_fetch_quotes(source, _settings):
        raise AssertionError(f"disabled source should not be fetched: {source}")

    monkeypatch.setattr(market_pool, "_fetch_quotes", fake_fetch_quotes)
    monkeypatch.setattr(market_pool, "_load_cached_quotes", lambda *_args, **_kwargs: [])

    try:
        market_pool._load_or_refresh_quotes(db, settings)
    except market_pool.MarketDataError as exc:
        assert "本地数据池没有可用缓存" in str(exc)
    else:
        raise AssertionError("expected MarketDataError")


def test_all_quote_sources_disabled_can_use_cached_quotes_without_fetching(db, monkeypatch):
    settings = DummySettings()
    _set_quote_source_enabled(db, monkeypatch, False)
    cached = [_quote("000001", "tushare", 10.00), _quote("000002", "tushare", 20.00)]

    def fake_fetch_quotes(source, _settings):
        raise AssertionError(f"disabled source should not be fetched: {source}")

    monkeypatch.setattr(market_pool, "_fetch_quotes", fake_fetch_quotes)
    monkeypatch.setattr(market_pool, "_load_cached_quotes", lambda *_args, **_kwargs: cached)

    quotes, meta = market_pool._load_or_refresh_quotes(db, settings)

    assert quotes == cached
    assert meta["status"] == "local_pool_readonly_source_disabled"
    assert "当前仅使用本地缓存" in meta["errors"][0]


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
    monkeypatch.setattr(market_pool, "_load_cached_quotes", lambda *_args, **_kwargs: [])

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
    code = _codes(1)[0]

    merged = market_pool._merge_quote_rows(
        code,
        [_quote(code, "tushare", 10.00)],
        ["tushare"],
        settings,
    )

    monkeypatch.setattr(market_pool, "get_settings", lambda: settings)
    monkeypatch.setattr(
        market_pool,
        "_load_quotes_from_pool",
        lambda *_args, **_kwargs: ([merged], {"status": "local_pool_readonly", "errors": []}),
    )
    monkeypatch.setattr(market_pool, "_load_cached_bars_batch", lambda *_args, **_kwargs: {code: _bars(code, days=35, end=date(2026, 5, 1))})
    monkeypatch.setattr(market_pool, "_latest_allowed_bar_date", lambda today=None: "2026-05-27")

    try:
        market_pool.get_market_universe_from_pool(db, refresh=False)
    except market_pool.MarketDataError as exc:
        assert "本地日K未覆盖报价日期" in str(exc)
    else:
        raise AssertionError("expected MarketDataError")


def test_current_realtime_quote_accepts_previous_trade_day_bars(monkeypatch):
    quote = {**_quote("000001", "tushare", 10.00), "trade_date": "2026-06-10"}
    bars = _bars("000001", days=35, end=date(2026, 6, 9))

    monkeypatch.setattr(market_pool, "_latest_allowed_quote_date", lambda today=None: "2026-06-10")

    assert market_pool._required_bar_date_for_quote(quote) == "2026-06-09"
    assert market_pool._bars_cover_quote_date(bars, quote) is True


def test_readiness_treats_current_realtime_quote_one_bar_day_gap_as_ready(db, monkeypatch):
    settings = DummySettings()
    codes = _codes(2)
    quotes = [
        {**_quote(codes[0], "tushare", 10.00), "trade_date": "2026-06-10"},
        {**_quote(codes[1], "tushare", 20.00), "trade_date": "2026-06-10"},
    ]
    summaries = _bar_summaries(codes, latest_trade_date="2026-06-09")

    monkeypatch.setattr(market_pool, "_latest_allowed_quote_date", lambda today=None: "2026-06-10")

    readiness = _readiness(db, monkeypatch, settings, quotes, summaries)

    assert readiness["status"] == "ready"
    assert readiness["can_run_local_recommendation"] is True
    assert readiness["has_missing_data"] is False
    assert readiness["kline"]["ready_count"] == 2
    assert readiness["kline"]["stale_count"] == 0


def test_get_market_universe_local_pool_preferred_refreshes_when_pool_is_empty(db, monkeypatch):
    settings = DummySettings()
    codes = _codes(2)
    calls = {"quotes": 0, "kline": 0}

    def fake_fetch_quotes(source, _settings):
        calls["quotes"] += 1
        return [_quote(codes[0], source, 10.00), _quote(codes[1], source, 20.00)]

    def fake_fetch_kline(_source, quote, _settings, **__):
        calls["kline"] += 1
        return _bars(quote["code"], days=35, end=date(2026, 5, 27))

    monkeypatch.setattr(market_pool, "get_settings", lambda: settings)
    monkeypatch.setattr(market_pool, "_fetch_quotes", fake_fetch_quotes)
    monkeypatch.setattr(market_pool, "_fetch_kline", fake_fetch_kline)
    monkeypatch.setattr(market_pool, "_incremental_kline_start", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(market_pool, "_latest_allowed_bar_date", lambda today=None: "2026-05-27")

    stocks, snapshot = market_pool.get_market_universe_from_pool(db, refresh=True)

    assert len(stocks) == 2
    assert calls["quotes"] == 1
    assert calls["kline"] == 2
    assert snapshot["breadth"]["quote_pool_status"] == "refreshed"


def test_load_or_refresh_bars_merges_incremental_bars_with_cached_history(monkeypatch):
    settings = DummySettings()
    quote = {
        **_quote("000001", "tushare", 10.00),
        "trade_date": "2026-05-27",
    }
    db = SimpleNamespace(commit=lambda: None)
    store = {"000001": _bars("000001", days=25, end=date(2026, 5, 20))}

    def fake_load_cached_bars(_db, code, limit):
        return list(store.get(code, []))[-limit:]

    def fake_refresh_bars_for_quote(_db, _quote, _settings):
        return _bars("000001", days=12, end=date(2026, 5, 27)), "tushare", []

    def fake_persist_kbars(_db, bars):
        merged = {row["trade_date"]: row for row in store["000001"]}
        for row in bars:
            merged[row["trade_date"]] = row
        store["000001"] = [merged[trade_date] for trade_date in sorted(merged)]

    monkeypatch.setattr(market_pool, "_load_cached_bars", fake_load_cached_bars)
    monkeypatch.setattr(market_pool, "_refresh_bars_for_quote", fake_refresh_bars_for_quote)
    monkeypatch.setattr(market_pool, "_persist_kbars", fake_persist_kbars)
    monkeypatch.setattr(market_pool, "_latest_allowed_bar_date", lambda today=None: "2026-05-27")

    bars, trace, errors = market_pool._load_or_refresh_bars(db, quote, settings)

    assert len(bars) == 32
    assert bars[-1]["trade_date"] == "2026-05-27"
    assert trace == {"selected_source": "tushare", "freshness_status": "refreshed"}
    assert errors == []


def test_incremental_kline_start_forces_full_backfill_when_cached_history_is_short(db):
    settings = DummySettings()
    short_code, ready_code = _codes(2)
    _persist_bars(db, _bars(short_code, days=8, end=date(2026, 5, 27)))
    _persist_bars(db, _bars(ready_code, days=35, end=date(2026, 5, 27)))
    db.flush()

    assert market_pool._incremental_kline_start(db, short_code, settings) is None
    assert market_pool._incremental_kline_start(db, ready_code, settings) == "20260517"


def test_market_snapshot_uses_full_eligible_universe_instead_of_top_300(db, monkeypatch):
    settings = DummySettings()
    settings.market_candidate_count = 0
    codes = _codes(320)
    quotes = []
    for index in range(320):
        pct = 5.0 if index < 300 else -4.5
        industry = "软件服务" if index < 300 else "煤炭"
        quote = {
            **_quote(codes[index], "tushare", 10.0 + index * 0.01, pct=pct),
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
    monkeypatch.setattr(market_pool, "get_settings", lambda: settings)
    monkeypatch.setattr(
        market_pool,
        "_load_quotes_from_pool",
        lambda *_args, **_kwargs: (quotes, {"status": "local_pool_readonly", "errors": [], "source_count": 1, "sources": ["tushare"]}),
    )

    snapshot = market_pool.get_market_snapshot_from_pool(db, refresh=False)

    assert snapshot["sample_count"] == 320
    assert snapshot["down_count"] == 20
    assert snapshot["breadth"]["base_filter_eligible_count"] == 320
    assert snapshot["breadth"]["actual_scan_count"] == 320


def test_local_pool_status_summarizes_unified_quotes_and_bars(db, monkeypatch):
    status = get_local_pool_status(db)

    assert "summary" in status
    assert "recommendation_readiness" in status
    assert "quote_sources" in status
    assert "unified_quotes" in status
    assert "daily_bars" in status
    assert isinstance(status["summary"]["active_unified_quote_count"], int)
    assert isinstance(status["summary"]["daily_bar_count"], int)


def test_local_pool_status_reports_recommendation_readiness(db, monkeypatch):
    settings = DummySettings()
    codes = _codes(2)
    quotes = [_quote(codes[0], "tushare", 10.00), _quote(codes[1], "tushare", 20.00)]

    readiness = _readiness(db, monkeypatch, settings, quotes, _bar_summaries(codes))

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


def test_local_pool_readiness_warns_when_sources_disabled_but_cache_is_usable(db, monkeypatch):
    settings = DummySettings()
    codes = _codes(2)
    quotes = [_quote(codes[0], "tushare", 10.00), _quote(codes[1], "tushare", 20.00)]
    _set_quote_source_enabled(db, monkeypatch, False)

    readiness = _readiness(db, monkeypatch, settings, quotes, _bar_summaries(codes))

    assert readiness["status"] == "ready"
    assert readiness["can_run_local_recommendation"] is True
    assert readiness["quote"]["active_sources"] == []
    assert readiness["quote"]["using_cache_without_active_sources"] is True
    assert "报价源开关当前全部关闭" in readiness["messages"][1]


def test_local_pool_readiness_treats_zero_candidate_count_as_full_market(db, monkeypatch):
    settings = DummySettings()
    settings.market_candidate_count = 0
    codes = _codes(3)

    quotes = [_quote(codes[0], "tushare", 10.00), _quote(codes[1], "tushare", 20.00), _quote(codes[2], "tushare", 30.00)]
    readiness = _readiness(db, monkeypatch, settings, quotes, _bar_summaries(codes))

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
    codes = _codes(2)
    quotes = [_quote(codes[0], "tushare", 10.00), _quote(codes[1], "tushare", 20.00)]

    readiness = _readiness(db, monkeypatch, settings, quotes, {})

    assert readiness["status"] == "blocked"
    assert readiness["can_run_local_recommendation"] is False
    assert readiness["kline"]["missing_count"] == 2
    assert readiness["kline"]["examples"]


def test_local_pool_status_separates_pool_coverage_from_demo_scan_limit(db, monkeypatch):
    settings = DummySettings()
    settings.market_candidate_count = 1
    settings.market_candidate_lookahead_multiplier = 1
    codes = _codes(3)
    quotes = [_quote(codes[0], "tushare", 10.00), _quote(codes[1], "tushare", 20.00), _quote(codes[2], "tushare", 30.00)]

    readiness = _readiness(db, monkeypatch, settings, quotes, _bar_summaries(codes))

    assert readiness["quote"]["available_count"] == 3
    assert readiness["quote"]["base_filter_eligible_count"] == 3
    assert readiness["quote"]["candidate_count"] == 1
    assert readiness["kline"]["ready_count"] == 1
    assert readiness["scan"]["actual_scan_count"] == 1
    assert readiness["scan"]["skill_input_count"] == 1
    assert readiness["scan"]["demo_limited"] is True


def test_local_pool_status_reports_short_history_separately(db, monkeypatch):
    settings = DummySettings()
    settings.market_candidate_count = 0
    codes = _codes(2)
    quotes = [_quote(codes[0], "tushare", 10.00), _quote(codes[1], "tushare", 20.00)]

    readiness = _readiness(
        db,
        monkeypatch,
        settings,
        quotes,
        {
            codes[0]: {"bar_count": 35, "latest_trade_date": "2026-05-27"},
            codes[1]: {"bar_count": 12, "latest_trade_date": "2026-05-27"},
        },
    )

    assert readiness["status"] == "warning"
    assert readiness["kline"]["ready_count"] == 1
    assert readiness["kline"]["missing_count"] == 0
    assert readiness["kline"]["short_history_count"] == 1
    assert "短历史 1 只" in readiness["messages"][1]


def test_local_pool_status_treats_new_listing_short_history_as_maturity_risk(db, monkeypatch):
    settings = DummySettings()
    settings.market_candidate_count = 0
    codes = _codes(2)
    quotes = [_quote(codes[0], "tushare", 10.00), _quote(codes[1], "tushare", 20.00)]

    readiness = _readiness(
        db,
        monkeypatch,
        settings,
        quotes,
        {
            codes[0]: {"bar_count": 35, "latest_trade_date": "2026-05-27"},
            codes[1]: {
                "bar_count": 22,
                "latest_trade_date": "2026-05-27",
                "first_trade_date": "2026-04-24",
                "list_date": "2026-04-24",
            },
        },
    )

    assert readiness["status"] == "warning"
    assert readiness["kline"]["ready_count"] == 1
    assert readiness["kline"]["short_history_count"] == 1
    assert readiness["kline"]["immature_history_count"] == 1
    assert "当前存在数据缺口：短历史" not in " ".join(readiness["messages"])
    assert "上市未满30根日K" in " ".join(readiness["messages"])


def test_local_pool_status_counts_low_liquidity_as_available_universe(db, monkeypatch):
    settings = DummySettings()
    settings.market_candidate_count = 0
    codes = _codes(2)
    quotes = [
        _quote(codes[0], "tushare", 10.00),
        _low_liquidity_quote(codes[1], "tushare", 20.00),
    ]

    readiness = _readiness(db, monkeypatch, settings, quotes, _bar_summaries(codes))
    filter_summary = readiness["quote"]["filter_summary"]

    assert readiness["quote"]["base_filter_eligible_count"] == 2
    assert readiness["quote"]["candidate_count"] == 2
    assert readiness["kline"]["ready_count"] == 2
    assert filter_summary["high_liquidity_count"] == 1
    assert filter_summary["low_liquidity_count"] == 1
    assert filter_summary["excluded_count"] == 0


def test_local_pool_status_uses_quote_date_for_coverage_reference(monkeypatch):
    assert _coverage_reference_date("2026-05-13", "2026-05-15") == "2026-05-13"
    assert _coverage_reference_date(None, "2026-05-15") == "2026-05-15"
    monkeypatch.setattr(market_pool, "_latest_allowed_quote_date", lambda today=None: "2026-06-10")
    assert _coverage_reference_date("2026-06-10", "2026-06-10") == "2026-06-09"


def test_inject_limit_up_flags_marks_limit_up_stocks(db, monkeypatch):
    """inject_limit_up_flags 应给涨停股注入 is_limit_up=True + 连板/封单字段，非涨停股保持 False。

    回归保护：limit-up-reseal-relay 的 universe 依赖 is_limit_up，prefilter 阶段
    缺此字段会导致策略 0 产出（第 87-94 轮修复的根因链）。
    """
    fake_rows = [
        {"code": "601689", "name": "招商银行", "trade_date": "2026-07-03",
         "limit_times": 2, "open_times": 1, "fd_amount_yi": 0.85,
         "limit_amount_yi": 1.2, "turnover_ratio": 5.5, "limit_type": "U"},
    ]
    monkeypatch.setattr(market_pool, "_load_cached_limit_up_board", lambda db, trade_date=None: fake_rows)

    stocks = [
        {"code": "601689", "name": "招商银行"},
        {"code": "000001", "name": "平安银行"},
    ]
    hit = market_pool.inject_limit_up_flags(db, stocks, trade_date="2026-07-03")

    assert hit == 1
    # 涨停股字段注入
    assert stocks[0]["is_limit_up"] is True
    assert stocks[0]["limit_up_consecutive_days"] == 2  # limit_times → consecutive_days
    assert stocks[0]["limit_up_open_times"] == 1
    assert stocks[0]["limit_up_fd_amount_yi"] == 0.85
    assert stocks[0]["limit_up_days20"] == 1  # 涨停股默认至少 1
    assert stocks[0]["twenty_cm_risk_gate"] == "pass"  # 默认 pass，feature_builder 会重算
    # 非涨停股保持默认
    assert stocks[1]["is_limit_up"] is False
    assert stocks[1]["limit_up_consecutive_days"] == 0


def test_inject_limit_up_flags_empty_stocks_returns_zero(db):
    """空 stocks 列表应返回 0，不报错。"""
    assert market_pool.inject_limit_up_flags(db, [], trade_date="2026-07-03") == 0
