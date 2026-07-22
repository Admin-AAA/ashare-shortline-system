"""LLM Skill 审查结果缓存服务。

cache_key 严格只 hash 影响判断的字段：skill_hash + 精简股票特征 + 市场快照 + 规则信号。
元数据字段（data_source/quote_source/kline_source/latest_trade_date）排除——
同一股票不同 run 来源不同，但不影响 LLM 判断，纳入会让缓存几乎永远不命中。

跨 run 持久化：相同输入零 LLM 调用。skill 文件改动 / 市场状态变化 / 规则信号变化
→ cache_key 自动失效。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.models.tables import LLMReviewCache

# LLM 审查健康度阈值：formal run 每日盘前跑一次，故以 24h 为「新鲜」窗口。
# 超过 24h 无成功审查即视为 stale（可能 key 失效 / provider 故障），前端据此告警。
_LLM_REVIEW_STALE_HOURS = 24


# 元数据字段：同一股票不同 run 来源不同，但不影响 LLM 判断，从 cache_key 排除。
# data_quality 保留（数据缺口会影响 LLM 评分）。
_CACHE_KEY_EXCLUDE_STOCK_FIELDS = {"data_source", "quote_source", "kline_source", "latest_trade_date"}


def _stable_json(value: Any) -> str:
    """稳定 JSON 序列化：sort_keys 保证 dict key 顺序稳定；list 顺序保留（是语义）。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def build_review_cache_key(
    *,
    skill_hash: str | None,
    stock: dict[str, Any],
    snapshot: dict[str, Any],
    rule_signal: dict[str, Any] | None,
) -> str:
    """生成 LLM 审查结果缓存 key。只含影响判断的字段。"""
    stock_clean = {
        k: v for k, v in (stock or {}).items()
        if k not in _CACHE_KEY_EXCLUDE_STOCK_FIELDS
    }
    payload = {
        "skill_hash": skill_hash or "",
        "stock": stock_clean,
        "snapshot": snapshot or {},
        "rule_signal": rule_signal or {},
    }
    raw = _stable_json(payload)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_cached_review(db: Session, cache_key: str) -> dict[str, Any] | None:
    """查缓存。命中返回 review dict，未命中返回 None。"""
    row = db.get(LLMReviewCache, cache_key)
    if row is None:
        return None
    try:
        return json.loads(row.review_json)
    except (json.JSONDecodeError, TypeError):
        return None


def write_cached_review(
    db: Session,
    *,
    cache_key: str,
    skill_id: str,
    skill_hash: str | None,
    trade_date: str | None,
    code: str | None,
    review: dict[str, Any],
    model: str | None,
) -> None:
    """写入缓存（upsert）。"""
    row = db.get(LLMReviewCache, cache_key)
    review_json = json.dumps(review, ensure_ascii=False, default=str)
    if row is None:
        db.add(LLMReviewCache(
            cache_key=cache_key,
            skill_id=skill_id,
            skill_hash=skill_hash,
            trade_date=trade_date,
            code=code,
            review_json=review_json,
            model=model,
        ))
    else:
        row.skill_hash = skill_hash
        row.trade_date = trade_date
        row.code = code
        row.review_json = review_json
        row.model = model
    db.commit()


