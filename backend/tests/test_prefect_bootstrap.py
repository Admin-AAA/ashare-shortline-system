"""Prefect 客户端引导回归测试。

覆盖 2026-11-15 的线上故障：后端进程没设 PREFECT_API_URL → 每次触发起临时
server → 与常驻 server 抢同一个 SQLite（database is locked / 502 / 接口 500）。
"""
from __future__ import annotations

import os
import sys
from types import ModuleType

import pytest

from app.core import prefect_bootstrap as pb
from app.core.prefect_bootstrap import PrefectClientUnavailable


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.delenv("PREFECT_API_URL", raising=False)
    monkeypatch.delenv("ASHARE_UNDER_PYTEST", raising=False)
    monkeypatch.setattr("app.core.alerts.emit_alert", lambda *a, **k: True)
    pb.reset_for_tests()
    yield
    pb.reset_for_tests()


def test_configured_env_is_never_overwritten(monkeypatch):
    url = "http://127.0.0.1:9999/api"
    monkeypatch.setenv("PREFECT_API_URL", url)
    monkeypatch.setattr(pb, "_probe", lambda _u: True)

    status = pb.ensure_resident_prefect_server()

    assert status["mode"] == "configured"
    assert status["api_url"] == url
    assert status["server_healthy"] is True
    assert pb.RESIDENT_API_URL != url
    assert pb.prefect_client_status() == status  # 缓存命中，不再探测


def test_missing_env_and_server_online_is_auto_configured(monkeypatch):
    calls = []
    monkeypatch.setattr(pb, "_probe", lambda u: calls.append(u) or True)

    status = pb.ensure_resident_prefect_server()

    assert status["mode"] == "auto_configured"
    assert status["api_url"] == pb.RESIDENT_API_URL
    assert status["server_healthy"] is True
    assert os.environ["PREFECT_API_URL"] == pb.RESIDENT_API_URL
    assert calls == [pb.RESIDENT_API_URL]


def test_missing_env_and_server_offline_falls_back_without_setting_env(monkeypatch):
    monkeypatch.setattr(pb, "_probe", lambda _u: False)

    status = pb.ensure_resident_prefect_server()

    assert status["mode"] == "ephemeral_fallback"
    assert status["ephemeral_risk"] is True
    assert "PREFECT_API_URL" not in os.environ


def test_require_rejects_when_no_api_url(monkeypatch):
    monkeypatch.setattr(pb, "_probe", lambda _u: False)

    with pytest.raises(PrefectClientUnavailable, match="PREFECT_API_URL"):
        pb.require_resident_prefect_client()


def test_require_rejects_late_configuration_after_prefect_import(monkeypatch):
    """prefect 已导入后补 env 无效（settings 缓存），必须明确拒绝而不是静默退化。"""
    monkeypatch.setitem(sys.modules, "prefect", ModuleType("prefect"))
    monkeypatch.setattr(pb, "_probe", lambda _u: True)

    with pytest.raises(PrefectClientUnavailable, match="settings 缓存无法刷新"):
        pb.require_resident_prefect_client()


def test_require_passes_when_configured_before_prefect_import(monkeypatch):
    monkeypatch.setenv("PREFECT_API_URL", pb.RESIDENT_API_URL)
    monkeypatch.setattr(pb, "_probe", lambda _u: True)

    status = pb.require_resident_prefect_client()

    assert status["mode"] == "configured"
    assert status["api_url"] == pb.RESIDENT_API_URL


def test_pytest_environment_skips_network_probe(monkeypatch):
    monkeypatch.setenv("ASHARE_UNDER_PYTEST", "1")
    monkeypatch.setattr(pb, "_probe", lambda _u: pytest.fail("pytest 下不应探测网络"))

    assert pb.ensure_resident_prefect_server()["mode"] == "test_skipped"
    assert "PREFECT_API_URL" not in os.environ
    assert pb.require_resident_prefect_client()["mode"] == "test_skipped"


def test_admin_orchestration_status_is_read_only():
    from app.api.admin import orchestration_status

    result = orchestration_status()

    assert set(("ok", "api_url", "resident_url", "mode")) <= set(result)
    assert result["resident_url"] == pb.RESIDENT_API_URL


def test_main_invokes_bootstrap_before_any_prefect_or_api_import():
    """启动前设 env 是本修复的前提；后人调整 import 顺序会静默破坏它。"""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(encoding="utf-8").splitlines()
    boot = next(i for i, line in enumerate(src)
               if "ensure_resident_prefect_server()" in line and not line.lstrip().startswith("#"))
    late_imports = [i for i, line in enumerate(src)
                    if line.startswith(("from prefect", "import prefect", "from app.api"))]
    assert late_imports and boot < min(late_imports)


def test_run_task_now_returns_503_on_client_misconfiguration(monkeypatch):
    from fastapi import HTTPException

    from app.api import admin

    monkeypatch.setattr(admin, "list_scheduled_tasks", lambda _db: [{"task_id": "data_health_snapshot_daily"}])
    monkeypatch.setattr(
        "app.orchestration.trigger_scheduled_task",
        lambda *_a, **_k: (_ for _ in ()).throw(PrefectClientUnavailable("临时 server 风险")),
    )

    with pytest.raises(HTTPException) as exc:
        admin.run_task_now("data_health_snapshot_daily", db=object())

    assert exc.value.status_code == 503
    assert "常驻 server" in str(exc.value.detail)
