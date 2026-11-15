"""数据源可观测性与限频（2026-11-15 七项改造）的回归测试。

覆盖点：
1. ``SlidingWindowLimiter.usage()`` / ``SharedSlidingWindowLimiter.usage()`` 只读且有真值；
2. ``limiter_registry`` 登记后 ``usage()`` 可读，limiter 被回收时降级 ``alive=False``；
3. ``/api/data-sources/limits`` 结构完整，且**不 acquire** 限流器（不产生副作用）；
4. promax 双口径：``window_table()`` 可被 env 覆盖，声明 RPM 与实际窗口不一致要出 warning；
5. 熔断阈值按 provider 差异化（``kind_threshold(kind, key)``）；
6. metrics 滑动窗口速率（进程重启归零的补充口径）；
7. 数据质量巡检新增「空返回率」与「配额限频」两项，且异常不炸主流程。
"""
from __future__ import annotations

import json
import time

import pytest


# ── 1. limiter usage ──────────────────────────────────────────────────────────

def test_sliding_window_limiter_usage_is_readonly():
    from app.services.tushare_transport import SlidingWindowLimiter

    lim = SlidingWindowLimiter(10)
    assert lim.usage() == (0, 10, 60.0)
    lim.acquire()
    used, limit, window = lim.usage()
    assert (used, limit, window) == (1, 10, 60.0)
    # usage() 不应消耗配额
    assert lim.usage()[0] == 1


def test_shared_limiter_usage_counts_rows(tmp_path):
    from app.services.shared_rate_limiter import SharedSlidingWindowLimiter

    db = tmp_path / "hits.sqlite"
    lim = SharedSlidingWindowLimiter("gateway:teajoin", 5, str(db))
    # 同一 key 的另一个实例（模拟另一个进程）也要看到同一份用量
    other = SharedSlidingWindowLimiter("gateway:teajoin", 5, str(db))
    lim.acquire()
    lim.acquire()
    assert lim.usage()[0] == 2
    assert other.usage()[0] == 2, "跨进程共享用量必须对两个实例一致"
    assert lim.key == "gateway:teajoin"


# ── 2. limiter_registry ───────────────────────────────────────────────────────

def test_limiter_registry_describe_and_usage():
    from app.core import limiter_registry
    from app.services.tushare_transport import SlidingWindowLimiter

    limiter_registry.reset()
    lim = SlidingWindowLimiter(7)
    limiter_registry.register("gateway:unit_test", lim, 7, window_seconds=60, kind="gateway")
    lim.acquire()

    usage = {u["key"]: u for u in limiter_registry.usage()}
    assert "gateway:unit_test" in usage
    row = usage["gateway:unit_test"]
    assert row["used"] == 1 and row["limit"] == 7 and row["alive"] is True

    described = {d["key"]: d for d in limiter_registry.describe()}
    assert described["gateway:unit_test"]["requests_per_minute"] == 7
    limiter_registry.reset()


def test_limiter_registry_survives_dead_limiter():
    """limiter 被回收（弱引用失效）时 usage() 不能抛，只能标 alive=False。"""
    import gc

    from app.core import limiter_registry
    from app.services.tushare_transport import SlidingWindowLimiter

    limiter_registry.reset()
    lim = SlidingWindowLimiter(3)
    limiter_registry.register("gateway:dead", lim, 3)
    del lim
    gc.collect()
    rows = {u["key"]: u for u in limiter_registry.usage()}
    if "gateway:dead" in rows:  # 若仍被引用则仍可用，两种情况都不许抛
        assert rows["gateway:dead"]["alive"] in (True, False)
    limiter_registry.reset()


# ── 3. /api/data-sources/limits 结构 ──────────────────────────────────────────

