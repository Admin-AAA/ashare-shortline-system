"""测试新增功能：分钟K线、新闻恢复、新数据模型、特征富集。"""

from types import SimpleNamespace

import pandas as pd
import pytest


class DummySettings:
    market_request_timeout_seconds = 5
    market_scan_size = 60
    market_kline_days = 90
    news_sources = "tushare,eastmoney"
    limit_up_cache_ttl_seconds = 3600
    concept_sector_cache_ttl_seconds = 3600
    capital_flow_cache_ttl_seconds = 7200
    dragon_tiger_cache_ttl_seconds = 3600
    news_cache_ttl_seconds = 7200
    quote_sources = "tushare"
    kline_sources = "tushare"
    data_provider = "tushare"


@pytest.fixture(autouse=True)
def _disable_xiaoshi_minute_fetch_for_legacy_unit_tests(monkeypatch):
    monkeypatch.setattr(
        "app.services.tushare_client._fetch_xiaoshi_minute_kbars",
        lambda *args, **kwargs: [],
    )
    # 本文件测的是 legacy 取数分支；本机 .env 打开 bar.minute 网关切换后会绕过这里的桩、
    # 走真实网关，结果随环境变化。固定走 legacy 分支。
    monkeypatch.setattr("app.services.datasource.gateway.is_cutover", lambda _ds: False)
    monkeypatch.setattr("app.services.datasource.gateway.is_shadow", lambda _ds: False)


# ═══════════════════════════════════════════════════════════════════
# 新闻恢复
# ═══════════════════════════════════════════════════════════════════


def test_news_sources_prefers_tushare_with_eastmoney_fallback():
    from app.services.market_auxiliary import _news_sources

    sources = _news_sources(DummySettings())
    assert sources == ["tushare", "eastmoney"]


def test_fetch_tushare_news_maps_announcements(monkeypatch):
    from app.services.market_auxiliary import _fetch_news

    class FakePro:
        def anns_d(self, **kwargs):
            assert kwargs["ts_code"] == "002475.SZ"
            assert kwargs["start_date"] == "20260522"
            assert kwargs["end_date"] == "20260529"
            return pd.DataFrame(
                [
                    {
                        "ts_code": "002475.SZ",
                        "ann_date": "20260529",
                        "title": "立讯精密：2026年度业绩预增公告",
                        "url": "https://example.test/ann/1.pdf",
                    },
                    {
                        "ts_code": "000001.SZ",
                        "ann_date": "20260529",
                        "title": "其他股票公告",
                        "url": "https://example.test/ann/2.pdf",
                    },
                ]
            )

    monkeypatch.setattr("app.services.tushare_client.get_pro", lambda: FakePro())

    # 主源巨潮（真实网络）不可用时才回退 anns_d；这里测回退映射，先让巨潮失败
    def _cninfo_down(*_a, **_kw):
        raise RuntimeError("cninfo down")

    monkeypatch.setattr("app.services.cninfo_news.fetch_announcements_for_code", _cninfo_down)
    rows = _fetch_news("tushare", "002475", DummySettings(), trade_date="2026-05-29")

    assert len(rows) == 1
    assert rows[0]["source"] == "tushare"
    assert rows[0]["code"] == "002475"
    assert rows[0]["announce_date"] == "2026-05-29"
    assert rows[0]["category"] == "announcement"
    assert rows[0]["sentiment"] == "positive"
    assert rows[0]["is_risk_warning"] is False
    assert rows[0]["article_id"]


# ═══════════════════════════════════════════════════════════════════
# 分钟K线
# ═══════════════════════════════════════════════════════════════════


def test_fetch_minute_kbars_uses_tushare(monkeypatch):
    from app.services.tushare_client import fetch_minute_kbars

    def fake_tushare(code, *, start_time, end_time, trace=None):
        return [
            {
                "code": code,
                "trade_date": "2026-05-22",
                "trade_time": "2026-05-22 09:30:00",
                "open": 10.70,
                "high": 10.70,
                "low": 10.70,
                "close": 10.70,
                "volume": 6616.0,
                "amount": 7079120.0,
            }
        ]

    monkeypatch.setattr("app.services.tushare_client._fetch_tushare_minute_kbars", fake_tushare)
    bars = fetch_minute_kbars("000001", trade_date="2026-05-22")
    assert len(bars) == 1
    assert bars[0]["code"] == "000001"
    assert bars[0]["close"] == 10.70
    assert bars[0]["volume"] == 6616.0


