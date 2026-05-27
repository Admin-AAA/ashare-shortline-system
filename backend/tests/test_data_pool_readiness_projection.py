from app.api import data_sources as data_sources_api
from app.services.data_pool.health_projection import summarize_alerts, summarize_tasks
from app.services.data_pool.readiness import summarize_pool_status


def test_summarize_pool_status_reports_ready_counts():
    status = {
        "unified_quote_count": 4300,
        "active_unified_quote_count": 4100,
        "eligible_quote_count": 2600,
        "daily_bar_coverage_rate": 0.92,
        "eligible_daily_bar_coverage_rate": 0.96,
        "daily_bar_missing_count": 120,
        "daily_bar_stale_count": 12,
        "daily_bar_short_history_count": 35,
        "recommendation_readiness": {
            "status": "ready",
            "can_run_local_recommendation": True,
            "messages": ["本地池可运行"],
        },
    }

    summary = summarize_pool_status(status)

    assert summary["status"] == "ready"
    assert summary["quote_total"] == 4300
    assert summary["eligible_quote_count"] == 2600
    assert summary["can_run_local_recommendation"] is True
    assert summary["bar_coverage_pct"] == 92.0
    assert summary["missing_bar_count"] == 120
    assert summary["stale_bar_count"] == 12
    assert summary["short_history_count"] == 35


def test_summarize_pool_status_accepts_local_pool_api_shape():
    status = {
        "summary": {
            "unified_quote_count": 4300,
            "active_unified_quote_count": 4100,
            "eligible_quote_count": 2600,
            "daily_bar_coverage_rate": 0.92,
            "eligible_daily_bar_coverage_rate": 0.96,
        },
        "recommendation_readiness": {
            "status": "ready",
            "can_run_local_recommendation": True,
            "messages": ["本地池可运行"],
        },
    }

    summary = summarize_pool_status(status)

    assert summary["quote_total"] == 4300
    assert summary["eligible_quote_count"] == 2600
    assert summary["bar_coverage_pct"] == 92.0


def test_summarize_pool_status_keeps_percent_values_from_local_pool_api():
    status = {
        "summary": {
            "unified_quote_count": 4300,
            "active_unified_quote_count": 4100,
            "eligible_quote_count": 2600,
            "daily_bar_coverage_rate": 75.5,
            "eligible_daily_bar_coverage_rate": 88.25,
        },
        "recommendation_readiness": {"status": "blocked", "can_run_local_recommendation": False},
    }

    summary = summarize_pool_status(status)

    assert summary["bar_coverage_pct"] == 75.5
    assert summary["eligible_bar_coverage_pct"] == 88.25


def test_summarize_pool_status_preserves_structured_gap_examples():
    status = {
        "summary": {
            "unified_quote_count": 4300,
            "active_unified_quote_count": 4100,
            "eligible_quote_count": 2600,
            "daily_bar_coverage_rate": 0.75,
            "eligible_daily_bar_coverage_rate": 0.82,
        },
        "recommendation_readiness": {
            "status": "warning",
            "can_run_local_recommendation": True,
            "messages": ["当前存在数据缺口：短历史 1 只。"],
            "kline": {
                "examples": [
                    {
                        "code": "000001",
                        "name": "平安银行",
                        "reason": "本地日K不足30条",
                        "bar_count": 12,
                        "latest_bar_date": "2026-05-20",
                        "quote_date": "2026-05-22",
                    }
                ],
            },
        },
    }

    summary = summarize_pool_status(status)

    assert summary["examples"] == status["recommendation_readiness"]["kline"]["examples"]
    assert summary["examples"][0]["code"] == "000001"
    assert summary["examples"][0]["reason"] == "本地日K不足30条"


def test_summarize_alerts_groups_severity():
    alerts = [
        {"severity": "high", "message": "报价源全部失败"},
        {"severity": "medium", "message": "日K覆盖不足"},
    ]

    summary = summarize_alerts(alerts)

    assert summary["high"] == 1
    assert summary["medium"] == 1
    assert summary["messages"] == ["报价源全部失败", "日K覆盖不足"]


def test_summarize_tasks_counts_running_and_failures():
    tasks = [
        {"task_id": "a", "enabled": True, "is_running": True, "last_status": "completed"},
        {"task_id": "b", "enabled": True, "is_running": False, "last_status": "failed", "last_error": "boom"},
    ]

    summary = summarize_tasks(tasks)

    assert summary["enabled_count"] == 2
    assert summary["running_count"] == 1
    assert summary["failed_count"] == 1
    assert summary["last_errors"] == ["boom"]


def test_cached_local_pool_status_reuses_heavy_status(monkeypatch):
    calls = []

    def fake_pool_status(db, unified_limit, unified_offset, bars_limit, bars_offset, **kwargs):
        calls.append((unified_limit, unified_offset, bars_limit, bars_offset))
        return {
            "summary": {"unified_quote_count": 10},
            "recommendation_readiness": {"status": "ready", "can_run_local_recommendation": True},
            "unified_quotes": [{"code": "000001"}],
            "daily_bars": [{"code": "000001"}],
        }

    monkeypatch.setattr(data_sources_api, "get_local_pool_status", fake_pool_status)
    with data_sources_api._pool_status_lock:
        data_sources_api._pool_status_cache.clear()

    first = data_sources_api.get_cached_local_pool_status(None, u_limit=20, b_limit=20, include_paginated=False)
    second = data_sources_api.get_cached_local_pool_status(None, u_limit=20, b_limit=20, include_paginated=False)

    assert first["summary"]["unified_quote_count"] == 10
    assert second["recommendation_readiness"]["status"] == "ready"
    assert calls == [(20, 0, 20, 0)]


def test_cached_local_pool_status_cache_only_returns_unready_without_heavy_call(monkeypatch):
    calls = []

    def fake_pool_status(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("heavy status should not be called")

    monkeypatch.setattr(data_sources_api, "get_local_pool_status", fake_pool_status)
    with data_sources_api._pool_status_lock:
        data_sources_api._pool_status_cache.clear()

    status = data_sources_api.get_cached_local_pool_status(None, cache_only=True, include_paginated=False)

    assert status["recommendation_readiness"]["status"] == "blocked"
    assert calls == []


def test_cached_local_pool_status_cache_only_can_use_stale_cache(monkeypatch):
    calls = []

    def fake_pool_status(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("heavy status should not be called")

    monkeypatch.setattr(data_sources_api, "get_local_pool_status", fake_pool_status)
    with data_sources_api._pool_status_lock:
        data_sources_api._pool_status_cache.clear()
        data_sources_api._pool_status_cache[data_sources_api._POOL_CACHE_KEY + ":full"] = {
            "ts": 0.0,
            "data": {
                "summary": {"unified_quote_count": 10},
                "recommendation_readiness": {"status": "ready", "can_run_local_recommendation": True},
                "unified_quotes": [{"code": "000001"}],
                "daily_bars": [{"code": "000001"}],
            },
        }

    status = data_sources_api.get_cached_local_pool_status(None, cache_only=True, include_paginated=False)

    assert status["recommendation_readiness"]["status"] == "ready"
    assert status["cache_stale"] is True
    assert status["unified_quotes"] == []
    assert calls == []