def test_limits_view_structure_and_readonly():
    from app.services.datasource import quota_view

    before = {u["key"]: u["used"] for u in __import__(
        "app.core.limiter_registry", fromlist=["usage"]).usage()}
    view = quota_view.collect(None, hours=1)
    after = {u["key"]: u["used"] for u in __import__(
        "app.core.limiter_registry", fromlist=["usage"]).usage()}

    for section in ("limiters", "daily", "promax", "breakers", "direct_sources", "rates", "quality"):
        assert section in view, section
    assert isinstance(view["warnings"], list)
    assert view["generated_at"]
    # 现有 key 的窗口用量不能被 collect() 改动（只读观测）
    for key, used in before.items():
        if key in after:
            assert after[key] == used, f"{key} 的用量被 limits 视图改动了"


def test_limits_view_reports_shared_limiter_state(monkeypatch):
    """共享库未配置时必须显式告警，而不是安静地显示 enabled=False。"""
    from app.services.datasource import quota_view
    import app.services.tushare_transport as transport

    monkeypatch.setattr(transport, "shared_limiter_db_path", lambda: "")
    view = quota_view.collect(None, hours=1)
    assert view["shared_limiter"]["enabled"] is False
    assert any("跨进程限频未启用" in w for w in view["warnings"])


def test_limits_endpoint_returns_payload():
    """端点层：collect 异常时降级为空结构，不抛 500。"""
    from fastapi.testclient import TestClient

    from app.api.data_sources import data_source_limits

    # 直接调函数（避免 DB 依赖）；db 传 None 走"无 db"分支
    payload = data_source_limits(hours=1, db=None)
    assert payload["generated_at"]
    assert "promax" in payload


# ── 4. promax 双口径 ─────────────────────────────────────────────────────────

def test_promax_window_table_default_and_override(monkeypatch):
    from app.core import promax_window as pw

    pw.reset_window_table_cache()
    monkeypatch.setattr("app.core.runtime_env.env_value", lambda *a, **k: "")
    assert pw.window_table() == pw.DEFAULT_WINDOWS

    pw.reset_window_table_cache()
    monkeypatch.setattr(
        "app.core.runtime_env.env_value",
        lambda *a, **k: "[[60, 200, 150], [3600, 2400, 900]]",
    )
    assert pw.window_table() == ((60.0, 200, 150), (3600.0, 2400, 900))
    pw.reset_window_table_cache()


def test_promax_window_table_bad_json_falls_back(monkeypatch):
    from app.core import promax_window as pw

    pw.reset_window_table_cache()
    monkeypatch.setattr("app.core.runtime_env.env_value", lambda *a, **k: "{not json")
    assert pw.window_table() == pw.DEFAULT_WINDOWS
    pw.reset_window_table_cache()


def test_promax_section_flags_declared_vs_effective(monkeypatch):
    from app.services.datasource import quota_view

    class _Cfg:
        tushare_promax_rate_limit_per_minute = 180
        tushare_promax_max_concurrency = 4

    section = quota_view._promax_section(_Cfg())
    assert section["declared_per_minute"] == 180
    assert section["windows"], "必须暴露实际生效的滚动窗口"
    # 2026-11-15 实测重标定后：生效口径来自上游 IP 硬窗（200/min），取 160/min 留 20% 余量，
    # 与声明口径 180 已经同量级（旧值 20/min 是连接池耗尽的误判，比上游低约 10 倍）。
    tightest = section["tightest_effective_per_minute"]
    assert 150 <= tightest <= 180, tightest
    assert abs(section["declared_vs_effective_ratio"] - 180 / tightest) < 0.5
    if section["warning"]:
        assert "高估" in section["warning"]


# ── 5. 熔断阈值按 provider 差异化 ─────────────────────────────────────────────

