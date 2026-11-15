"""数据源实时配额 / 限频视图（``GET /api/data-sources/limits``）。

**为什么需要**：改造前"上游还能打多少次"只存在于三处互不相通的地方——限流器藏在 provider 实例里、
日额度只在 ``provider_quota`` 的 SQLite 里、熔断状态只有 ``shared_breaker_states()`` 但没有任何
HTTP 接口暴露。于是判断"现在能不能加一个补数任务"只能靠翻代码和日志。

本模块只读拼装四类事实，**绝不 acquire 限流器、绝不触发请求、任何异常降级为 None**：

1. ``limiters``   —— 每个限流 key 的窗口用量/上限/剩余。数据源优先取**共享 SQLite 的 hits 表**
   （跨进程真实用量，backend + Prefect worker 合起来算）；本进程已登记但未落共享库的补注册表值。
2. ``daily``      —— ``provider_quota.usage() / daily_quota()``：当日已用 / 日额度 / 是否已耗尽。
3. ``promax``     —— 双口径并列：**声明** ``tushare_promax_rate_limit_per_minute=180``（每分钟口径，
   上游健康信息）与**实际生效**的 5min/1h 滚动窗口（盘中 100/5min、900/1h，见 ``promax_window``）。
   两者差约 9 倍，必须同时展示，否则读配置的人会高估额度。
4. ``breakers``   —— 当前处于 open 的熔断 key、解除时间、原因。

外加 ``rates``（metrics 滑动窗口速率，进程重启不清零口径）与 ``quality``（近 N 小时 provider 级
成功率 / 空返回率，来自 data_source_attempts），后者是本视图真正的决策依据：eastmoney 79.9% 空返回
这种源，配额再宽松也没用。
"""
from __future__ import annotations

import logging
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# provider_quota 里有日额度概念的 provider（transport 名，与 traces 里的 provider 一致）
QUOTA_PROVIDERS = ("teajoin", "promax", "quantdata")

# 直连源（不走网关限流器，额度写死在模块常量里）
DIRECT_SOURCE_SPECS = (
    ("sina_realtime", "app.services.datasource.sina_realtime", "SINA_RPM", "新浪实时行情"),
    ("tdx_quote", "app.services.datasource.tdx_quote", "TDX_RPM", "通达信 HQ"),
    ("akshare", "app.services.datasource.akshare_backup", "AKSHARE_RPM", "AKShare 备份"),
    ("baostock", "app.services.datasource.bar_minute", "BAOSTOCK_RPM", "BaoStock 分钟线"),
)

# 空返回率告警阈值（近 N 小时，样本量足够才判定）
EMPTY_RATE_YELLOW = 0.30
EMPTY_RATE_RED = 0.60
MIN_SAMPLE_FOR_VERDICT = 50

_PROMax_KEY_RE = re.compile(r"promax:research:(intraday|offhours):(\d+)")


def _num(v: Any, digits: int = 3) -> float | None:
    try:
        return round(float(v), digits)
    except (TypeError, ValueError):
        return None


def _provider_of_key(key: str) -> str:
    """限流 / 熔断 key → provider 名。"""
    key = str(key or "")
    if key.startswith("gateway:"):
        return key.split(":")[1] if len(key.split(":")) > 1 else key
    m = _PROMax_KEY_RE.search(key)
    if m:
        return "promax"
    parts = [p for p in key.split(":") if p]
    # {namespace}:{provider}:{dataset} / live:tushare:promax:rt_k
    for part in parts:
        if part in QUOTA_PROVIDERS:
            return part
    return parts[0] if parts else key


def _window_of_key(key: str, default: float = 60.0) -> float:
    m = _PROMax_KEY_RE.search(str(key))
    if m:
        try:
            return float(m.group(2))
        except ValueError:
            return default
    return default


