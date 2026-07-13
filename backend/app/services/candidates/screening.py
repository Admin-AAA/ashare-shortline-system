"""候选召回与打分（任务 4.3）：候选排除规则 / 打分子函数 / 召回计划 / 候选筛选排序。

## 迁移说明
本文件是从 `market_data.py` 原地搬迁的**权威实现**（module extraction，
不改变任何函数逻辑/参数/返回值）：`_candidate_exclusion_reason` /
`_candidate_filter_summary` / `_eligible_market_quotes` /
`_candidate_recall_plan` / `_select_recall_candidates` / `_select_candidates` /
`_candidate_score` / `_candidate_amount_score` / `_candidate_pct_score` /
`_candidate_turnover_score` / `_candidate_trend_score`。`market_data.py`
改为从本模块 re-export 这些名字（保持
`from app.services.market_data import _candidate_score` 等既有导入路径不
失效，包括 `backend/tests/test_market_candidate_scoring.py`），不再保留
重复实现。

这些函数是"候选召回/打分"职责：从全市场报价里排除不可交易标的
（ST/停牌/无量等）、给每只候选股票打综合分、按打分+行业配额做召回排序——
与"采集"（拉取报价/K线原始数据）无关，按设计 §6 归入 `candidates/screening`，
移出 market_data 的采集职责范围。

## 避免循环 import 的说明
`_candidate_exclusion_reason` 依赖的 `_is_supported_a_share`
（判断代码是否为受支持的 A 股，非候选打分职责，仍留在 `market_data.py`）
与 `_select_recall_candidates` 依赖的 `_top_industries_from_quotes`
（已迁至 `market_source/snapshot.py`，任务 4.3 同批搬迁）分别处理：
- `_top_industries_from_quotes` 来自 `market_source.snapshot`，是单向依赖
  （该模块不反向依赖本模块），直接模块级 import 即可，不会成环。
- `_is_supported_a_share` 仍定义在 `market_data.py`，而 `market_data.py`
  又需要从本模块 re-export `_candidate_score` 等名字，会形成模块级双向
  import；因此对 `_is_supported_a_share` 使用**函数内延迟 import**，只在
  调用时才解析，不在模块加载时建立循环依赖。
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any

from app.services.market_source.snapshot import _top_industries_from_quotes


def _eligible_market_quotes(quotes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in quotes
        if _candidate_exclusion_reason(item) is None
    ]


def _candidate_recall_plan(total_quote_count: int, eligible_count: int, settings: Any) -> dict[str, Any]:
    explicit_scan_limit = max(0, int(getattr(settings, "market_candidate_count", 0) or 0))
    lookahead_multiplier = max(1, int(getattr(settings, "market_candidate_lookahead_multiplier", 4) or 1))
    broad_recall_default = max(0, int(getattr(settings, "market_broad_recall_count", 1800) or 0))
    full_market_mode = explicit_scan_limit <= 0

    if explicit_scan_limit > 0:
        broad_recall_count = min(
            eligible_count,
            max(explicit_scan_limit, explicit_scan_limit * lookahead_multiplier),
        )
        scan_target_count = min(eligible_count, explicit_scan_limit)
    else:
        if broad_recall_default <= 0:
            broad_recall_count = eligible_count
        else:
            broad_recall_count = min(eligible_count, broad_recall_default)
        scan_target_count = broad_recall_count

    return {
        "full_market_mode": full_market_mode,
        "explicit_scan_limit": explicit_scan_limit,
        "lookahead_multiplier": lookahead_multiplier,
        "broad_recall_count": broad_recall_count,
        "scan_target_count": scan_target_count,
        "broad_recall_limited": broad_recall_count < eligible_count,
        "total_quote_count": total_quote_count,
        "eligible_count": eligible_count,
    }


def _select_recall_candidates(quotes: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    if not quotes:
        return []

    ordered = list(quotes)
    if count <= 0 or count >= len(ordered):
        return sorted(
            ordered,
            key=lambda item: (_candidate_score(item), float(item.get("amount_yi") or 0)),
            reverse=True,
        )

    window = min(len(ordered), max(count * 3, 160))
    recall_scores: dict[str, float] = defaultdict(float)
    recall_factors: dict[str, set[str]] = defaultdict(set)
    ranking_specs = [
        ("composite", lambda item: _candidate_score(item), 1.0),
        ("amount", lambda item: float(item.get("amount_yi") or 0), 0.75),
        ("turnover", lambda item: _candidate_turnover_score(float(item.get("turnover_rate") or 0)), 0.65),
        ("pct", lambda item: _candidate_pct_score(float(item.get("pct") or 0)), 0.65),
        ("trend", lambda item: _candidate_trend_score(float(item.get("chg60") or 0)), 0.55),
    ]
    for factor_name, key_fn, weight in ranking_specs:
        ranked = sorted(ordered, key=key_fn, reverse=True)[:window]
        for rank, item in enumerate(ranked):
            code = str(item.get("code") or "")
            if not code:
                continue
            recall_scores[code] += weight * (window - rank)
            recall_factors[code].add(factor_name)

    industry_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in ordered:
        industry = str(item.get("industry") or "未分类")
        if industry != "未分类":
            industry_rows[industry].append(item)
    industry_quota = max(2, min(8, max(1, count // 150)))
    for industry in _top_industries_from_quotes(ordered)[:12]:
        ranked = sorted(
            industry_rows.get(industry, []),
            key=lambda item: (_candidate_score(item), float(item.get("amount_yi") or 0)),
            reverse=True,
        )[:industry_quota]
        for rank, item in enumerate(ranked):
            code = str(item.get("code") or "")
            if not code:
                continue
            recall_scores[code] += max(12.0, window * 0.35) - rank
            recall_factors[code].add("industry")

    selected = sorted(
        ordered,
        key=lambda item: (
            recall_scores.get(str(item.get("code") or ""), 0.0),
            len(recall_factors.get(str(item.get("code") or ""), set())),
            _candidate_score(item),
            float(item.get("amount_yi") or 0),
        ),
        reverse=True,
    )[:count]
    result = [dict(item) for item in selected]
    for item in result:
        code = str(item.get("code") or "")
        item["_quote_recall_score"] = round(recall_scores.get(code, 0.0), 2)
        item["_quote_recall_factors"] = sorted(recall_factors.get(code, set()))
    return result


def _select_candidates(quotes: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    filtered = [
        item
        for item in quotes
        if _candidate_exclusion_reason(item) is None
    ]
    filtered.sort(key=_candidate_score, reverse=True)
    return filtered[:count]


def _candidate_exclusion_reason(item: dict[str, Any]) -> str | None:
    from app.services.market_data import _is_supported_a_share  # 延迟 import，避免模块级循环依赖

    if not _is_supported_a_share(item):
        return "unsupported_market"
    name = str(item.get("name") or "")
    if item.get("is_st") or "ST" in name.upper() or "退" in name:
        return "st_or_delisting_risk"
    if float(item.get("price") or 0) <= 1:
        return "invalid_price"
    if float(item.get("amount_yi") or 0) <= 0 or float(item.get("turnover_rate") or 0) <= 0:
        return "suspended_or_no_trade"
    return None


def _candidate_filter_summary(quotes: list[dict[str, Any]]) -> dict[str, Any]:
    exclusion_counts: Counter[str] = Counter()
    eligible_count = 0
    high_liquidity_count = 0
    tradeable_quote_count = 0
    for item in quotes:
        reason = _candidate_exclusion_reason(item)
        if reason is not None:
            exclusion_counts[reason] += 1
            continue
        eligible_count += 1
        if float(item.get("amount_yi") or 0) >= 0.5 and float(item.get("turnover_rate") or 0) >= 0.1:
            high_liquidity_count += 1
        if float(item.get("amount_yi") or 0) >= 1.5 and 1.0 <= float(item.get("turnover_rate") or 0) <= 12.0:
            tradeable_quote_count += 1
    return {
        "total_count": len(quotes),
        "base_eligible_count": eligible_count,
        "excluded_count": len(quotes) - eligible_count,
        "high_liquidity_count": high_liquidity_count,
        "low_liquidity_count": max(0, eligible_count - high_liquidity_count),
        "tradeable_quote_count": tradeable_quote_count,
        "exclusion_counts": dict(exclusion_counts),
        "high_liquidity_threshold": {
            "amount_yi": 0.5,
            "turnover_rate": 0.1,
        },
        "preferred_tradeability_threshold": {
            "amount_yi": 1.5,
            "turnover_rate_min": 1.0,
            "turnover_rate_max": 12.0,
        },
    }


def _candidate_score(item: dict[str, Any]) -> float:
    return (
        _candidate_amount_score(float(item.get("amount_yi") or 0))
        + _candidate_pct_score(float(item.get("pct") or 0))
        + _candidate_turnover_score(float(item.get("turnover_rate") or 0))
        + _candidate_trend_score(float(item.get("chg60") or 0))
    )


def _candidate_amount_score(amount_yi: float) -> float:
    if amount_yi <= 0:
        return -20.0
    return min(18.0, math.log1p(amount_yi) * 7.5)


def _candidate_pct_score(pct: float) -> float:
    if pct <= -7:
        return -10.0
    if pct <= -3:
        return -5.0
    if pct < 0:
        return -1.5
    if pct <= 1:
        return 4.0
    if pct <= 5:
        return 15.0 - abs(pct - 3.0) * 2.0
    if pct <= 8:
        return 11.0 - (pct - 5.0) * 1.5
    return max(-6.0, 6.5 - (pct - 8.0) * 2.4)


def _candidate_turnover_score(turnover_rate: float) -> float:
    if turnover_rate < 0.8:
        return -8.0
    if turnover_rate < 2.0:
        return 3.0 + (turnover_rate - 0.8) * 4.0
    if turnover_rate <= 8.0:
        return 15.0 - abs(turnover_rate - 5.0) * 1.2
    if turnover_rate <= 12.0:
        return 8.5 - (turnover_rate - 8.0) * 2.0
    return max(-8.0, 0.5 - (turnover_rate - 12.0) * 1.8)


def _candidate_trend_score(chg60: float) -> float:
    if chg60 <= -15:
        return -8.0
    if chg60 < 0:
        return -2.5
    if chg60 <= 5:
        return 2.0 + chg60 * 0.6
    if chg60 <= 30:
        return 5.0 + (chg60 - 5.0) * 0.32
    if chg60 <= 50:
        return 13.0 - (chg60 - 30.0) * 0.22
    return max(-4.0, 8.6 - (chg60 - 50.0) * 0.28)
