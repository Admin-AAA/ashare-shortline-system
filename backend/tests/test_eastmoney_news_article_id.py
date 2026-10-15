"""东方财富公告 id：notice_id → art_code 改名兼容（2026-09 起 notice_id 消失，article_id 全空 → 唯一键冲突，0 入库）。"""
from app.services import market_auxiliary as ma


def test_art_code_preferred():
    assert ma._eastmoney_article_id({"art_code": "AN202610121830367208"}) == "AN202610121830367208"


def test_legacy_notice_id_still_supported():
    assert ma._eastmoney_article_id({"notice_id": "123"}) == "123"
    assert ma._eastmoney_article_id({"art_code": "", "notice_id": "123"}) == "123"


def test_missing_returns_empty():
    assert ma._eastmoney_article_id({}) == ""


def test_fetch_maps_art_code(monkeypatch):
    payload = {"data": {"list": [{
        "art_code": "AN1", "notice_date": "2026-10-13 00:00:00", "title_ch": "某某:关于收到立案告知书的公告",
    }]}}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return payload

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "Client", _Client)
    rows = ma._fetch_eastmoney_news("000001", None)
    ann = [r for r in rows if r["category"] == "announcement"]
    assert ann and ann[0]["article_id"] == "AN1"
    assert ann[0]["announce_date"] == "2026-10-13"
    assert ann[0]["is_risk_warning"] is True
