from datetime import datetime, timedelta, timezone

import pytest

from app.services.datasource import cninfo_search as cs
from app.services.datasource import gateway as gw
from app.services.datasource import wrap


class _Resp:
    def __init__(self, status=200, payload=None, text_body=None):
        self.status_code, self._p, self._t = status, payload, text_body

    def json(self):
        if self._t is not None:
            raise ValueError("not json")
        return self._p


def _item(i, ts=1760000000000):
    return {"secCode": f"00000{i}", "secName": f"股{i}", "announcementTitle": f"<em>草案</em>{i}",
            "announcementTime": ts, "announcementId": str(1000 + i), "adjunctUrl": f"finalpage/x/{i}.PDF"}


@pytest.fixture(autouse=True)
def _gateway_env(monkeypatch):
    wrap._breakers.clear()

    class _NoLimit:
        def acquire(self):
            return None

    monkeypatch.setattr(wrap, "_limiter", lambda name, rpm: _NoLimit())
    monkeypatch.setattr(gw, "provider_enabled", lambda p: True)
    yield
    wrap._breakers.clear()


def test_paginates_and_normalizes(monkeypatch):
    pages = {1: {"announcements": [_item(1), _item(2)], "hasMore": True},
             2: {"announcements": [_item(2), _item(3)], "hasMore": False}}
    sent = []

    def fake_post(url, data=None, headers=None, timeout=None):
        sent.append((url, dict(data), headers))
        return _Resp(payload=pages[data["pageNum"]])

    monkeypatch.setattr("httpx.post", fake_post)
    rows = cs.search_announcements("草案", "category_gqjl_szsh", "2026-09-19", "2026-10-19")
    assert [r["announcementId"] for r in rows] == ["1001", "1002", "1003"]  # deduped across pages
    r = rows[0]
    assert r["announcementTitle"] == "草案1" and r["secCode"] == "000001" and r["secName"] == "股1"
    assert r["announcementTime"] == datetime.fromtimestamp(1760000000, tz=timezone(timedelta(hours=8)))
    assert r["pdf_url"].startswith("http://static.cninfo.com.cn/") and r["raw"]["announcementTitle"] == "<em>草案</em>1"
    url, form, headers = sent[0]
    assert url == cs.URL and headers["Referer"] == cs.REFERER
    assert form["seDate"] == "2026-09-19~2026-10-19" and form["tabName"] == "fulltext"
    assert form["category"] == "category_gqjl_szsh" and form["searchkey"] == "草案" and form["pageSize"] == 30
    assert len(sent) == 2


def test_dataset_always_on_gateway():
    assert gw.is_cutover("ann.search.cninfo")


def test_blocked_raises_and_counts_breaker(monkeypatch):
    monkeypatch.setattr("httpx.post", lambda *a, **k: _Resp(status=403))
    with pytest.raises(cs.CninfoBlockedError):
        cs.search_announcements("x", start="2026-10-01", end="2026-10-02")
    # force_open on CninfoBlockedError -> next call short-circuits
    with pytest.raises(gw.DataSourceError) as ei:
        cs.search_announcements("x", start="2026-10-01", end="2026-10-02")
    assert ei.value.kind == gw.ErrorKind.CIRCUIT_OPEN


def test_non_json_is_blocked(monkeypatch):
    monkeypatch.setattr("httpx.post", lambda *a, **k: _Resp(text_body="<html>"))
    with pytest.raises(cs.CninfoBlockedError):
        cs.search_announcements("x", start="2026-10-01", end="2026-10-02")


def test_5xx_upstream(monkeypatch):
    monkeypatch.setattr("httpx.post", lambda *a, **k: _Resp(status=502))
    with pytest.raises(gw.DataSourceError) as ei:
        cs.search_announcements("x", start="2026-10-01", end="2026-10-02")
    assert ei.value.kind == gw.ErrorKind.UPSTREAM
