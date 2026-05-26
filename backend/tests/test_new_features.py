"""测试新增功能：分钟K线、新闻恢复、新数据模型、特征富集。"""

from types import SimpleNamespace

import pytest


class DummySettings:
    market_request_timeout_seconds = 5
    market_scan_size = 60
    market_kline_days = 90
    news_sources = "eastmoney"
    limit_up_cache_ttl_seconds = 3600
    concept_sector_cache_ttl_seconds = 3600
    capital_flow_cache_ttl_seconds = 7200
    dragon_tiger_cache_ttl_seconds = 3600
    news_cache_ttl_seconds = 7200
    quote_sources = "eastmoney"
    kline_sources = "tushare"
    data_provider = "multi"


# ═══════════════════════════════════════════════════════════════════
# 新闻恢复
# ═══════════════════════════════════════════════════════════════════


def test_news_sources_returns_eastmoney():
    from app.services.market_auxiliary import _news_sources

    sources = _news_sources(DummySettings())
    assert sources == ["eastmoney"]


def test_fetch_news_dispatches_to_eastmoney(monkeypatch):
    from app.services.market_auxiliary import _fetch_news

    def fake_eastmoney(code, settings):
        return [{"title": "测试新闻", "code": code}]

    monkeypatch.setattr(
        "app.services.market_auxiliary._fetch_eastmoney_news", fake_eastmoney
    )
    result = _fetch_news("eastmoney", "000001", DummySettings())
    assert len(result) == 1
    assert result[0]["title"] == "测试新闻"


def test_news_sentiment_classification(monkeypatch):
    from app.services.market_auxiliary import _fetch_eastmoney_news

    calls = []

    def fake_http(url, settings):
        if "ann" in url:
            return {
                "data": {
                    "list": [
                        {
                            "notice_id": "ann_001",
                            "title_ch": "关于公司收到监管函的公告",
                            "notice_date": "2026-05-22 00:00:00",
                            "summary": "监管函内容",
                        }
                    ]
                }
            }
        if "notify" in url:
            return {
                "data": {
                    "list": [
                        {
                            "notice_id": "news_001",
                            "title_ch": "公司业绩大幅增长公告",
                            "notice_date": "2026-05-22 00:00:00",
                            "summary": "业绩增长",
                        }
                    ]
                }
            }
        return {"data": {}}

    monkeypatch.setattr(
        "app.services.market_data._http_get_json", fake_http
    )
    result = _fetch_eastmoney_news("000001", DummySettings())
    assert len(result) == 2
    ann = [r for r in result if r["category"] == "announcement"][0]
    assert ann["sentiment"] == "negative"
    assert ann["is_risk_warning"] is True
    news = [r for r in result if r["category"] == "news"][0]
    assert news["sentiment"] == "positive"


# ═══════════════════════════════════════════════════════════════════
# 分钟K线
# ═══════════════════════════════════════════════════════════════════


def test_fetch_minute_kbars_parses_tencent_response(monkeypatch):
    from app.services.tushare_client import fetch_minute_kbars

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "data": {
                    "trends": [
                        "2026-05-22 09:30,0.00,10.70,10.70,10.70,6616,7079120.00,10.700",
                        "2026-05-22 09:31,0.00,10.66,10.68,10.66,60046,64093840.00,10.680",
                    ]
                }
            }

    monkeypatch.setattr("app.services.tushare_client._fetch_tushare_minute_kbars", lambda *args, **kwargs: [])
    monkeypatch.setattr("app.services.tushare_client.curl_requests.get", lambda *args, **kwargs: FakeResponse())
    monkeypatch.setattr(
        "app.services.tushare_client.requests.get",
        lambda *args, **kwargs: (_ for _ in ()).throw(Exception("no fallback")),
    )
    bars = fetch_minute_kbars("000001")
    assert len(bars) == 2
    assert bars[0]["code"] == "000001"
    assert bars[0]["close"] == 10.70
    assert bars[0]["volume"] == 6616.0
    assert "09:30:00" in bars[0]["trade_time"]