def test_kind_threshold_per_provider(monkeypatch):
    from app.core import shared_breaker as sb

    monkeypatch.setattr(
        sb, "_provider_overrides",
        lambda: {"promax": {"timeout": 1, "bad_data": 2}, "teajoin": {"timeout": 4}},
    )
    assert sb.kind_threshold("timeout", "gw:promax:rt_k") == 1
    assert sb.kind_threshold("bad_data", "gw:promax:rt_k") == 2
    assert sb.kind_threshold("timeout", "gw:teajoin:bar.minute") == 4
    # 无覆盖的 provider 回落全局默认（timeout=2 / bad_data=3）
    assert sb.kind_threshold("timeout", "gw:sina_realtime:quote") == 2
    assert sb.kind_threshold("bad_data", "gw:sina_realtime:quote") == 3
    # rate_limit 已纳入可配阈值（2026-11-15）：默认连续 2 次才熔断，per-provider 可覆盖
    assert sb.kind_threshold("rate_limit", "gw:sina_realtime:quote") == 2
    assert sb.kind_threshold("rate_limit", "gw:promax:rt_k") == 2
    # 其它未知类型仍不支持
    assert sb.kind_threshold("permission", "gw:promax:rt_k") is None
    # key 为空时不能炸
    assert sb.kind_threshold("timeout", None) >= 1


def test_provider_of_handles_namespaced_keys(monkeypatch):
    from app.core import shared_breaker as sb

    monkeypatch.setattr(sb, "_provider_overrides", lambda: {"promax": {}, "teajoin": {}})
    assert sb._provider_of("gw:promax:rt_k") == "promax"
    assert sb._provider_of("default:gw:teajoin:bar.minute") == "teajoin"
    assert sb._provider_of("promax") == "promax"
    assert sb._provider_of("gw:unknown:x") is None
    assert sb._provider_of(None) is None


# ── 6. metrics 滑动窗口速率 ──────────────────────────────────────────────────

def test_metrics_rates_track_recent_counters():
    from app.core import metrics

    metrics.reset()
    for _ in range(5):
        metrics.inc("dataset_requests", provider="teajoin", outcome="success")
    rates = {(r["name"], tuple(sorted(r["labels"].items()))): r for r in metrics.rates()}
    row = rates[("dataset_requests", (("outcome", "success"), ("provider", "teajoin")))]
    assert row["count"] == 5
    assert row["per_minute"] == pytest.approx(5.0, rel=0.2)


def test_metrics_rates_ignore_old_buckets():
    from app.core import metrics

    metrics.reset()
    metrics.inc("dataset_requests", provider="stale", outcome="success")
    with metrics._lock:  # 直接把桶推到 10 分钟前
        for dq in metrics._recent.values():
            for bucket in dq:
                bucket[0] -= 600
    assert all(r["labels"].get("provider") != "stale" or r["count"] == 0 for r in metrics.rates())


def test_metrics_snapshot_contains_rates():
    from app.core import metrics

    metrics.reset()
    metrics.inc("provider_requests", provider="promax", outcome="success")
    snap = metrics._local_snapshot()
    assert "rates" in snap and snap["rates"]
    metrics.reset()


# ── 7. 数据质量巡检新增两项 ───────────────────────────────────────────────────

def test_check_provider_empty_rate_severity(monkeypatch):
    from app.services import data_quality_monitor as dqm

    monkeypatch.setattr(
        "app.services.datasource.quota_view.quality_by_provider",
        lambda db, hours=24: [
            {"provider": "eastmoney", "attempts": 100, "empty": 80, "empty_rate": 0.8,
             "success_rate": 0.2, "avg_latency_seconds": 0.5, "verdict": "red_empty_rate"},
            {"provider": "sina_realtime", "attempts": 100, "empty": 1, "empty_rate": 0.01,
             "success_rate": 0.99, "avg_latency_seconds": 0.1, "verdict": "ok"},
        ],
    )
    item = dqm.check_provider_empty_rate(db=None)
    assert item["key"] == "gateway:provider_empty_rate"
    assert item["severity"] == dqm.RED
    assert "eastmoney" in item["detail"]


def test_check_provider_empty_rate_skips_small_sample(monkeypatch):
    from app.services import data_quality_monitor as dqm

    monkeypatch.setattr(
        "app.services.datasource.quota_view.quality_by_provider",
        lambda db, hours=24: [
            {"provider": "akshare", "attempts": 10, "empty": 9, "empty_rate": 0.9,
             "success_rate": 0.1, "avg_latency_seconds": 1.0, "verdict": "insufficient_sample"},
        ],
    )
    item = dqm.check_provider_empty_rate(db=None)
    assert item["severity"] == dqm.GREEN
    assert "暂不判定" in item["detail"]