def test_fetch_minute_kbars_sh_stock(monkeypatch):
    from app.services.tushare_client import fetch_minute_kbars

    def fake_tushare(code, *, start_time, end_time, trace=None):
        return [
            {
                "code": code,
                "trade_date": "2026-05-22",
                "trade_time": "2026-05-22 09:30:00",
                "open": 8.88,
                "high": 8.88,
                "low": 8.88,
                "close": 8.88,
                "volume": 1716.0,
                "amount": 1523808.0,
            }
        ]

    monkeypatch.setattr("app.services.tushare_client._fetch_tushare_minute_kbars", fake_tushare)
    bars = fetch_minute_kbars("600000", trade_date="2026-05-22")
    assert len(bars) == 1
    assert bars[0]["code"] == "600000"
    assert bars[0]["close"] == 8.88


def test_fetch_minute_kbars_handles_empty(monkeypatch):
    from app.services.tushare_client import fetch_minute_kbars

    monkeypatch.setattr("app.services.tushare_client._fetch_tushare_minute_kbars", lambda *args, **kwargs: [])
    bars = fetch_minute_kbars("000001", trade_date="2026-05-22")
    assert bars == []


def test_fetch_minute_kbars_filters_exact_trade_date(monkeypatch):
    from app.services.tushare_client import fetch_minute_kbars

    def fake_tushare(code, *, start_time, end_time, trace=None):
        return [
            {
                "code": code,
                "trade_date": "2026-05-22",
                "trade_time": "2026-05-22 14:30:00",
                "open": 10.80,
                "high": 10.81,
                "low": 10.79,
                "close": 10.80,
                "volume": 1200.0,
                "amount": 1296000.0,
            }
        ]

    monkeypatch.setattr("app.services.tushare_client._fetch_tushare_minute_kbars", fake_tushare)
    bars = fetch_minute_kbars("000001", trade_date="2026-05-22")
    assert len(bars) == 1
    assert bars[0]["trade_date"] == "2026-05-22"


def test_fetch_recent_minute_kbars_returns_empty_when_tushare_empty(monkeypatch):
    from app.services.tushare_client import fetch_recent_minute_kbars

    monkeypatch.setattr("app.services.tushare_client._fetch_tushare_minute_kbars", lambda *args, **kwargs: [])
    bars = fetch_recent_minute_kbars("000001", ndays=2)
    assert bars == []


def test_fetch_recent_index_minute_kbars_returns_empty_when_tushare_empty(monkeypatch):
    from app.services.tushare_client import fetch_recent_index_minute_kbars

    monkeypatch.setattr("app.services.tushare_client._fetch_tushare_minute_kbars", lambda *args, **kwargs: [])
    bars = fetch_recent_index_minute_kbars("000300", ndays=2)
    assert bars == []


def test_fetch_minute_kbars_prefers_tushare_stk_mins(monkeypatch):
    from app.services.tushare_client import fetch_minute_kbars

    def fake_tushare(code, *, start_time, end_time, trace=None):
        if trace is not None:
            trace.append({"source": "tushare_stk_mins", "code": code, "status": "success", "row_count": 1})
        return [
            {
                "code": code,
                "trade_date": "2026-05-22",
                "trade_time": "2026-05-22 09:30:00",
                "open": 10.7,
                "high": 10.7,
                "low": 10.7,
                "close": 10.7,
                "volume": 100.0,
                "amount": 107000.0,
            }
        ]

    monkeypatch.setattr("app.services.tushare_client._fetch_tushare_minute_kbars", fake_tushare)

    trace: list[dict] = []
    bars = fetch_minute_kbars("000001", trade_date="2026-05-22", trace=trace)

    assert len(bars) == 1
    assert bars[0]["trade_date"] == "2026-05-22"
    assert trace[0]["source"] == "tushare_stk_mins"


def test_fetch_recent_minute_kbars_uses_tushare_without_fallback(monkeypatch):
    from app.services.tushare_client import fetch_recent_minute_kbars

    def fake_tushare(code, *, start_time, end_time, trace=None):
        if trace is not None:
            trace.append({"source": "tushare_stk_mins", "code": code, "status": "success", "row_count": 1})
        return [
            {
                "code": code,
                "trade_date": "2026-05-22",
                "trade_time": "2026-05-22 09:30:00",
                "open": 10.7,
                "high": 10.7,
                "low": 10.7,
                "close": 10.7,
                "volume": 100.0,
                "amount": 107000.0,
            }
        ]

    monkeypatch.setattr("app.services.tushare_client._fetch_tushare_minute_kbars", fake_tushare)

    trace: list[dict] = []
    bars = fetch_recent_minute_kbars("000001", ndays=1, trace=trace)

    assert len(bars) == 1
    assert bars[0]["trade_date"] == "2026-05-22"
    assert [item["source"] for item in trace] == ["tushare_stk_mins"]