def test_fetch_minute_kbars_sh_stock(monkeypatch):
    from app.services.tushare_client import fetch_minute_kbars

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "data": {
                    "trends": [
                        "2026-05-22 09:30,0.00,8.88,8.88,8.88,1716,1523808.00,8.880"
                    ]
                }
            }

    monkeypatch.setattr("app.services.tushare_client._fetch_tushare_minute_kbars", lambda *args, **kwargs: [])
    monkeypatch.setattr("app.services.tushare_client.curl_requests.get", lambda *args, **kwargs: FakeResponse())
    monkeypatch.setattr(
        "app.services.tushare_client.requests.get",
        lambda *args, **kwargs: (_ for _ in ()).throw(Exception("no fallback")),
    )
    bars = fetch_minute_kbars("600000")
    assert len(bars) == 1
    assert bars[0]["code"] == "600000"
    assert bars[0]["close"] == 8.88


def test_fetch_minute_kbars_handles_errors(monkeypatch):
    from app.services.tushare_client import fetch_minute_kbars

    monkeypatch.setattr("app.services.tushare_client._fetch_tushare_minute_kbars", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "app.services.tushare_client.curl_requests.get",
        lambda *args, **kwargs: (_ for _ in ()).throw(Exception("network error")),
    )
    monkeypatch.setattr(
        "app.services.tushare_client.requests.get",
        lambda *args, **kwargs: (_ for _ in ()).throw(Exception("network error")),
    )
    bars = fetch_minute_kbars("000001")
    assert bars == []


def test_fetch_minute_kbars_filters_exact_trade_date(monkeypatch):
    from app.services.tushare_client import fetch_minute_kbars

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "data": {
                    "trends": [
                        "2026-05-21 14:30,0.00,10.50,10.51,10.49,1000,1050000.00,10.500",
                        "2026-05-22 14:30,0.00,10.80,10.81,10.79,1200,1296000.00,10.800",
                    ]
                }
            }

    monkeypatch.setattr("app.services.tushare_client._fetch_tushare_minute_kbars", lambda *args, **kwargs: [])
    monkeypatch.setattr("app.services.tushare_client.curl_requests.get", lambda *args, **kwargs: FakeResponse())
    monkeypatch.setattr(
        "app.services.tushare_client.requests.get",
        lambda *args, **kwargs: (_ for _ in ()).throw(Exception("no fallback")),
    )
    bars = fetch_minute_kbars("000001", trade_date="2026-05-22")
    assert len(bars) == 1
    assert bars[0]["trade_date"] == "2026-05-22"


def test_fetch_recent_minute_kbars_falls_back_to_sina(monkeypatch):
    from app.services.tushare_client import fetch_recent_minute_kbars

    class EmptyEastmoneyResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"trends": []}}

    class FakeSinaResponse:
        status_code = 200
        text = (
            'jsonp=([{"day":"2026-05-21 14:59:00","open":"10.10","high":"10.11","low":"10.09","close":"10.10","volume":"100","amount":"101000"},'
            '{"day":"2026-05-22 09:30:00","open":"10.20","high":"10.21","low":"10.19","close":"10.20","volume":"200","amount":"204000"}]);'
        )

        def raise_for_status(self):
            pass

    monkeypatch.setattr("app.services.tushare_client._fetch_tushare_minute_kbars", lambda *args, **kwargs: [])
    monkeypatch.setattr("app.services.tushare_client.curl_requests.get", lambda *args, **kwargs: EmptyEastmoneyResponse())
    monkeypatch.setattr("app.services.tushare_client.requests.get", lambda *args, **kwargs: FakeSinaResponse())

    bars = fetch_recent_minute_kbars("000001", ndays=2)

    assert len(bars) == 2
    assert bars[0]["trade_date"] == "2026-05-21"
    assert bars[-1]["trade_date"] == "2026-05-22"
    assert bars[-1]["pre_close"] == 10.10