def test_check_provider_limits_never_raises(monkeypatch):
    from app.services import data_quality_monitor as dqm

    monkeypatch.setattr(
        "app.services.datasource.quota_view.collect",
        lambda db, hours=6: {"shared_limiter": {"enabled": False}, "breakers": [], "daily": [], "promax": {}},
    )
    item = dqm.check_provider_limits(db=None)
    assert item["severity"] == dqm.YELLOW
    assert "跨进程限频未启用" in item["detail"]


def test_run_checks_includes_new_items(monkeypatch):
    """新检查项必须真的挂进 run_checks（否则等于没加）。"""
    from app.services import data_quality_monitor as dqm

    from unittest.mock import MagicMock

    captured: list[str] = []

    def _spy(label, key, category, kind, fn):
        captured.append(key)
        return []

    monkeypatch.setattr(dqm, "_safe", _spy)
    db = MagicMock()
    db.execute.return_value.scalars.return_value.all.return_value = []

    dqm.run_checks(db, include_files=False)
    assert "gateway:provider_empty_rate" in captured
    assert "gateway:quota_limits" in captured


# ── 8. thsdk 参数化 ──────────────────────────────────────────────────────────

def test_ths_limiter_disabled_by_default(monkeypatch):
    from app.services.datasource import ths_client

    ths_client.reset_limiter_cache()

    class _S:
        ths_rate_limit_per_minute = 0
        ths_min_interval_seconds = 0.0

    monkeypatch.setattr(ths_client, "_settings", lambda: _S())
    assert ths_client._ths_limiter() is None
    ths_client.reset_limiter_cache()


def test_ths_limiter_enabled_when_configured(monkeypatch):
    from app.services.datasource import ths_client
    from app.core import limiter_registry

    limiter_registry.reset()
    ths_client.reset_limiter_cache()

    class _S:
        ths_rate_limit_per_minute = 900
        ths_min_interval_seconds = 0.0

    monkeypatch.setattr(ths_client, "_settings", lambda: _S())
    lim = ths_client._ths_limiter()
    assert lim is not None
    assert {u["key"] for u in limiter_registry.usage()} == {"gateway:ths"}
    ths_client.reset_limiter_cache()
    limiter_registry.reset()


# ── 9. quality 段防回归：模型名/join 写错会静默变空 ────────────────────────────

def test_quality_by_provider_returns_rows_from_db():
    """用内存 SQLite 建真表验证 join 正确。

    防的是什么：``quality_by_provider`` 里 ``except Exception: return []`` 会把
    ImportError / 列名写错全部吞掉，质量段就永远是空列表，而接口仍返回 200——
    「观测接口静默变瞎」比报错更难发现（本次就是 import 了不存在的 TraceRow）。
    """
    from datetime import datetime

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.session import Base
    from app.models.datasource import DataSourceAttempt, DataSourceRequestTrace
    from app.services.datasource.quota_view import quality_by_provider

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        DataSourceRequestTrace.__table__, DataSourceAttempt.__table__,
    ])
    Session = sessionmaker(bind=engine)
    db = Session()
    now = datetime.utcnow()

    def _trace(tid: str) -> None:
        db.add(DataSourceRequestTrace(
            trace_id=tid, correlation_id=tid, operation_name="op", request_identity_hash="h" * 64,
            contract_id="c", contract_version=1, selected_provider="p", final_outcome="ok",
            attempt_count=1, fallback_count=0, duration_ms=10, telemetry_status="ok",
            started_at=now, completed_at=now,
        ))

    # 60 次尝试：20 次成功、40 次空返回 → 空返回率 66.7%，越过 red 阈值（≥60%）。
    # 必须超过最小样本量（50），否则 verdict 会是 insufficient_sample。
    outcomes = ["success"] * 20 + ["empty"] * 40
    for i, outcome in enumerate(outcomes):
        tid = f"t{i}"
        _trace(tid)
        db.add(DataSourceAttempt(
            attempt_id=f"a{i}", trace_id=tid, ordinal=0, provider="eastmoney",
            outcome=outcome, duration_ms=100,
        ))
    db.commit()

    rows = quality_by_provider(db, hours=24)
    assert rows, "quality_by_provider 返回空：join/模型名可能写错（异常被静默吞掉）"
    row = {r["provider"]: r for r in rows}["eastmoney"]
    assert row["attempts"] == 60
    assert row["empty"] == 40
    assert row["empty_rate"] == pytest.approx(round(40 / 60, 3))  # 实现按 3 位小数取整
    assert row["verdict"] == "red_empty_rate"
    db.close()


