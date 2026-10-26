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


def emit_daily(kind: str, key: str, message: str, now: _dt.datetime | None = None) -> bool:
    """每天最多发一次：今天已经发过（登记里 last_seen 是今天）就不再发；否则发并刷新 last_seen。"""
    from app.core.alerts import active_alert_records, emit_alert

    today = (now or _dt.datetime.now(_CST)).astimezone(_CST).strftime("%Y-%m-%d")
    for rec in active_alert_records(kind):
        if rec.get("key") == key and str(rec.get("last_seen") or "")[:10] == today:
            return False
    return emit_alert(kind, key, message, cooldown=0)


def reemit_persistent(db=None, now: _dt.datetime | None = None) -> list[str]:
    """条件还在就每天重发，不让问题在 24 小时自动解除后悄悄消失（只读结果文件和本地日历，不重算）。

    - 滚动重算结果（rolling_r12_latest.json）里仍是 negative、且仍在现役表里的策略 -> rolling_r12_negative；
      已经下线的不再提醒，并解除对应告警。
    - 下一个交易日在本地交易日历里查不到 -> trade_calendar seal:<今天>（每天一次）。"""
    from app.services import midterm_service as ms
    from app.services.rolling_r12 import load_latest

    now = (now or _dt.datetime.now(_CST)).astimezone(_CST)
    out: list[str] = []
    doc = load_latest()
    if doc and doc.get("cutoff"):
        cutoff = str(doc["cutoff"])
        active = set(ms.STRATEGIES)
        if db is not None:
            try:
                from app.services.daily_output_check import expected_short_strategies

                active |= set(expected_short_strategies(db))
            except Exception:  # noqa: BLE001 - 读不到短线现役表就只看中长线
                logger.debug("expected short strategies unavailable", exc_info=True)
        else:
            active |= set((doc.get("short") or {}).get("strategies") or {})
        neg = sorted(sid for sid, v in {**(doc.get("midlong") or {}), **((doc.get("short") or {}).get("strategies") or {})}.items()
                     if v.get("verdict") == "negative" and sid in active)
        if neg:
            if emit_daily("rolling_r12_negative", cutoff,
                          f"近12个月（截止 {cutoff}）每笔仍为负的现役策略：{','.join(neg)}", now=now):
                out.append(f"rolling_r12_negative|{cutoff}")
        else:
            resolve_recovered("rolling_r12_negative", cutoff, f"截止 {cutoff} 转负的策略已不在现役表里")
    try:
        from app.services import midterm_live_seal as seal

        today = now.date().isoformat()
        if seal._local_next_open(today) is None:  # 查不到时 _alert_missing 已按天发过告警
            out.append(f"trade_calendar|seal:{today}")
    except Exception:  # noqa: BLE001
        logger.debug("calendar coverage check failed", exc_info=True)
    return out


def resolve_recovered(kind: str, key: str, message: str) -> bool:
    """复核恢复：立即解除这一条（本来就不活跃时不刷日志）。"""
    from app.core.alerts import resolve_alert

    try:
        return resolve_alert(kind, key, message)
    except Exception:  # noqa: BLE001 - 解除失败不影响任务
        logger.warning("resolve alert %s|%s failed", kind, key, exc_info=True)
        return False
