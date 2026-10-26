"""测量脚本请求（X-Ashare-Perf-Probe: 1，且来自本机回环）不写 api_request_metrics；其余照常记录。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main


@pytest.fixture
def recorded(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(main, "_record_api_metric", lambda *a: calls.append(a))
    return calls


def _client(host: str) -> TestClient:
    # 不用 with：不触发 lifespan（不迁移、不起定时任务）
    return TestClient(main.app, client=(host, 50000))


def test_probe_from_loopback_is_not_recorded(recorded):
    r = _client("127.0.0.1").get("/api/auth/status", headers={"X-Ashare-Perf-Probe": "1"})
    assert r.status_code == 200
    assert recorded == []


def test_normal_request_is_recorded(recorded):
    _client("127.0.0.1").get("/api/auth/status")
    assert len(recorded) == 1


def test_probe_header_from_other_host_is_still_recorded(recorded):
    _client("192.168.1.20").get("/api/auth/status", headers={"X-Ashare-Perf-Probe": "1"})
    assert len(recorded) == 1
