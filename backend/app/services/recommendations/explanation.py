from __future__ import annotations

import json
from typing import Any

from app.schemas.dashboard import DataQualitySummary


def loads_json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, dict | list):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return default


def risk_level_from_tags(tags: list[str]) -> str:
    high_terms = ("退市", "监管", "跌停", "重大利空", "高位分歧", "断板")
    medium_terms = ("放量", "乖离", "回撤", "减持", "解禁", "分歧")

    if not tags:
        return "low"

    joined = " ".join(str(tag) for tag in tags)
    if any(term in joined for term in high_terms):
        return "high"
    if any(term in joined for term in medium_terms):
        return "medium"
    return "medium"


def consensus_label(skill_breakdown: list[dict[str, Any]]) -> str:
    stances = [
        _get(_get(row, "agent_skill_review", {}) or {}, "stance")
        for row in skill_breakdown
        if _get(_get(row, "agent_skill_review", {}) or {}, "stance")
    ]

    if not stances:
        return "未知"

    stance_set = set(stances)
    if "support" in stance_set and ({"oppose", "veto"} & stance_set):
        return "多空冲突"
    if len(stance_set) == 1:
        return "一致"
    return "轻微分歧"


def data_quality_summary(feature_snapshot: dict[str, Any]) -> dict[str, Any]:
    data_quality = _get(feature_snapshot, "data_quality", {}) or {}
    source_trace = _get(feature_snapshot, "source_trace", {}) or {}
    defaults = DataQualitySummary().model_dump()

    quote_status = (
        _get(data_quality, "quote_status")
        or _get(data_quality, "quote_cache_status")
        or _get(source_trace, "quote_status")
        or _get(source_trace, "quote_cache_status")
        or defaults["quote_status"]
    )
    quote_source_count = (
        _get(data_quality, "quote_source_count")
        or _get(source_trace, "quote_source_count")
        or _count_sources(_get(source_trace, "quote_sources"))
        or defaults["quote_source_count"]
    )
    kline_status = (
        _get(data_quality, "kline_status")
        or _get(data_quality, "kline_cache_status")
        or _get(source_trace, "kline_status")
        or _get(source_trace, "kline_cache_status")
        or defaults["kline_status"]
    )
    kline_days = (
        _get(data_quality, "kline_days")
        or _get(source_trace, "kline_days")
        or defaults["kline_days"]
    )
    auxiliary_coverage = (
        _get(data_quality, "auxiliary_coverage")
        or _get(source_trace, "auxiliary_coverage")
        or defaults["auxiliary_coverage"]
    )
    missing_fields = (
        _get(data_quality, "missing_fields")
        or _get(source_trace, "missing_fields")
        or defaults["missing_fields"]
    )

    return {
        "quote_status": quote_status,
        "quote_source_count": int(quote_source_count),
        "kline_status": kline_status,
        "kline_days": int(kline_days),
        "auxiliary_coverage": float(auxiliary_coverage),
        "missing_fields": list(missing_fields),
    }


def _count_sources(sources: Any) -> int:
    if isinstance(sources, list | tuple | set):
        return len(sources)
    return 0


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)