def llm_review_health(db: Session) -> dict[str, Any]:
    """LLM 审查服务健康度：以 llm_review_cache 的最近命中时间为新鲜度代理。

    为什么用缓存命中时间做代理：缓存仅在 LLM 审查【成功】时才写入；若模型 key 失效 /
    provider 故障 / 路由降级跳过审查，则不再有新缓存写入 → last_review_at 逐渐陈旧。

    价值（北极星 B 项可见性缺口）：此前用户在模型 key 过期时完全无感知——
    系统静默给出「缺 LLM delta」的推荐，驾驶舱无任何告警。本端点把「AI 审查层是否仍在
    真实工作」变成可观测指标，让用户一眼看到 freshness，及时排查 key / provider。

    注意：这是【服务可用性】观测，不等同于「LLM 评分质量」。质量由 R131 的幻觉 /
    净价值校验覆盖；此处只回答「审查层活着吗、多久没成功了」。
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    agg = db.execute(text("SELECT COUNT(*), MAX(created_at) FROM llm_review_cache")).fetchone()
    total = int(agg[0] or 0)
    last_at: datetime | None = agg[1]
    recent_24h = int(
        db.execute(
            text("SELECT COUNT(*) FROM llm_review_cache WHERE created_at >= :cut"),
            {"cut": now - timedelta(hours=24)},
        ).scalar()
        or 0
    )
    model: str | None = None
    if last_at is not None:
        m = db.execute(
            text("SELECT model FROM llm_review_cache ORDER BY created_at DESC LIMIT 1")
        ).fetchone()
        model = m[0] if m else None
    return {
        "last_review_at": last_at.isoformat() if last_at else None,
        "recent_count_24h": recent_24h,
        "total_entries": total,
        "model": model,
        "status": _llm_health_status(last_at, recent_24h, total, now),
        "checked_at": now.isoformat(),
    }


def _call_health_rate(succ: int, fail: int) -> tuple[float, bool]:
    """由 skill_review 成功/失败数算成功率 + 是否低成功率（<80%）。纯函数，可单测。"""
    total = succ + fail
    rate = (succ / total * 100) if total > 0 else 0.0
    return round(rate, 1), rate < 80.0


def recent_llm_call_health(db: Session, *, limit: int = 5) -> dict[str, Any]:
    """最近 N 个全市场 formal run 的 LLM 调用成功率 + 失败原因（run 级过程健康）。

    与 llm_review_health（缓存新鲜度，服务可用性代理）正交：本函数看「skill_review 调用
    本身是否成功」——formal run 标 agent_runtime=llm 但 skill_review 全失败时（如模型熔断/
    余额不足），推荐级 delta 全缺失，缓存新鲜度可能仍 ok（命中旧 cache），但实际新 run 的
    LLM 全失败。本函数从 result_summary_json 提取 skill_review_success/failure_audit，
    让 LLM 层失效立即可见。

    返回 {runs: [{trade_date, success, failed, rate, unhealthy, top_reasons}], any_unhealthy}。
    """
    rows = db.execute(text("""
        SELECT trade_date,
               JSON_UNQUOTE(JSON_EXTRACT(result_summary_json, '$.agent_runtime')) AS ar,
               JSON_UNQUOTE(JSON_EXTRACT(result_summary_json, '$.skill_input_count')) AS input_cnt,
               JSON_UNQUOTE(JSON_EXTRACT(result_summary_json, '$.pipeline.skill_review_success_count')) AS succ,
               JSON_UNQUOTE(JSON_EXTRACT(result_summary_json, '$.pipeline.skill_review_failure_audit.failed_count')) AS fail,
               JSON_UNQUOTE(JSON_EXTRACT(result_summary_json, '$.pipeline.skill_review_failure_audit.reason_counts')) AS reasons
        FROM recommendation_runs
        WHERE run_mode='formal' AND status='completed' AND universe_count > 4000
        ORDER BY started_at DESC LIMIT :lim
    """), {"lim": limit}).fetchall()

    runs: list[dict[str, Any]] = []
    any_unhealthy = False
    for r in rows:
        td, ar = r[0], r[1]
        inp = int(r[2]) if r[2] not in (None, "null") else 0
        succ = int(r[3]) if r[3] not in (None, "null") else 0
        fail = int(r[4]) if r[4] not in (None, "null") else 0
        # agent_review_disabled 时 ar != 'llm'，跳过（非 LLM 模式不计入健康判定）
        if ar != "llm" or inp == 0:
            continue
        rate, unhealthy = _call_health_rate(succ, fail)
        any_unhealthy = any_unhealthy or unhealthy
        reasons = json.loads(r[5]) if r[5] and r[5] != "null" else {}
        top_reasons = sorted(reasons.items(), key=lambda x: -int(x[1] or 0))[:3] if reasons else []
        runs.append({
            "trade_date": str(td),
            "success": succ,
            "failed": fail,
            "rate": rate,
            "unhealthy": unhealthy,
            "top_reasons": [{"reason": k, "count": int(v or 0)} for k, v in top_reasons],
        })
    return {"runs": runs, "any_unhealthy": any_unhealthy}


def _llm_health_status(
    last_at: datetime | None,
    recent_24h: int,
    total: int,
    now: datetime,
) -> str:
    """纯函数：LLM 审查健康状态判定（便于单测，不碰 DB）。

    状态语义：
      - never：缓存完全为空 → 从未成功审查过（部署/配置缺失）
      - stale：最近一次成功审查距今 > 24h，或近 24h 零命中 → 审查层可能已停摆
      - ok   ：近 24h 内有成功审查 → 审查层真实在工作
    """
    if total == 0 or last_at is None:
        return "never"
    age_h = (now - last_at).total_seconds() / 3600.0
    if age_h > _LLM_REVIEW_STALE_HOURS or recent_24h == 0:
        return "stale"
    return "ok"