def _shared_hits(db_path: str | None) -> dict[str, int]:
    """共享限频库里**当前窗口内**的计数：``key -> count``（各 key 窗口不同，取最大窗口保守统计）。"""
    out: dict[str, int] = {}
    if not db_path:
        return out
    try:
        conn = sqlite3.connect(db_path, timeout=1.0)
    except Exception:  # noqa: BLE001
        return out
    try:
        now = time.time()
        horizon = now - 3600.0  # 最大窗口 1h
        rows = conn.execute("SELECT k, COUNT(*) FROM hits WHERE ts>? GROUP BY k", (horizon,)).fetchall()
        for k, n in rows:
            window = _window_of_key(k, 60.0)
            if window >= 3600.0:
                out[str(k)] = int(n)
                continue
            # 窗口比 1h 小：只数窗口内的
            cnt = conn.execute("SELECT COUNT(*) FROM hits WHERE k=? AND ts>?", (k, now - window)).fetchone()
            out[str(k)] = int(cnt[0]) if cnt else int(n)
    except Exception:  # noqa: BLE001 - 观测降级，绝不影响主流程
        logger.debug("shared hits read failed", exc_info=True)
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    return out


def _limiters_section(db_path: str | None) -> list[dict[str, Any]]:
    from app.core import limiter_registry

    shared = _shared_hits(db_path)
    rows: dict[str, dict[str, Any]] = {}
    for item in limiter_registry.usage():
        key = item["key"]
        used = item["used"]
        alive = item.get("alive", True)
        if key in shared:  # 共享库是跨进程真值，优先
            used = shared[key]
            alive = True
        limit = item.get("limit") or item.get("requests_per_minute")
        rows[key] = {
            "key": key,
            "provider": _provider_of_key(key),
            "kind": item.get("kind"),
            "window_seconds": _num(item.get("window_seconds"), 1),
            # alive=False：provider 实例已回收（该源当前没在用），used 无意义，标 null 并给 note，
            # 避免把「没在跑」看成「用量为 0」或「用量读不到」。
            "used": used if alive else None,
            "limit": limit,
            "headroom_pct": _num((1 - (used or 0) / limit) * 100, 1) if (limit and alive) else None,
            "scope": "shared" if key in shared else "in_process",
            "alive": alive,
            "note": None if alive else "provider 实例已回收（当前未启用该源），用量不可读",
        }
    # 共享库里出现但本进程没登记过的 key（Prefect worker 建的限流器）
    for key, used in shared.items():
        if key in rows:
            continue
        window = _window_of_key(key, 60.0)
        limit = _declared_rpm(_provider_of_key(key))
        rows[key] = {
            "key": key,
            "provider": _provider_of_key(key),
            "kind": "shared_only",
            "window_seconds": window,
            "used": used,
            "limit": limit,
            "headroom_pct": _num((1 - used / limit) * 100, 1) if limit else None,
            "scope": "shared",
        }
    return sorted(rows.values(), key=lambda i: (i["provider"] or "", i["key"]))


def _declared_rpm(provider: str) -> int | None:
    """配置里该 provider 声明的每分钟上限（拿不到返回 None）。"""
    try:
        from app.core.config import get_settings

        s = get_settings()
        mapping = {
            "teajoin": "tushare_teajoin_rate_limit_per_minute",
            "promax": "tushare_promax_rate_limit_per_minute",
            "quantdata": "tushare_quantdata_rate_limit_per_minute",
            "ths": "ths_rate_limit_per_minute",
        }
        field = mapping.get(provider)
        if not field:
            return None
        value = int(getattr(s, field, 0) or 0)
        return value or None
    except Exception:  # noqa: BLE001
        return None


