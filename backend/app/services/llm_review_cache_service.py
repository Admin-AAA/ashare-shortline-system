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
from typing import Any

from sqlalchemy.orm import Session

from app.models.tables import LLMReviewCache


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