def test_fetch_recent_index_minute_kbars_falls_back_to_sina(monkeypatch):
    from app.services.tushare_client import fetch_recent_index_minute_kbars

    class EmptyEastmoneyResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"trends": []}}

    class FakeSinaResponse:
        status_code = 200
        text = (
            'jsonp=([{"day":"2026-05-21 14:59:00","open":"4000.10","high":"4001.11","low":"3999.09","close":"4000.10","volume":"100","amount":"101000"},'
            '{"day":"2026-05-22 09:30:00","open":"4010.20","high":"4011.21","low":"4009.19","close":"4010.20","volume":"200","amount":"204000"}]);'
        )

        def raise_for_status(self):
            pass

    monkeypatch.setattr("app.services.tushare_client._fetch_tushare_minute_kbars", lambda *args, **kwargs: [])
    monkeypatch.setattr("app.services.tushare_client.curl_requests.get", lambda *args, **kwargs: EmptyEastmoneyResponse())
    monkeypatch.setattr("app.services.tushare_client.requests.get", lambda *args, **kwargs: FakeSinaResponse())

    bars = fetch_recent_index_minute_kbars("000300", ndays=2)

    assert len(bars) == 2
    assert bars[0]["code"] == "000300"
    assert bars[-1]["pre_close"] == 4000.10


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
    monkeypatch.setattr(
        "app.services.tushare_client.curl_requests.get",
        lambda *args, **kwargs: (_ for _ in ()).throw(Exception("should not fallback")),
    )

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
    monkeypatch.setattr(
        "app.services.tushare_client.curl_requests.get",
        lambda *args, **kwargs: (_ for _ in ()).throw(Exception("should not fallback")),
    )
    monkeypatch.setattr(
        "app.services.tushare_client.requests.get",
        lambda *args, **kwargs: (_ for _ in ()).throw(Exception("should not fallback")),
    )

    trace: list[dict] = []
    bars = fetch_recent_minute_kbars("000001", ndays=1, trace=trace)

    assert len(bars) == 1
    assert bars[0]["trade_date"] == "2026-05-22"
    assert [item["source"] for item in trace] == ["tushare_stk_mins"]


def test_fetch_recent_minute_kbars_records_tushare_permission_fallback(monkeypatch):
    from app.services import tushare_client
    from app.services.tushare_client import fetch_recent_minute_kbars

    tushare_client._TUSHARE_STK_MINS_PERMISSION_DENIED_REASON = None

    class FakePro:
        def stk_mins(self, **kwargs):
            raise RuntimeError("Tushare stk_mins: 您没有该接口权限")

    class EmptyEastmoneyResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"trends": []}}

    class FakeSinaResponse:
        status_code = 200
        text = 'jsonp=([{"day":"2026-05-22 09:30:00","open":"10.20","high":"10.21","low":"10.19","close":"10.20","volume":"200","amount":"204000"}]);'

        def raise_for_status(self):
            pass

    monkeypatch.setattr("app.services.tushare_client.get_pro", lambda: FakePro())
    monkeypatch.setattr("app.services.tushare_client.curl_requests.get", lambda *args, **kwargs: EmptyEastmoneyResponse())
    monkeypatch.setattr("app.services.tushare_client.requests.get", lambda *args, **kwargs: FakeSinaResponse())

    trace: list[dict] = []
    bars = fetch_recent_minute_kbars("000001", ndays=1, trace=trace)

    assert len(bars) == 1
    assert any(item["source"] == "tushare_stk_mins" and item["status"] == "permission_denied" for item in trace)
    assert any(item["source"] == "sina_minute" and item["status"] == "success" for item in trace)


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
        source="eastmoney", article_id="news_001",
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
    assert s.get("pledge_ratio") == 0.13
    assert s.get("broker_rec_count") == 3
    assert s.get("has_upcoming_float") is True
    assert s.get("upcoming_float_ratio") == 5.0


def test_risk_score_includes_pledge_and_float():
    from app.services.feature_builder import _risk_score

    # 高质押 + 即将解禁 → 高风险
    stock = {"pledge_ratio": 35, "has_upcoming_float": True}
    score = _risk_score(stock)
    assert score >= 25  # 15 (pledge) + 10 (float)


def test_news_sources_not_empty():
    from app.core.config import Settings

    s = Settings()
    assert s.news_sources == "eastmoney"