def _daily_section() -> list[dict[str, Any]]:
    from app.core import provider_quota

    out = []
    for provider in QUOTA_PROVIDERS:
        try:
            used, exhausted = provider_quota.usage(provider)
        except Exception:  # noqa: BLE001
            used, exhausted = 0, False
        try:
            quota = provider_quota.daily_quota(provider)
        except Exception:  # noqa: BLE001
            quota = None
        out.append({
            "provider": provider,
            "used_today": int(used or 0),
            "daily_quota": quota,
            "used_pct": _num((used / quota) * 100, 1) if quota else None,
            "remaining": (quota - int(used or 0)) if quota else None,
            "exhausted_today": bool(exhausted),
            # teajoin 卖家口径 500/min，但我们没有日额度上限：显式说明，避免被误读成"无限"
            "note": None if quota else "未配置日额度上限（无 ASHARE_TUSHARE_%s_DAILY_QUOTA）" % provider.upper(),
        })
    return out


def _promax_section(cfg) -> dict[str, Any]:
    """promax 双口径：声明 RPM 与实际滚动窗口并列。"""
    from app.core import promax_window

    intraday = False
    insights: dict[str, Any] = {}
    try:
        intraday = bool(promax_window.in_trading_session())
    except Exception:  # noqa: BLE001
        intraday = False
    try:
        from app.core.runtime_env import env_flag, env_value

        insights["rt_k_live_open_until"] = _num(promax_window._rt_k_live_open_until(), 0)
        # 用布尔解析而不是原样回显字符串：否则 .env 里写 0/false 会被当成"已启用"，
        # 空值时也应是明确的 False 而不是 null（前端不该自己猜）。
        insights["research_enabled"] = env_flag("ASHARE_TUSHARE_PROMAX_RESEARCH_ENABLED", False)
    except Exception:  # noqa: BLE001
        pass
    windows = []
    for window, off_limit, in_limit in promax_window.window_table():
        windows.append({
            "window_seconds": _num(window, 0),
            "limit_offhours": off_limit,
            "limit_intraday": in_limit,
            "effective_limit": in_limit if intraday else off_limit,
            "effective_per_minute": _num((in_limit if intraday else off_limit) * 60.0 / window, 2) if window else None,
        })
    declared = None
    try:
        declared = int(getattr(cfg, "tushare_promax_rate_limit_per_minute", 0) or 0) or None
    except Exception:  # noqa: BLE001
        declared = None
    concurrency = None
    try:
        concurrency = int(getattr(cfg, "tushare_promax_max_concurrency", 0) or 0) or None
    except Exception:  # noqa: BLE001
        concurrency = None
    tightest = min((w["effective_per_minute"] for w in windows if w["effective_per_minute"]), default=None)
    return {
        "in_trading_session": intraday,
        "declared_per_minute": declared,
        "max_concurrency": concurrency,
        "windows": windows,
        "tightest_effective_per_minute": tightest,
        "declared_vs_effective_ratio": _num(declared / tightest, 2) if declared and tightest else None,
        "warning": (
            None if not (declared and tightest and declared > tightest * 1.5) else
            f"配置口径 {declared}/min 高于实际生效窗口 {tightest}/min（差 {declared / tightest:.1f} 倍）："
            "按 config 估算配额会高估可用量，决策请以 windows 为准"
        ),
        **insights,
    }


def _breakers_section() -> list[dict[str, Any]]:
    try:
        from app.core.shared_breaker import shared_breaker_states

        states = shared_breaker_states() or []
    except Exception:  # noqa: BLE001
        return []
    now = time.time()
    out = []
    for s in states:
        key = str(s.get("key") or "")
        # 网关熔断 key 形如 gw:{provider}:{dataset}；promax 的 rt_k 是 promax:{api}
        provider = _provider_of_key(key[3:] if key.startswith("gw:") else key)
        out.append({
            "key": key,
            "provider": provider,
            "open_until": _num(s.get("open_until"), 0),
            "seconds_remaining": _num(max(0.0, float(s.get("open_until") or 0) - now), 0),
            "reason": s.get("reason"),
            "role": s.get("role"),
        })
    return out


def _direct_section() -> list[dict[str, Any]]:
    import importlib

    out = []
    for provider, module, attr, label in DIRECT_SOURCE_SPECS:
        rpm = None
        try:
            rpm = int(getattr(importlib.import_module(module), attr))
        except Exception:  # noqa: BLE001
            rpm = None
        out.append({"provider": provider, "label": label, "declared_per_minute": rpm, "enforced_by": "in-process constant"})
    return out


