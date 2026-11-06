"""运维告警：LLM / 数据源等外部依赖失败时写一条可 grep 的 ERROR 日志，并维护「当前活跃告警」登记。

所有告警走 logger ``ashare.alerts``，消息以 ``[ALERT:<kind>]`` 开头，写入 backend/logs/app.log
（文件 handler 记录 WARNING 及以上）。同一 (kind, key) 在冷却期内只记一次，避免刷屏。
排查：``grep '\\[ALERT:' backend/logs/app.log``。

2026-10-25：告警此前只有「发出」没有「解除」——日志里 data_freshness 告警 47 条永远不消，看不出哪些已修好。
现在 emit_alert 同时把 (kind, key) 写入活跃登记（data/runtime/alerts_active.json），
问题恢复后调用 ``resolve_alert`` 记一条 ``[ALERT_RESOLVED:<kind>]`` 并移出登记；
``active_alert_records()`` 给 admin/告警面板读取。登记读写失败只记 debug，不影响业务。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("ashare.alerts")

ALERT_COOLDOWN_SECONDS = 600.0
REGISTRY_PATH = Path(__file__).resolve().parents[2] / "data" / "runtime" / "alerts_active.json"
_last_emitted: dict[tuple[str, str], float] = {}
_lock = threading.Lock()
_CST = timezone(timedelta(hours=8))


def _now_iso() -> str:
    return datetime.now(_CST).strftime("%Y-%m-%d %H:%M:%S")


# pytest 下登记只放内存，避免测试里的 emit_alert 写进生产登记文件
_memory_registry: dict[str, dict[str, Any]] = {}


def _memory_only() -> bool:
    return bool(os.environ.get("ASHARE_UNDER_PYTEST")) and not os.environ.get("ASHARE_ALERT_REGISTRY_FILE_TEST")


def _load_registry() -> dict[str, dict[str, Any]]:
    if _memory_only():
        return dict(_memory_registry)
    try:
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001 - 登记损坏时当作空，下一次写入会重建
        logger.debug("alerts registry unreadable: %r", exc)
        return {}


def _save_registry(reg: dict[str, dict[str, Any]]) -> None:
    if _memory_only():
        _memory_registry.clear()
        _memory_registry.update(reg)
        return
    try:
        REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = REGISTRY_PATH.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, REGISTRY_PATH)
    except Exception as exc:  # noqa: BLE001
        logger.debug("alerts registry write failed: %r", exc)


def _reg_key(kind: str, key: str) -> str:
    return f"{kind}|{key}"


def emit_alert(kind: str, key: str, message: str, *, cooldown: float = ALERT_COOLDOWN_SECONDS) -> bool:
    """记录一条告警；冷却期内同一 (kind, key) 被抑制时返回 False。无论是否被抑制都刷新活跃登记。

    冷却同时看进程内记录和登记里的 last_emitted_epoch（Prefect 每个任务是新子进程，只看进程内
    会让「每天重发一次」变成每轮都发）。
    """
    now = time.monotonic()
    wall = time.time()
    with _lock:
        reg = _load_registry()
        entry = reg.get(_reg_key(kind, key)) or {}
        last = _last_emitted.get((kind, key))
        reg_last = entry.get("last_emitted_epoch")
        suppressed = (last is not None and now - last < cooldown) or (
            isinstance(reg_last, (int, float)) and 0 <= wall - reg_last < cooldown
        )
        reg[_reg_key(kind, key)] = {
            "kind": kind, "key": key, "message": message[:500],
            "first_seen": entry.get("first_seen") or _now_iso(), "last_seen": _now_iso(),
            "last_emitted_epoch": reg_last if suppressed else wall,
        }
        _save_registry(reg)
        if suppressed:
            return False
        _last_emitted[(kind, key)] = now
    logger.error("[ALERT:%s] %s | key=%s", kind, message, key)
    return True


def resolve_alert(kind: str, key: str, message: str = "") -> bool:
    """问题已恢复：移出活跃登记并记一条 [ALERT_RESOLVED]。本来就不活跃时返回 False（不刷日志）。"""
    with _lock:
        reg = _load_registry()
        entry = reg.pop(_reg_key(kind, key), None)
        _last_emitted.pop((kind, key), None)
        if entry is None:
            return False
        _save_registry(reg)
    logger.warning("[ALERT_RESOLVED:%s] %s | key=%s first_seen=%s", kind, message or "已恢复", key, entry.get("first_seen"))
    return True


def active_alert_records(kind: str | None = None) -> list[dict[str, Any]]:
    expire_stale_alerts()
    reg = _load_registry()
    rows = [v for v in reg.values() if kind is None or v.get("kind") == kind]
    return sorted(rows, key=lambda r: (r.get("kind", ""), r.get("key", "")))


# ---------------------------------------------------------------- TTL 自动过期
# 2026-11-06：登记里积了 87 条超过 24 小时仍活跃的告警，绝大多数是「key 不带日期、发出后没有
# 任何解除方」的孤儿——datasource_all_failed|<api>（某接口当时全部上游失败，之后成功了也不会
# 主动解除）、ivol_coverage|<date>（按日一次性）、DAILY_RUN_FAILED|<date>（当日失败，隔天再也
# 不会重发也不再结算）。于是「超过 24 小时仍未解除的告警」这一项永远是黄：检查项的本意是
# 「放着不管的告警会变成噪音」，而它自己被这种永不结算的告警永久污染。
#
# 告警语义是「当前是否仍然成立」。同一 (kind, key) 超过 TTL 没有再出现过一次，就说明条件已经
# 不成立（或者没有检查方再关心它），自动解除并记一条 [ALERT_RESOLVED] 留审计痕迹；条件若真的
# 还在，下一次 emit_alert 会重新登记，不会丢。
ALERT_TTL_HOURS = float(os.environ.get("ASHARE_ALERT_TTL_HOURS", "24") or 24)
_expire_lock = threading.Lock()
_last_expire_at: float = 0.0
EXPIRE_INTERVAL_SECONDS = 300.0


def expire_stale_alerts(now: datetime | None = None) -> list[str]:
    """把「超过 TTL 没有再出现」的活跃告警自动解除，返回被解除的 key 列表（kind|key）。

    只看 last_seen：emit_alert 每次都会刷新它，所以还在发生的告警不会被清掉。
    注册表读不出来 / pytest 内存登记时直接跳过。节流到每 EXPIRY 一次，读路径频繁调用也不痛。
    """
    global _last_expire_at, _memory_registry
    monotonic_now = time.monotonic()
    if monotonic_now - _last_expire_at < EXPIRE_INTERVAL_SECONDS:
        return []
    with _expire_lock:
        if time.monotonic() - _last_expire_at < EXPIRE_INTERVAL_SECONDS:
            return []
        _last_expire_at = time.monotonic()
        now = now or datetime.now(_CST)
        reg = _load_registry()
        if not reg:
            return []
        expired: list[str] = []
        for key, entry in list(reg.items()):
            try:
                seen = datetime.strptime(str(entry.get("last_seen")), "%Y-%m-%d %H:%M:%S").replace(tzinfo=_CST)
            except Exception:  # noqa: BLE001 - 时间坏掉不清理，留给人工
                continue
            if (now - seen).total_seconds() <= ALERT_TTL_HOURS * 3600:
                continue
            reg.pop(key, None)
            expired.append(key)
        if not expired:
            return []
        _save_registry(reg)
    for key in expired:
        kind, _, name = key.partition("|")
        _last_emitted.pop((kind, name), None)
        logger.warning("[ALERT_RESOLVED:%s] %s 已超过 %g 小时未再出现，自动解除（TTL 归档） | key=%s",
                       kind, name, ALERT_TTL_HOURS, name)
    return expired



def reset_alerts() -> None:
    with _lock:
        _last_emitted.clear()
        _memory_registry.clear()
