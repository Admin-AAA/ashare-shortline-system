"""T4: 驾驶舱概览端点的 stale-while-revalidate 缓存行为验证。

避免每次页面加载/轮询都重算（冷缓存全量池扫描约 1.8s + run 查询 ~268ms），
确保重复请求在 TTL 内直接命中缓存、过期时先返回旧值再后台刷新。
"""
import time
from unittest.mock import MagicMock

from app.api import dashboard as dashboard_mod


def _reset_cache():
    dashboard_mod._OVERVIEW_CACHE["data"] = None
    dashboard_mod._OVERVIEW_CACHE["ts"] = 0.0
    dashboard_mod._OVERVIEW_REFRESHING = False


def test_overview_cache_serves_repeated_requests_without_recompute():
    _reset_cache()
    calls = {"n": 0}
    sentinel = object()

    def fake_build(db):
        calls["n"] += 1
        return sentinel

    original = dashboard_mod._build_overview_payload
    dashboard_mod._build_overview_payload = fake_build
    try:
        db = MagicMock()
        first = dashboard_mod.overview(db=db)
        second = dashboard_mod.overview(db=db)
    finally:
        dashboard_mod._build_overview_payload = original

    assert first is sentinel
    assert second is sentinel
    assert calls["n"] == 1, "重计算路径在 TTL 内应只执行一次"


def test_overview_cache_expiry_returns_stale_and_schedules_refresh():
    _reset_cache()
    built = []
    scheduled = {"n": 0}

    def fake_build(db):
        built.append(object())
        return built[-1]

    def fake_schedule():
        scheduled["n"] += 1

    original_build = dashboard_mod._build_overview_payload
    original_sched = dashboard_mod._schedule_overview_refresh
    dashboard_mod._build_overview_payload = fake_build
    dashboard_mod._schedule_overview_refresh = fake_schedule
    try:
        db = MagicMock()
        first = dashboard_mod.overview(db=db)  # 冷缓存 -> 同步计算并写入
        # 强制过期
        dashboard_mod._OVERVIEW_CACHE["ts"] = (
            time.monotonic() - (dashboard_mod._OVERVIEW_TTL_SECONDS + 1)
        )
        second = dashboard_mod.overview(db=db)  # 过期 -> 返回旧值并后台刷新
    finally:
        dashboard_mod._build_overview_payload = original_build
        dashboard_mod._schedule_overview_refresh = original_sched

    assert first is second, "过期时应先返回旧值，不阻塞请求"
    assert scheduled["n"] == 1, "过期时应触发一次后台刷新"
    assert len(built) == 1, "过期时不应同步重算"