def _rates_section() -> list[dict[str, Any]]:
    """进程内滑动窗口速率：provider 维度的"现在每分钟打多少次"。"""
    from app.core import metrics

    try:
        samples = metrics.rates()
    except Exception:  # noqa: BLE001
        return []
    agg: dict[str, dict[str, Any]] = {}
    for item in samples:
        name = item.get("name")
        labels = item.get("labels") or {}
        provider = labels.get("provider") or labels.get("dataset") or "-"
        if name not in ("dataset_requests", "provider_requests", "provider_ratelimit_wait", "provider_fallback"):
            continue
        if name != "dataset_requests" and labels.get("provider") is None:
            continue
        bucket = agg.setdefault(str(provider), {
            "provider": str(provider), "requests_per_minute": 0.0, "ratelimit_waits_per_minute": 0.0,
            "fallbacks_per_minute": 0.0,
        })
        per_minute = float(item.get("per_minute") or 0.0)
        if name == "dataset_requests":
            bucket["requests_per_minute"] = round(bucket["requests_per_minute"] + per_minute, 2)
        elif name == "provider_ratelimit_wait":
            bucket["ratelimit_waits_per_minute"] = round(bucket["ratelimit_waits_per_minute"] + per_minute, 2)
        elif name == "provider_fallback":
            bucket["fallbacks_per_minute"] = round(bucket["fallbacks_per_minute"] + per_minute, 2)
    return sorted(agg.values(), key=lambda i: -i["requests_per_minute"])


def quality_by_provider(db, *, hours: int = 24) -> list[dict[str, Any]]:
    """近 N 小时 provider 级成功率 / 空返回率（来自 data_source_attempts）。

    这是配额视图的"值不值得给它配额"依据：eastmoney 606 次里 484 次空返回（79.9%），
    限频再宽松也是废源。任何异常返回空列表。
    """
    if db is None:
        return []
    try:
        from sqlalchemy import func, select

        # 注意：模型名是 DataSourceRequestTrace（表 data_source_request_traces）。
        # 早前误写成不存在的 TraceRow，ImportError 被本函数的 except 吞掉，导致
        # quality 段长期恒为空——这类"静默降级"必须靠测试兜住（见 test_limits_view_*）。
        from app.models.datasource import DataSourceAttempt, DataSourceRequestTrace

        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=int(hours))
        rows = db.execute(
            select(
                DataSourceAttempt.provider,
                DataSourceAttempt.outcome,
                func.count().label("n"),
                func.avg(DataSourceAttempt.duration_ms).label("avg_ms"),
                func.max(DataSourceAttempt.duration_ms).label("max_ms"),
            )
            .join(DataSourceRequestTrace,
                  DataSourceRequestTrace.trace_id == DataSourceAttempt.trace_id)
            .where(DataSourceRequestTrace.started_at >= cutoff)
            .group_by(DataSourceAttempt.provider, DataSourceAttempt.outcome)
        ).all()
    except Exception:  # noqa: BLE001
        logger.debug("provider quality aggregation failed", exc_info=True)
        return []
    agg: dict[str, dict[str, Any]] = {}
    for provider, outcome, n, avg_ms, max_ms in rows:
        bucket = agg.setdefault(str(provider), {
            "provider": str(provider), "attempts": 0, "success": 0, "empty": 0, "errors": 0,
            "avg_latency_seconds": None, "max_latency_seconds": None, "_lat_weight": 0.0,
        })
        bucket["attempts"] += int(n or 0)
        outcome = str(outcome or "")
        if outcome in ("success", "stale"):
            bucket["success"] += int(n or 0)
        elif outcome == "empty":
            bucket["empty"] += int(n or 0)
        else:
            bucket["errors"] += int(n or 0)
        if avg_ms is not None and n:
            bucket["_lat_weight"] += float(avg_ms) * int(n)
        if max_ms:
            bucket["max_latency_seconds"] = _num(max(float(bucket["max_latency_seconds"] or 0), float(max_ms)) / 1000.0, 2)
    out = []
    for bucket in agg.values():
        attempts = bucket["attempts"] or 1
        bucket["success_rate"] = _num(bucket["success"] / attempts, 3)
        bucket["empty_rate"] = _num(bucket["empty"] / attempts, 3)
        bucket["error_rate"] = _num(bucket["errors"] / attempts, 3)
        bucket["avg_latency_seconds"] = _num(bucket["_lat_weight"] / attempts / 1000.0, 3) if bucket["_lat_weight"] else None
        bucket.pop("_lat_weight", None)
        if bucket["attempts"] < MIN_SAMPLE_FOR_VERDICT:
            bucket["verdict"] = "insufficient_sample"
        elif (bucket["empty_rate"] or 0) >= EMPTY_RATE_RED:
            bucket["verdict"] = "red_empty_rate"
        elif (bucket["empty_rate"] or 0) >= EMPTY_RATE_YELLOW:
            bucket["verdict"] = "yellow_empty_rate"
        elif (bucket["success_rate"] or 0) < 0.5:
            bucket["verdict"] = "red_low_success"
        else:
            bucket["verdict"] = "ok"
        out.append(bucket)
    out.sort(key=lambda i: -i["attempts"])
    return out


