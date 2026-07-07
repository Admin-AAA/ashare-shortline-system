"""T4: 驾驶舱概览端点的 stale-while-revalidate 缓存行为验证。

避免每次页面加载/轮询都重算（冷缓存全量池扫描约 1.8s + run 查询 ~268ms），
确保重复请求在 TTL 内直接命中缓存、过期时先返回旧值再后台刷新。
"""
import threading
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


def test_overview_stale_cache_no_deadlock():
    """回归 (T7): 缓存过期时 overview 必须在释放锁之后再调度后台刷新。

    旧实现在持有 _OVERVIEW_LOCK 时调用 _schedule_overview_refresh，而后者内部
    再次获取同一把非重入 threading.Lock -> 死锁。生产表现为
    /api/dashboard/overview 每次请求卡死、直到 curl 10s 超时。

    与 test_overview_cache_expiry_returns_stale_and_schedules_refresh 不同，本测试
    **不 mock** _schedule_overview_refresh，直接走真实锁路径复现生产死锁。
    """
    _reset_cache()
    sentinel = object()
    # 预置一个「过期但存在」的缓存：模拟进程运行 > TTL 后的稳态
    dashboard_mod._OVERVIEW_CACHE["data"] = sentinel
    dashboard_mod._OVERVIEW_CACHE["ts"] = time.monotonic() - (
        dashboard_mod._OVERVIEW_TTL_SECONDS + 5
    )

    built = []
    original_build = dashboard_mod._build_overview_payload

    def fake_build(db):
        built.append(object())
        return built[-1]

    # 仅 mock 重算（避免后台刷新线程真的扫库），但保留真实的 _schedule_overview_refresh
    dashboard_mod._build_overview_payload = fake_build
    try:
        db = MagicMock()
        result: dict = {}
        err: dict = {}

        def target():
            try:
                result["v"] = dashboard_mod.overview(db=db)
            except Exception as e:  # noqa: BLE001
                err["e"] = e

        t = threading.Thread(target=target, daemon=True)
        t.start()
        t.join(timeout=3.0)
        assert not t.is_alive(), "过期缓存路径不应死锁（旧 bug 会卡在 _OVERVIEW_LOCK 上）"
        assert "e" not in err, f"overview 抛异常: {err.get('e')}"
        assert result.get("v") is sentinel, "过期时应立即返回旧缓存值，不阻塞请求"
    finally:
        dashboard_mod._build_overview_payload = original_build
        _reset_cache()
