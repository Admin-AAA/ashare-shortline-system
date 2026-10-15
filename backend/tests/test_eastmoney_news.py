"""东方财富新闻主源：解析 / 反爬退避 / 限速。"""
from __future__ import annotations

import app.services.eastmoney_news as em


def test_parse_prefers_art_code():
    row = em.parse_announcement_row(
        {"art_code": "AN1", "notice_date": "2026-10-13 00:00:00", "title_ch": "某某:立案调查"},
        "000001",
    )
    assert row is not None
    assert row["article_id"] == "AN1"
    assert row["is_risk_warning"] is True
    assert row["source"] == "eastmoney"


def test_parse_drops_empty_article_id():
    assert em.parse_announcement_row({"notice_date": "2026-10-13", "title_ch": "x"}, "000001") is None


def test_fetch_raises_after_repeated_567(monkeypatch):
    em.reset_rate_limiter_for_tests()
    sleeps: list[float] = []

    class _Resp:
        status_code = 567

        def raise_for_status(self):
            raise AssertionError("should not raise_for_status on 567")

        def json(self):
            return {}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            return _Resp()

        def close(self):
            pass

    monkeypatch.setattr(em.httpx, "Client", _Client)
    monkeypatch.setattr(em.time, "sleep", lambda s: sleeps.append(s))
    try:
        em.fetch_announcements_for_code("000001")
        raise AssertionError("expected EastmoneyBlockedError")
    except em.EastmoneyBlockedError:
        pass
    assert any(s >= 5 for s in sleeps)


def test_fetch_maps_rows(monkeypatch):
    em.reset_rate_limiter_for_tests()
    payload = {"data": {"list": [{
        "art_code": "AN9",
        "notice_date": "2026-10-14 00:00:00",
        "title_ch": "普通公告",
    }]}}

    class _Resp:
        status_code = 200

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

        def close(self):
            pass

    monkeypatch.setattr(em.httpx, "Client", _Client)
    monkeypatch.setattr(em.time, "sleep", lambda *_: None)
    rows = em.fetch_announcements_for_code("000001", start_date="2026-10-01", end_date="2026-10-15")
    assert rows == [{
        "source": "eastmoney",
        "article_id": "AN9",
        "code": "000001",
        "announce_date": "2026-10-14",
        "title": "普通公告",
        "summary": None,
        "category": "announcement",
        "sentiment": "neutral",
        "is_risk_warning": False,
    }]