def collect(db=None, *, hours: int = 24) -> dict[str, Any]:
    """拼装完整视图。所有子项独立 try，任一项失败不影响其余。"""
    from app.core.config import get_settings
    from app.services.tushare_transport import shared_limiter_db_path

    try:
        cfg = get_settings()
    except Exception:  # noqa: BLE001
        cfg = None

    db_path = None
    try:
        db_path = shared_limiter_db_path() or None
    except Exception:  # noqa: BLE001
        db_path = None

    warnings: list[str] = []
    if not db_path:
        warnings.append(
            "跨进程限频未启用（TUSHARE_SHARED_LIMITER_DB 为空）：backend 与 Prefect worker 各持一份配额，"
            "两进程合计可打满双倍上限"
        )

    limiters = _limiters_section(db_path)
    daily = _daily_section()
    promax = _promax_section(cfg)
    breakers = _breakers_section()
    quality = quality_by_provider(db, hours=hours)

    for item in daily:
        if item["used_pct"] is not None and item["used_pct"] >= 80:
            warnings.append(f"{item['provider']} 日额度已用 {item['used_pct']}%（{item['used_today']}/{item['daily_quota']}）")
        if item["exhausted_today"]:
            warnings.append(f"{item['provider']} 今日额度已耗尽，补数会被 quota_skip 拦下")
    for item in quality:
        if item["verdict"] == "red_empty_rate":
            warnings.append(
                f"{item['provider']} 近 {hours}h 空返回率 {item['empty_rate']}: {item['empty']}/{item['attempts']} 次"
                " —— 该源近乎废源，建议降级或摘除"
            )
    for item in breakers:
        warnings.append(f"熔断开启：{item['key']}（还剩 {int(item['seconds_remaining'] or 0)}s，原因 {item['reason']}）")
    if promax.get("warning"):
        warnings.append(promax["warning"])

    return {
        "generated_at": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        "shared_limiter": {
            "enabled": bool(db_path),
            "db": db_path,
            "env_keys": ["TUSHARE_SHARED_LIMITER_DB", "ASHARE_TUSHARE_SHARED_LIMITER_DB"],
        },
        "limiters": limiters,
        "daily": daily,
        "promax": promax,
        "breakers": breakers,
        "direct_sources": _direct_section(),
        "rates": _rates_section(),
        "quality": quality,
        "warnings": warnings,
    }


__all__ = ["collect", "quality_by_provider", "EMPTY_RATE_YELLOW", "EMPTY_RATE_RED", "MIN_SAMPLE_FOR_VERDICT"]
