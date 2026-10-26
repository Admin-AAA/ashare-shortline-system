"""teajoin 做主源（LeaderSpec）：链首排序、盘中延迟敏感接口让路、失败/超时快速降级、不健康不做主源、总开关。"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.services import tushare_transport as tt
from app.services.tushare_transport import (
    FailureCategory,
    LeaderSpec,
    ProviderFailure,
    TushareProviderRouter,
)

SPEC = LeaderSpec(
    provider="teajoin",
    apis=frozenset({"cyq_perf", "daily", "fina_indicator_vip"}),
    intraday_sensitive=frozenset({"daily"}),
    heavy_apis=frozenset({"fina_indicator_vip"}),
    read_timeout=40.0,
    heavy_timeout=90.0,
)


class Fake:
    def __init__(self, name, *, fail=None, health=1.0):
        self.name = name
        self.fail = fail
        self._health = health
        self.calls = []
        self.config = SimpleNamespace(rate_limit_per_minute=60, priority=1)

    def supports(self, api):
        return True

    def lb_health(self, api):
        return self._health

    def lb_headroom(self, api):
        return 1.0

    def close(self):
        pass

    def health_snapshot(self):
        return {}

    def request(self, api, *, params=None, fields=""):
        self.calls.append((api, tt._LEAD_CALL.get()))
        if self.fail:
            raise ProviderFailure(provider=self.name, api_name=api, category=self.fail, retryable=False, fallback_allowed=True)
        return {"data": {"items": [[1]], "fields": ["x"]}}


def router(*providers, leaders=(SPEC,)):
    return TushareProviderRouter(list(providers), leaders=leaders)


def at(monkeypatch, hh, mm, research=False, weekday=0):
    # 2026-10-26 是周一
    day = 26 + weekday
    now = datetime(2026, 10, day, hh, mm, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(tt, "_in_research_window", lambda n=None: tt.RESEARCH_INTRADAY_WINDOW[0] <= (now.hour, now.minute) < tt.RESEARCH_INTRADAY_WINDOW[1] and now.weekday() < 5)
    monkeypatch.setattr(tt, "_is_research_namespace", lambda: research)


def order(r, api):
    return [p.name for p in r._ordered_providers(api)]


def test_leader_goes_first_off_hours(monkeypatch):
    at(monkeypatch, 20, 0)
    r = router(Fake("promax"), Fake("quantdata"), Fake("teajoin"))
    assert order(r, "cyq_perf")[0] == "teajoin"
    assert order(r, "daily")[0] == "teajoin"
    assert order(r, "rt_k")[0] != "teajoin"  # 不在名单：保持原链路


def test_intraday_live_sensitive_api_demoted_but_others_lead(monkeypatch):
    at(monkeypatch, 10, 30)
    r = router(Fake("promax"), Fake("quantdata"), Fake("teajoin"))
    assert order(r, "daily")[-1] == "teajoin"        # 盘中 live：延迟敏感接口让原源先走
    assert order(r, "cyq_perf")[0] == "teajoin"      # 非敏感接口照常做主源


def test_intraday_research_namespace_leads_to_save_live_quota(monkeypatch):
    at(monkeypatch, 10, 30, research=True)
    r = router(Fake("promax"), Fake("quantdata"), Fake("teajoin"))
    assert order(r, "daily")[0] == "teajoin"


def test_unhealthy_leader_not_first(monkeypatch):
    at(monkeypatch, 20, 0)
    r = router(Fake("promax"), Fake("teajoin", health=0.1))
    assert order(r, "cyq_perf")[0] == "promax"


def test_lead_failure_falls_back_with_short_timeout(monkeypatch):
    at(monkeypatch, 20, 0)
    tj, px = Fake("teajoin", fail=FailureCategory.TIMEOUT), Fake("promax")
    r = router(px, tj)
    env = r.request("cyq_perf")
    assert env["data"]["items"]
    assert tj.calls and tj.calls[0][1] == (40.0, 1)  # 主源：25s 读超时 + 只试 1 次
    assert px.calls and px.calls[0][1] is None       # 备源不受影响


def test_heavy_api_uses_heavy_timeout(monkeypatch):
    at(monkeypatch, 20, 0)
    tj = Fake("teajoin")
    r = router(Fake("promax"), tj)
    r.request("fina_indicator_vip")
    assert tj.calls[0][1] == (90.0, 1)


def test_no_leaders_keeps_original_order(monkeypatch):
    at(monkeypatch, 20, 0)
    r = router(Fake("promax"), Fake("teajoin"), leaders=())
    assert order(r, "cyq_perf") == ["promax", "teajoin"]


def test_lead_scope_caps_http_timeout_and_attempts():
    assert tt._LEAD_CALL.get() is None
    with tt._lead_scope(7.0, 1):
        assert tt._LEAD_CALL.get() == (7.0, 1)
    assert tt._LEAD_CALL.get() is None


def test_capability_registry_consistent_with_probe_and_excludes_realtime():
    import json
    from pathlib import Path

    from app.services.datasource import teajoin_capability as cap

    assert not ({"rt_k", "rt_min", "stk_mins", "anns_d", "news"} & cap.TEAJOIN_APIS)
    assert cap.INTRADAY_SENSITIVE <= cap.TEAJOIN_APIS and cap.HEAVY_APIS <= cap.TEAJOIN_APIS and cap.FAST_APIS <= cap.TEAJOIN_APIS
    docs = Path(__file__).resolve().parents[2] / "docs"
    r1 = json.loads((docs / "teajoin_catalog_probe.json").read_text())["results"]
    r2 = {r["api"]: r for r in json.loads((docs / "teajoin_catalog_probe_r2.json").read_text())["results"]}
    final = {r["api"]: r2.get(r["api"], r) for r in r1}
    unavailable = {a for a, r in final.items() if r["status"] not in ("ok", "empty")}
    assert not unavailable, unavailable
    assert set(final) <= cap.TEAJOIN_APIS  # 目录里实测可用的都已登记


def test_real_router_builds_with_teajoin_leader_and_keeps_realtime_chain(monkeypatch):
    from app.core.config import get_settings
    from app.services import tushare_client as tc

    monkeypatch.setenv("ASHARE_TUSHARE_TEAJOIN_TOKEN", "x" * 8)
    monkeypatch.setenv("ASHARE_TUSHARE_PROMAX_API_KEY", "y" * 8)
    monkeypatch.setenv("ASHARE_TUSHARE_QUANTDATA_TOKEN", "z" * 8)
    get_settings.cache_clear()
    try:
        router, _ = tc._build_provider_router()
        assert "teajoin" in router._leaders
        # 盘后：批量接口 teajoin 排首；实时/分钟接口链路不含 teajoin
        monkeypatch.setattr(tt, "_in_research_window", lambda n=None: False)
        assert [p.name for p in router._ordered_providers("cyq_perf")][0] == "teajoin"
        for api in ("rt_k", "rt_min", "stk_mins", "anns_d"):
            assert "teajoin" not in [p.name for p in router._ordered_providers(api)]
    finally:
        get_settings.cache_clear()


def test_lead_switch_off_restores_tail_fallback(monkeypatch):
    from app.core.config import get_settings
    from app.services import tushare_client as tc

    monkeypatch.setenv("ASHARE_TUSHARE_TEAJOIN_TOKEN", "x" * 8)
    monkeypatch.setenv("ASHARE_TUSHARE_PROMAX_API_KEY", "y" * 8)
    monkeypatch.setenv("ASHARE_TUSHARE_TEAJOIN_LEAD", "false")
    get_settings.cache_clear()
    try:
        router, _ = tc._build_provider_router()
        assert not router._leaders
        assert [p.name for p in router._ordered_providers("cyq_perf")][-1] == "teajoin"
    finally:
        get_settings.cache_clear()
