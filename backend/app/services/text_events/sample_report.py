"""抽样标注的汇总。成本只在配置了每百万 token 单价时计算，否则只报 token。"""
from __future__ import annotations

import statistics
from typing import Any, Sequence


def build_sample_report(results: Sequence[Any], *, price_prompt: float = 0.0, price_completion: float = 0.0) -> dict[str, Any]:
    rows = list(results)
    total = len(rows)
    failed = [row for row in rows if row.status == "failed"]
    skipped = [row for row in rows if row.status == "skipped"]
    labeled = [row for row in rows if row.label and row.status in {"ok", "cached"}]
    distribution: dict[str, int] = {}
    categories: dict[str, int] = {}
    for row in labeled:
        distribution[row.label] = distribution.get(row.label, 0) + 1
        if row.category:
            categories[row.category] = categories.get(row.category, 0) + 1
    latencies = [float(row.latency_ms) for row in rows if row.latency_ms and not row.cached and row.label_origin == "llm"]
    prompt_tokens = _sum(rows, "prompt_tokens")
    completion_tokens = _sum(rows, "completion_tokens")
    compared = [row for row in labeled if row.keyword_label]
    agreed = [row for row in compared if row.label == row.keyword_label]
    disagreements = []
    for row in compared:
        if row.label == row.keyword_label:
            continue
        disagreements.append({
            "source": row.source,
            "event_id": row.event_id,
            "title": (row.title or "")[:80],
            "label": row.label,
            "keyword_label": row.keyword_label,
            "rationale": row.rationale,
            "label_origin": row.label_origin,
        })
        if len(disagreements) >= 20:
            break
    cost = None
    if price_prompt > 0 or price_completion > 0:
        cost = round(prompt_tokens / 1_000_000 * price_prompt + completion_tokens / 1_000_000 * price_completion, 6)
    return {
        "count": total,
        "labeled": len(labeled),
        "cached": sum(1 for row in rows if row.cached),
        "rule": sum(1 for row in labeled if row.label_origin == "rule"),
        "llm": sum(1 for row in labeled if row.label_origin == "llm"),
        "failed": len(failed),
        "skipped": len(skipped),
        "failure_rate": round(len(failed) / total, 4) if total else 0.0,
        "label_distribution": distribution,
        "category_distribution": categories,
        "latency_ms": {
            "n": len(latencies),
            "mean": round(statistics.fmean(latencies), 1) if latencies else None,
            "p50": round(statistics.median(latencies), 1) if latencies else None,
            "p95": round(_percentile(latencies, 0.95), 1) if latencies else None,
        },
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cost": cost,
        "cost_note": None if cost is not None else "未配置 ASHARE_TEXT_EVENT_LLM_PRICE_PER_MILLION_PROMPT / _COMPLETION，只统计 token",
        "keyword_agreement": {
            "n": len(compared),
            "agreed": len(agreed),
            "rate": round(len(agreed) / len(compared), 4) if compared else None,
        },
        "disagreements": disagreements,
        "disclaimer": "仅供参考，不参与选股",
        "participates_in_selection": False,
    }


def _sum(rows: Sequence[Any], name: str) -> int:
    total = 0
    for row in rows:
        value = getattr(row, name, None)
        if value:
            total += int(value)
    return total


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return ordered[index]
