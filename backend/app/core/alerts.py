"""运维告警：LLM / 数据源等外部依赖失败时写一条可 grep 的 ERROR 日志。

所有告警走 logger ``ashare.alerts``，消息以 ``[ALERT:<kind>]`` 开头，写入 backend/logs/app.log
（文件 handler 记录 WARNING 及以上）。同一 (kind, key) 在冷却期内只记一次，避免刷屏。
排查：``grep '\\[ALERT:' backend/logs/app.log``。
"""
from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger("ashare.alerts")

ALERT_COOLDOWN_SECONDS = 600.0
_last_emitted: dict[tuple[str, str], float] = {}
_lock = threading.Lock()


def emit_alert(kind: str, key: str, message: str, *, cooldown: float = ALERT_COOLDOWN_SECONDS) -> bool:
    """记录一条告警；冷却期内同一 (kind, key) 被抑制时返回 False。"""
    now = time.monotonic()
    with _lock:
        last = _last_emitted.get((kind, key))
        if last is not None and now - last < cooldown:
            return False
        _last_emitted[(kind, key)] = now
    logger.error("[ALERT:%s] %s | key=%s", kind, message, key)
    return True


def reset_alerts() -> None:
    with _lock:
        _last_emitted.clear()
