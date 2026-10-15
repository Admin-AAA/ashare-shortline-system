from __future__ import annotations

import app.services.cninfo_news as cn


def test_parse_and_risk():
    row = cn.parse_announcement(
        {"announcementId": "122", "announcementTitle": "关于收到立案告知书的公告", "announcementTime": 1791820800000},
        "000001",
    )
    assert row is not None
    assert row["source"] == "cninfo"
    assert row["article_id"] == "122"
    assert row["is_risk_warning"] is True
    assert row["announce_date"] >= "2026-10-12"


def test_fetch_uses_org_and_query(monkeypatch):
    cn.reset_rate_limiter_for_tests()
    calls = []

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    class _Client:
        def __init__(self, *a, **k):
            pass

        def post(self, url, data=None):
            calls.append((url, dict(data or {})))
            if "topSearch" in url:
                return _Resp([{"code": "000001", "category": "A股", "orgId": "gssz0000001"}])
            return _Resp({
                "totalAnnouncement": 1,
                "announcements": [{
                    "announcementId": "9",
                    "announcementTitle": "普通公告",
                    "announcementTime": 1791820800000,
                }],
            })

        def close(self):
            pass

    monkeypatch.setattr(cn.httpx, "Client", _Client)
    monkeypatch.setattr(cn.time, "sleep", lambda *_: None)
    rows = cn.fetch_announcements_for_code("000001", start_date="2026-09-21", end_date="2026-10-15")
    assert rows and rows[0]["article_id"] == "9"
    assert any("topSearch" in u for u, _ in calls)
    assert any("hisAnnouncement" in u for u, _ in calls)