def test_fetch_recent_minute_kbars_records_tushare_permission_denied(monkeypatch):
    from app.services import tushare_client
    from app.services.tushare_client import fetch_recent_minute_kbars

    tushare_client._TUSHARE_STK_MINS_PERMISSION_DENIED_REASON = None

    class FakePro:
        def stk_mins(self, **kwargs):
            raise RuntimeError("Tushare stk_mins: 您没有该接口权限")

    monkeypatch.setattr("app.services.tushare_client.get_pro", lambda: FakePro())

    trace: list[dict] = []
    bars = fetch_recent_minute_kbars("000001", ndays=1, trace=trace)

    assert bars == []
    assert any(item["source"] == "tushare_stk_mins" and item["status"] == "permission_denied" for item in trace)


def test_fetch_recent_minute_kbars_permission_from_structured_router_error(monkeypatch):
    """权限判定用结构化 FailureCategory，不依赖 str(exc) 子串巧合。"""
    from app.services import tushare_client
    from app.services.tushare_client import fetch_recent_minute_kbars
    from app.services.tushare_transport import (
        FailureCategory,
        ProviderFailure,
        TushareProviderError,
    )

    tushare_client._TUSHARE_STK_MINS_PERMISSION_DENIED_REASON = None

    class FakePro:
        def stk_mins(self, **kwargs):
            failure = ProviderFailure(
                provider="gw_c",
                api_name="stk_mins",
                category=FailureCategory.PERMISSION,
                retryable=False,
                fallback_allowed=True,
            )
            raise TushareProviderError("stk_mins", [failure])

    monkeypatch.setattr("app.services.tushare_client.get_pro", lambda: FakePro())

    trace: list[dict] = []
    bars = fetch_recent_minute_kbars("000001", ndays=1, trace=trace)

    assert bars == []
    assert any(item["source"] == "tushare_stk_mins" and item["status"] == "permission_denied" for item in trace)


def test_fetch_recent_minute_kbars_network_error_is_not_permission(monkeypatch):
    """非权限类聚合异常（如网络）不得误判成 permission_denied。"""
    from app.services import tushare_client
    from app.services.tushare_client import fetch_recent_minute_kbars
    from app.services.tushare_transport import (
        FailureCategory,
        ProviderFailure,
        TushareProviderError,
    )

    tushare_client._TUSHARE_STK_MINS_PERMISSION_DENIED_REASON = None

    class FakePro:
        def stk_mins(self, **kwargs):
            failure = ProviderFailure(
                provider="gw_c",
                api_name="stk_mins",
                category=FailureCategory.NETWORK,
                retryable=True,
                fallback_allowed=True,
            )
            raise TushareProviderError("stk_mins", [failure])

    monkeypatch.setattr("app.services.tushare_client.get_pro", lambda: FakePro())

    trace: list[dict] = []
    bars = fetch_recent_minute_kbars("000001", ndays=1, trace=trace)

    assert bars == []
    assert any(item["source"] == "tushare_stk_mins" and item["status"] == "error" for item in trace)
    assert not any(item["status"] == "permission_denied" for item in trace)


# ═══════════════════════════════════════════════════════════════════
# 新数据模型
# ═══════════════════════════════════════════════════════════════════


def test_auction_data_model():
    from app.models.tables import AuctionData

    a = AuctionData(
        code="000001", trade_date="2026-05-22",
        vol=661600.0, price=10.70, amount=7079120.0,
        pre_close=10.70, turnover_rate=0.0034,
        volume_ratio=0.74, float_share=1940560.0,
    )
    assert a.code == "000001"
    assert a.price == 10.70


def test_broker_recommend_model():
    from app.models.tables import BrokerRecommend

    b = BrokerRecommend(
        code="000001", name="平安银行",
        month="202605", broker="中信证券",
    )
    assert b.broker == "中信证券"


def test_pledge_stat_model():
    from app.models.tables import PledgeStat

    p = PledgeStat(
        code="000001", end_date="2026-05-15",
        pledge_count=9, unrest_pledge=2460.5,
        rest_pledge=0.0, pledge_ratio=0.13,
    )
    assert p.pledge_ratio == 0.13


def test_minute_kbar_model():
    from app.models.tables import MinuteKbar

    m = MinuteKbar(
        code="000001", trade_date="2026-05-22",
        trade_time="2026-05-22 09:30:00",
        open=10.70, high=10.70, low=10.70, close=10.70,
        volume=6616.0, amount=7079120.0,
    )
    assert m.code == "000001"


def test_news_announcement_model():
    from app.models.tables import NewsAnnouncement

    n = NewsAnnouncement(
        source="tushare", article_id="news_001",
        code="000001", announce_date="2026-05-22",
        title="业绩预增公告", category="news",
        sentiment="positive", is_risk_warning=False,
    )
    assert n.sentiment == "positive"


