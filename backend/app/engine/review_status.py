"""Agent 审查状态的唯一业务语义。"""
from __future__ import annotations

from typing import Any, Iterable

REVIEW_VERIFIED = "verified"
REVIEW_PARTIAL = "partial"
REVIEW_DEGRADED = "degraded"
REVIEW_RULE_ONLY = "rule_only"


def is_real_agent_review(review: Any) -> bool:
    return (
        isinstance(review, dict)
        and bool(review)
        and str(review.get("runtime") or "") != "agent_skill_fallback_prelim"
    )


def classify_review_results(
    reviews: Iterable[dict[str, Any]], *, agent_review_disabled: bool = False
) -> str:
    if agent_review_disabled:
        return REVIEW_RULE_ONLY
    rows = list(reviews)
    succeeded = sum(1 for row in rows if is_real_agent_review(row.get("review")) and not row.get("error"))
    if succeeded == len(rows) and rows:
        return REVIEW_VERIFIED
    if succeeded:
        return REVIEW_PARTIAL
    return REVIEW_DEGRADED


__all__ = [
    "REVIEW_DEGRADED",
    "REVIEW_PARTIAL",
    "REVIEW_RULE_ONLY",
    "REVIEW_VERIFIED",
    "classify_review_results",
    "is_real_agent_review",
]
