"""网关单次行数上限自动翻页（2026-11-05：teajoin fina_indicator_vip period=20260630 恰好 10000 行被截断）。"""
from __future__ import annotations

import pytest

from app.services import tushare_transport as tt
from app.services.tushare_transport import AuthMode, ProviderConfig, TushareHttpProvider, TushareProviderRouter


def _p(name, **kw):
    return TushareHttpProvider(ProviderConfig(name=name, base_url="http://x", credential="c",
                                              auth_mode=AuthMode.JSON_TOKEN, rate_limit_per_minute=0, **kw))


def _rows(start, n):
    return [[f"{i:06d}.SH", "20260630"] for i in range(start, start + n)]


def _env(items, **extra):
    return {"code": 0, "msg": "", "data": {"fields": ["ts_code", "end_date"], "items": items, **extra}}


@pytest.fixture
def alerts(monkeypatch):
    out = []
    monkeypatch.setattr("app.core.alerts.emit_alert", lambda *a, **k: out.append(a) or True)
    return out


def _serve(monkeypatch, provider, total, *, cap=10000, ignore_offset=False, fail_at=None):
    calls = []

    def request(api_name, *, params=None, fields=""):
        params = dict(params or {})
        calls.append(params)
        off = int(params.get("offset") or 0)
        if fail_at is not None and off >= fail_at:
            raise tt.ProviderFailure(provider=provider.name, api_name=api_name, category=tt.FailureCategory.TIMEOUT,
                                     retryable=True, fallback_allowed=True)
        if ignore_offset:
            off = 0
        return _env(_rows(off, max(0, min(cap, total - off))))

    monkeypatch.setattr(provider, "request", request)
    return calls


def test_teajoin_full_page_is_paged_to_the_end(monkeypatch, alerts):
    tj = _p("teajoin")
    calls = _serve(monkeypatch, tj, 21694)
    env = TushareProviderRouter([tj]).request("fina_indicator_vip", params={"period": "20260630"}, fields="ts_code")
    items = env["data"]["items"]
    assert len(items) == 21694 and len({r[0] for r in items}) == 21694
    assert [c.get("offset") for c in calls] == [None, 10000, 20000]
    assert all(c["period"] == "20260630" for c in calls)
    assert alerts and alerts[0][0] == "provider_row_cap_hit" and "翻 3 页" in alerts[0][2]


def test_exact_multiple_stops_on_empty_page(monkeypatch, alerts):
    tj = _p("teajoin")
    calls = _serve(monkeypatch, tj, 10000)
    env = TushareProviderRouter([tj]).request("fina_indicator_vip", params={"period": "20260630"})
    assert len(env["data"]["items"]) == 10000 and len(calls) == 2


def test_under_cap_makes_no_extra_call(monkeypatch, alerts):
    tj = _p("teajoin")
    calls = _serve(monkeypatch, tj, 6753)
    env = TushareProviderRouter([tj]).request("fina_indicator_vip", params={"period": "20260930"})
    assert len(env["data"]["items"]) == 6753 and len(calls) == 1 and alerts == []


def test_explicit_limit_or_offset_is_left_to_caller(monkeypatch, alerts):
    tj = _p("teajoin")
    calls = _serve(monkeypatch, tj, 30000)
    TushareProviderRouter([tj]).request("fina_indicator_vip", params={"period": "x", "offset": 0, "limit": 10000})
    assert len(calls) == 1


def test_minute_apis_never_auto_paged(monkeypatch, alerts):
    tj = _p("teajoin")
    calls = _serve(monkeypatch, tj, 30000)
    TushareProviderRouter([tj]).request("stk_mins", params={"ts_code": "000001.SZ"})
    assert len(calls) == 1


@pytest.mark.parametrize("kw", [{"ignore_offset": True}, {"fail_at": 10000}])
def test_paging_problem_falls_back_never_returns_truncated(monkeypatch, alerts, kw):
    tj, qd = _p("teajoin", priority=1), _p("quantdata", priority=2)
    _serve(monkeypatch, tj, 21694, **kw)
    _serve(monkeypatch, qd, 7000)
    env = TushareProviderRouter([tj, qd]).request("fina_indicator_vip", params={"period": "20260630"})
    assert len(env["data"]["items"]) == 7000  # 换源，不拿 teajoin 的截断结果


def test_paging_problem_without_backup_raises(monkeypatch, alerts):
    tj = _p("teajoin")
    _serve(monkeypatch, tj, 21694, ignore_offset=True)
    with pytest.raises(tt.TushareProviderError):
        TushareProviderRouter([tj]).request("fina_indicator_vip", params={"period": "20260630"})


def test_other_provider_at_common_cap_alerts_but_does_not_page(monkeypatch, alerts):
    qd = _p("quantdata")
    calls = _serve(monkeypatch, qd, 9000, cap=5000)
    env = TushareProviderRouter([qd]).request("cyq_perf", params={"trade_date": "20261104"})
    assert len(env["data"]["items"]) == 5000 and len(calls) == 1  # 调用方 fetch_trade_date_paged 自己续页
    assert alerts and "不支持网关翻页" in alerts[0][2]


def test_request_pinned_pages_too(monkeypatch, alerts):
    tj = _p("teajoin")
    _serve(monkeypatch, tj, 11694)
    env = TushareProviderRouter([tj]).request_pinned("teajoin", "fina_indicator_vip", params={"period": "20260630"})
    assert len(env["data"]["items"]) == 11694


def test_page_with_reordered_fields_is_remapped(monkeypatch, alerts):
    tj = _p("teajoin")

    def request(api_name, *, params=None, fields=""):
        if (params or {}).get("offset"):
            return {"data": {"fields": ["end_date", "ts_code"], "items": [["20260630", "999999.SH"]]}}
        return _env(_rows(0, 10000))

    monkeypatch.setattr(tj, "request", request)
    env = TushareProviderRouter([tj]).request("fina_indicator_vip", params={"period": "20260630"})
    assert env["data"]["items"][-1] == ["999999.SH", "20260630"]