# ═══════════════════════════════════════════════════════════════════
# 扩张因子 DailyKbar
# ═══════════════════════════════════════════════════════════════════


def test_daily_kbar_has_new_factor_fields():
    from app.models.tables import DailyKbar

    bar = DailyKbar(
        code="000001", trade_date="2026-05-22",
        open=10.0, high=10.5, low=9.9, close=10.1,
        volume=1000, amount=10000, pct_chg=1.0,
        turnover_rate=1.0,
        adj_factor=1.0, asi=100.0, atr=0.5, bbi=10.2,
        dpo=0.1, obv=50000.0, psy=60.0, vr=120.0,
    )
    assert bar.asi == 100.0
    assert bar.atr == 0.5
    assert bar.obv == 50000.0


# ═══════════════════════════════════════════════════════════════════
# 特征富集
# ═══════════════════════════════════════════════════════════════════


def test_enrich_with_auction_data():
    from app.services.feature_builder import _enrich_with_auxiliary_data

    stocks = [{"code": "000001", "dsa_profile": {}}]
    aux = {
        "auctions": {
            "000001": {"volume_ratio": 0.74, "turnover_rate": 0.0034, "price": 10.70, "pre_close": 10.70}
        },
        "pledges": {"000001": {"pledge_ratio": 0.13, "pledge_count": 9}},
        "broker_recs": {"000001": 3},
        "share_floats_upcoming": {"000001": [{"float_date": "2026-06-01", "float_ratio": 5.0}]},
        "limit_up": {}, "concept_sectors": {}, "capital_flows": {},
        "dragon_tigers": {}, "news": {},
    }
    # 用 monkeypatching 避免 DB 调用
    _enrich_with_auxiliary_data(stocks, None, {}, _aux_override=aux)
    s = stocks[0]
    assert s.get("auction_volume_ratio") == 0.74
    assert s.get("auction_signal_available") is True
    assert s.get("auction_gap_pct") == 0.0
    assert "auction" not in s["data_quality"]["missing_fields"]
    assert s.get("pledge_ratio") == 0.13
    assert s.get("broker_rec_count") == 3
    assert s.get("has_upcoming_float") is True
    assert s.get("upcoming_float_ratio") == 5.0


def test_enrich_without_auction_data_keeps_signal_unavailable():
    from app.services.feature_builder import _enrich_with_auxiliary_data

    stocks = [{"code": "000001", "dsa_profile": {}, "data_quality": {}}]
    aux = {
        "auctions": {}, "pledges": {}, "broker_recs": {},
        "share_floats_upcoming": {}, "limit_up": {}, "concept_sectors": {},
        "capital_flows": {}, "dragon_tigers": {}, "news": {},
    }

    _enrich_with_auxiliary_data(stocks, None, {}, _aux_override=aux)

    stock = stocks[0]
    assert stock["auction_signal_available"] is False
    assert stock["auction_gap_pct"] is None
    assert stock["auction_price"] is None
    assert stock["auction_pre_close"] is None
    assert "auction" not in stock["data_quality"]["missing_fields"]
    assert "auction" in stock["data_quality"]["optional_missing_fields"]


def test_risk_score_includes_pledge_and_float():
    from app.services.feature_builder import _risk_score

    # 高质押 + 即将解禁 → 高风险
    stock = {"pledge_ratio": 35, "has_upcoming_float": True}
    score = _risk_score(stock)
    assert score >= 25  # 15 (pledge) + 10 (float)


def test_news_sources_enabled_by_default():
    from app.core.config import Settings

    s = Settings()
    assert s.news_sources == "tushare,eastmoney"


def test_filter_effectiveness_returns_structure(db):
    """filter_effectiveness 应返回正确的结构：count/filtered_count/kept/filters。"""
    from app.services.outcome_service import filter_effectiveness

    result = filter_effectiveness(db)
    # 无数据时应返回 error 或正确结构
    if "error" in result:
        assert result["count"] == 0
        return
    assert "count" in result
    assert "filtered_count" in result
    assert "filter_rate_pct" in result
    assert "kept" in result
    assert "filtered_by_rule" in result
    # kept 结构验证
    assert "count" in result["kept"]
    assert "net_win_rate" in result["kept"]
    assert "net_avg_return" in result["kept"]
    assert "net_profit_factor" in result["kept"]
    # filtered_by_rule 结构验证
    for rule_name, rule_data in result["filtered_by_rule"].items():
        assert "count" in rule_data
        assert "win_rate" in rule_data
        assert "avg_return" in rule_data
