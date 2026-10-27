"""promax 额度专留实时行情（2026-10-26 21:00）+ teajoin stk_mins 主源 + 盘中让位默认关闭。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.config import get_settings
from app.services import tushare_transport as tt
from app.services.tushare_transport import FailureCategory, LeaderSpec, ProviderFailure, TushareProviderError, TushareProviderRouter


class Fake:
    def __init__(self, name, *, fail=None):
        self.name, self.fail, self.calls = name, fail, []
        self.config = SimpleNamespace(rate_limit_per_minute=60, priority=1)

    def supports(self, api):
        return True

    def lb_health(self, api):
        return 1.0

    def lb_headroom(self, api):
        return 1.0

    def close(self):
        pass

    def health_snapshot(self):
        return {}

    def request(self, api, *, params=None, fields=""):
        self.calls.append(api)
        if self.fail:
            raise ProviderFailure(provider=self.name, api_name=api, category=self.fail, retryable=False, fallback_allowed=True)
        return {"data": {"items": [[1]], "fields": ["x"]}}


def make(*providers, leaders=()):
    return TushareProviderRouter(list(providers), api_routes={}, api_tiers={}, leaders=list(leaders))


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    for k in ("ASHARE_TUSHARE_PROMAX_RTK_RESERVED", "ASHARE_TUSHARE_PROMAX_RESEARCH_ENABLED"):
        monkeypatch.delenv(k, raising=False)
    get_settings.cache_clear()
    monkeypatch.setattr(tt, "_is_research_namespace", lambda: False)
    yield
    get_settings.cache_clear()


def test_live_non_rtk_api_never_touches_promax_when_others_exist():
    px, tj = Fake("promax"), Fake("teajoin", fail=FailureCategory.TIMEOUT)
    r = make(tj, px)
    with pytest.raises(TushareProviderError):
        r.request("daily")
    assert px.calls == []  # teajoin 失败也不动 promax 额度


def test_rt_k_keeps_promax():
    px, tj = Fake("promax"), Fake("teajoin")
    r = make(px, tj)
    assert r.request("rt_k")["data"]["items"] and px.calls == ["rt_k"]


def test_sole_promax_api_still_served_live():
    px = Fake("promax")
    assert make(px).request("anns_d")["data"]["items"] and px.calls == ["anns_d"]


def test_research_namespace_never_uses_promax_even_if_sole(monkeypatch):
    monkeypatch.setattr(tt, "_is_research_namespace", lambda: True)
    px = Fake("promax")
    with pytest.raises(TushareProviderError):
        make(px).request("anns_d")
    assert px.calls == []


def test_switches_restore_old_behaviour(monkeypatch):
    monkeypatch.setenv("ASHARE_TUSHARE_PROMAX_RTK_RESERVED", "false")
    monkeypatch.setenv("ASHARE_TUSHARE_PROMAX_RESEARCH_ENABLED", "true")
    get_settings.cache_clear()
    px, tj = Fake("promax"), Fake("teajoin", fail=FailureCategory.TIMEOUT)
    assert make(tj, px).request("daily")["data"]["items"] and px.calls == ["daily"]
    monkeypatch.setattr(tt, "_is_research_namespace", lambda: True)
    px2 = Fake("promax")
    assert make(px2).request("anns_d")["data"]["items"]


def test_live_intraday_lead_timeout_is_tightened_but_research_is_not():
    spec = LeaderSpec(provider="teajoin", apis=frozenset({"daily", "cyq_perf"}), heavy_apis=frozenset({"cyq_perf"}),
                      read_timeout=40.0, heavy_timeout=90.0, intraday_read_timeout=15.0)
    assert spec.timeout_for("daily") == 40.0
    assert spec.timeout_for("daily", live_intraday=True) == 15.0
    assert spec.timeout_for("cyq_perf", live_intraday=True) == 30.0
    assert spec.timeout_for("cyq_perf") == 90.0


def test_real_router_teajoin_leads_stk_mins_and_no_intraday_yield(monkeypatch):
    from app.services import tushare_client as tc

    monkeypatch.setenv("ASHARE_TUSHARE_TEAJOIN_TOKEN", "x" * 8)
    monkeypatch.setenv("ASHARE_TUSHARE_PROMAX_API_KEY", "y" * 8)
    monkeypatch.setenv("ASHARE_TUSHARE_QUANTDATA_TOKEN", "z" * 8)
    get_settings.cache_clear()
    router, _ = tc._build_provider_router()
    spec = router._leaders["teajoin"]
    assert "stk_mins" in spec.apis
    assert spec.intraday_sensitive == frozenset()  # 盘中让位默认关闭
    assert router._apply_promax_reserve("stk_mins", router._ordered_providers("stk_mins"))[0].name == "teajoin"
    assert "promax" not in [p.name for p in router._apply_promax_reserve("stk_mins", router._ordered_providers("stk_mins"))]
    assert router._ordered_providers("rt_k")[0].name == "promax"
    # 回到旧让位
    monkeypatch.setenv("ASHARE_TUSHARE_TEAJOIN_INTRADAY_YIELD", "true")
    get_settings.cache_clear()
    router2, _ = tc._build_provider_router()
    assert "daily" in router2._leaders["teajoin"].intraday_sensitive


def test_teajoin_stk_mins_uses_minute_contract_gate(monkeypatch):
    from app.services import tushare_client as tc

    monkeypatch.setenv("ASHARE_TUSHARE_TEAJOIN_TOKEN", "x" * 8)
    get_settings.cache_clear()
    router, _ = tc._build_provider_router()
    tj = router._providers["teajoin"]
    assert isinstance(tj, tc._TeajoinHttpProvider)
    good = {"data": {"fields": ["trade_time"], "items": [["2026-10-23 09:30:00"], ["2026-10-23 15:00:00"]]}}
    bad = {"data": {"fields": ["trade_time"], "items": [["2026-10-23 09:30:00"], ["2026-10-23 09:30:00"]]}}
    assert tc._promax_minute_contract_issue(good) is None
    assert tc._promax_minute_contract_issue(bad) == "duplicate_minute"
