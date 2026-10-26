"""PR #10 新增告警的自动解除：复核恢复时立即解除；一直没再触发的，最后一次触发 24 小时后自动解除。

告警登记在 app.core.alerts（data/runtime/alerts_active.json）。这里只管下面这些新告警，别的告警不动：
- daily_output_failed（key=交易日）：盘后出票失败
- data_health_snapshot（key=交易日）：健康快照时数据质量巡检失败
- rolling_r12_negative / rolling_r12_incomplete（key=截止日）：近12个月滚动重算发现策略转负 / 取数失败
- trade_calendar 里 key 以 "seal:" 开头的：实盘封签查不到交易日历
"""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Any

logger = logging.getLogger(__name__)

EXPIRE_HOURS = 24
MANAGED_KINDS = ("daily_output_failed", "data_health_snapshot", "rolling_r12_negative", "rolling_r12_incomplete")
SEAL_KIND, SEAL_PREFIX = "trade_calendar", "seal:"
_CST = _dt.timezone(_dt.timedelta(hours=8))


def is_managed(rec: dict[str, Any]) -> bool:
    kind, key = rec.get("kind"), str(rec.get("key") or "")
    return kind in MANAGED_KINDS or (kind == SEAL_KIND and key.startswith(SEAL_PREFIX))


def _parse(ts: Any) -> _dt.datetime | None:
    try:
        return _dt.datetime.strptime(str(ts), "%Y-%m-%d %H:%M:%S").replace(tzinfo=_CST)
    except (TypeError, ValueError):
        return None


def expire_stale(now: _dt.datetime | None = None, hours: float = EXPIRE_HOURS) -> list[str]:
    """最后一次触发（last_seen）超过 hours 小时的受管告警自动解除，返回解除的 kind|key。"""
    from app.core.alerts import active_alert_records, resolve_alert

    now = (now or _dt.datetime.now(_CST)).astimezone(_CST)
    out = []
    for rec in active_alert_records():
        if not is_managed(rec):
            continue
        seen = _parse(rec.get("last_seen")) or _parse(rec.get("first_seen"))
        if seen is not None and now - seen >= _dt.timedelta(hours=hours):
            if resolve_alert(rec["kind"], rec["key"], f"超过 {hours:g} 小时没有再触发，自动解除"):
                out.append(f"{rec['kind']}|{rec['key']}")
    return out


def resolve_recovered(kind: str, key: str, message: str) -> bool:
    """复核恢复：立即解除这一条（本来就不活跃时不刷日志）。"""
    from app.core.alerts import resolve_alert

    try:
        return resolve_alert(kind, key, message)
    except Exception:  # noqa: BLE001 - 解除失败不影响任务
        logger.warning("resolve alert %s|%s failed", kind, key, exc_info=True)
        return False