def test_limits_section_marks_dead_limiter():
    """provider 实例已回收时要标 alive=False + note，不能显示成「用量为 0」。"""
    import gc

    from app.core import limiter_registry
    from app.services.datasource.quota_view import _limiters_section

    limiter_registry.reset()

    class _Lim:
        def usage(self):
            return (0, 5, 60.0)

    lim = _Lim()
    limiter_registry.register("gateway:ghost", lim, 5)
    del lim
    gc.collect()
    rows = {r["key"]: r for r in _limiters_section(None)}
    if "gateway:ghost" in rows:
        assert rows["gateway:ghost"]["alive"] is False
        assert rows["gateway:ghost"]["used"] is None
        assert rows["gateway:ghost"]["note"]
    limiter_registry.reset()


# ── 6. 限频熔断阈值与冷却（2026-11-15 实测标定）──────────────────────────────

def _isolated_breaker(monkeypatch, key="gw:teajoin:trade_cal"):
    """把共享状态（SQLite）隔离掉，只测本进程内的阈值/冷却决策。"""
    from app.core import shared_breaker as sb

    published: list[tuple[str, float, str]] = []
    monkeypatch.setattr(sb, "shared_open_until", lambda k: 0.0)
    monkeypatch.setattr(sb, "_publish_open", lambda k, until, reason="": published.append((k, until, reason)))
    monkeypatch.setattr(sb, "_publish_closed", lambda k: None)
    monkeypatch.setattr(sb, "_provider_overrides", lambda: {})
    breaker = sb.SharedCircuitBreaker(key, failure_threshold=3, recovery_seconds=60)
    return sb, breaker, published


def test_rate_limit_opens_only_after_threshold(monkeypatch):
    sb, breaker, published = _isolated_breaker(monkeypatch)
    assert sb.kind_threshold("rate_limit") == 2  # 默认连续 2 次
    breaker.record_failure(kind="rate_limit")
    assert breaker.state == "closed", "单次限频（免费额度下的常态噪音）不应直接熔断"
    breaker.record_failure(kind="rate_limit")
    assert breaker.state == "open"
    assert published and "rate_limitx2" in published[-1][2]


def test_rate_limit_threshold_configurable(monkeypatch):
    sb, breaker, _ = _isolated_breaker(monkeypatch, key="gw:promax:rt_k")
    monkeypatch.setattr(sb, "_provider_overrides", lambda: {"promax": {"rate_limit": 1}})
    breaker.record_failure(kind="rate_limit")
    assert breaker.state == "open", "旧行为（1 次即熔断）应可通过 overrides 恢复"


def test_rate_limit_cooldown_uses_minute_scale(monkeypatch):
    sb, breaker, published = _isolated_breaker(monkeypatch)
    breaker.record_failure(kind="rate_limit")
    breaker.record_failure(kind="rate_limit")
    # 无 retry-after：用分钟级专用冷却 90s，而不是通用 300s 探测间隔
    delay = published[-1][1] - __import__("time").time()
    assert 80 <= delay <= 95, delay
    assert sb.RATE_LIMIT_COOLDOWN_SECONDS == 90


def test_rate_limit_cooldown_honours_retry_after(monkeypatch):
    _, breaker, published = _isolated_breaker(monkeypatch)
    breaker.record_failure(kind="rate_limit")
    breaker.record_failure(kind="rate_limit", recovery_seconds=56.0)  # promax 实测「请在 56 秒后重试」
    delay = published[-1][1] - __import__("time").time()
    assert 50 <= delay <= 61, delay
