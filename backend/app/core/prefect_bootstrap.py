"""Prefect 客户端引导：必须在任何 ``prefect`` 模块被 import 之前调用。

背景（2026-11-15）：本机有常驻 Prefect server（127.0.0.1:4200）与 ProcessWorker，
worker 的 launchd plist 明确设了 ``PREFECT_API_URL``，但后端 plist 漏了。Prefect 3
客户端在没设 API URL 时会为每个 ``get_client()`` **临时起一个 server**，它会：

* 在随机端口监听（线上日志里出现过 8245 / 8861）；
* 与常驻 server 共用同一个 ``~/.prefect/prefect.db``，并发写触发
  ``sqlite3.OperationalError: database is locked``（实测累计 16834 次、82MB 错误日志）；
* 两个临时 server 同时启动会端口冲突，``run_deployment`` 拿到 502，
  ``POST /api/admin/tasks/{id}/run`` 直接 500。

Prefect settings 在首次 import 时缓存，**进程运行后再改 ``os.environ`` 无效**
（实测客户端仍起随机端口的临时 server）。所以本模块只负责「启动前探测 + 设环境变量」，
运行期的配置漂移由 :func:`require_resident_prefect_client` 明确拒绝并告警，
绝不静默退化成临时 server。
"""
from __future__ import annotations

import logging
import os
import sys
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

RESIDENT_API_URL = "http://127.0.0.1:4200/api"
_PROBE_TIMEOUT_SECONDS = 1.5
_ENV_KEY = "PREFECT_API_URL"

_status: dict[str, Any] | None = None


class PrefectClientUnavailable(RuntimeError):
    """客户端没有可靠地连到常驻 server；继续调用会静默起临时 server 并污染数据库。"""


def _probe(api_url: str) -> bool:
    try:
        with urllib.request.urlopen(api_url.rstrip("/") + "/health", timeout=_PROBE_TIMEOUT_SECONDS) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001 - 探测失败一律按「不在线」处理
        return False


def reset_for_tests() -> None:
    """仅测试用：清掉进程级状态缓存。"""
    global _status
    _status = None


def ensure_resident_prefect_server(*, force: bool = False) -> dict[str, Any]:
    """探测常驻 server，必要时在 Prefect 被导入前补上 ``PREFECT_API_URL``。

    幂等且带进程级缓存；pytest 下不做真实网络探测、不改全局环境。
    返回值可直接暴露给诊断端点，字段稳定。
    """
    global _status
    if _status is not None and not force:
        return dict(_status)

    configured = (os.environ.get(_ENV_KEY) or "").strip()
    under_test = bool(os.environ.get("ASHARE_UNDER_PYTEST"))
    prefect_already_imported = "prefect" in sys.modules
    status: dict[str, Any] = {
        "api_url": configured or None,
        "resident_url": RESIDENT_API_URL,
        "mode": "configured" if configured else "unknown",
        "server_healthy": None,
        "prefect_already_imported": prefect_already_imported,
        "ephemeral_risk": False,
    }

    if configured:
        # 已显式配置：绝不覆盖用户的设置；只做一次健康探测用于诊断。
        if not under_test:
            status["server_healthy"] = _probe(configured)
    elif under_test:
        status["mode"] = "test_skipped"
    elif _probe(RESIDENT_API_URL):
        os.environ[_ENV_KEY] = RESIDENT_API_URL
        status.update(api_url=RESIDENT_API_URL, mode="auto_configured", server_healthy=True)
        logger.info("prefect client -> resident server %s (auto-configured)", RESIDENT_API_URL)
    else:
        status.update(mode="ephemeral_fallback", server_healthy=False, ephemeral_risk=True)
        logger.warning(
            "常驻 Prefect server %s 不可达，客户端将退化为临时 server；"
            "手动触发任务可能 502，且会与常驻 server 抢同一个 SQLite。请检查 "
            "com.wangao.ashare-prefect-server 服务", RESIDENT_API_URL,
        )

    _status = status
    return dict(status)


def prefect_client_status() -> dict[str, Any]:
    """给只读诊断端点用：不触发副作用之外的配置变更（最多一次缓存探测）。"""
    return ensure_resident_prefect_server()


def require_resident_prefect_client() -> dict[str, Any]:
    """任何 ``run_deployment`` / ``get_client`` 调用前的安全闸。

    Prefect settings 在 import 时缓存，运行期补 env 无法让已导入的客户端生效；
    这种情况下宁可明确失败 + 告警，也不要静默起临时 server 把 flow run 提交到
    一个随后就关闭的 server 上。
    """
    status = ensure_resident_prefect_server()
    # pytest 不拦截：单测通过 monkeypatch 显式构造异常场景来验证拒绝逻辑。
    if os.environ.get("ASHARE_UNDER_PYTEST"):
        return status

    reason = ""
    if not (os.environ.get(_ENV_KEY) or "").strip():
        reason = "PREFECT_API_URL 未设置，Prefect 客户端会起临时 server"
    elif status.get("mode") == "auto_configured" and status.get("prefect_already_imported"):
        reason = "PREFECT_API_URL 是在 prefect 已导入后才补齐，settings 缓存无法刷新"
    elif status.get("mode") == "ephemeral_fallback":
        reason = f"常驻 Prefect server {RESIDENT_API_URL} 不可达"

    if reason:
        from app.core.alerts import emit_alert

        emit_alert("prefect_client_misconfigured", _ENV_KEY,
                   f"{reason}；请给后端进程配置 PREFECT_API_URL={RESIDENT_API_URL} 并重启后端")
        raise PrefectClientUnavailable(reason)
    return status
