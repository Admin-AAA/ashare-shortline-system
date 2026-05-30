from __future__ import annotations

from collections import Counter
from typing import Any

from app.engine.strategy_runner import evaluate_skill_prefilter


def apply_prefilters(
    stocks: list[dict[str, Any]],
    selected_skills: list[dict[str, Any]],
    min_candidate_count: int = 24,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ordered_stocks = list(stocks)

    # 策略优先架构：不再使用全局一刀切预过滤。
    # 每个策略通过 strategy.yaml 的 universe.include/exclude 规则自主定义候选范围。
    # common_tradeability_exclusion_reason 保留为可选参考函数，策略可在 universe 中引用。
    common_filtered: list[dict[str, Any]] = ordered_stocks
    common_exclusion_counts: Counter[str] = Counter()
    common_reason_by_code: dict[str, str | None] = {}

    per_skill: dict[str, dict[str, Any]] = {}
    union_codes: set[str] = set()
    pass_skill_ids_by_code: dict[str, list[str]] = {}
    skill_reason_by_code: dict[str, dict[str, str]] = {}

    for selected in selected_skills:
        skill = selected["skill"]
        skill_id = str(selected["skill_id"])
        pass_count = 0
        exclusion_counts: Counter[str] = Counter()
        sample_codes: list[str] = []

        for stock in common_filtered:
            passed, reason = evaluate_skill_prefilter(skill, stock, stage="market")
            code = str(stock.get("code") or "")
            skill_reason_by_code.setdefault(code, {})[skill_id] = "passed" if passed else str(reason or "filtered")
            if passed:
                pass_count += 1
                if code:
                    union_codes.add(code)
                    pass_skill_ids_by_code.setdefault(code, []).append(skill_id)
                    if len(sample_codes) < 8:
                        sample_codes.append(code)
            else:
                exclusion_counts[reason] += 1

        per_skill[skill_id] = {
            "name": selected.get("name") or skill.name,
            "input_count": len(common_filtered),
            "pass_count": pass_count,
            "excluded_count": max(0, len(common_filtered) - pass_count),
            "exclusion_counts": dict(exclusion_counts),
            "sample_codes": sample_codes,
        }

    filtered = []
    for stock in common_filtered:
        code = str(stock.get("code") or "")
        if code not in union_codes:
            continue
        stock["prefilter_skill_ids"] = sorted(pass_skill_ids_by_code.get(code, []))
        stock["prefilter_source"] = "skill_pass"
        filtered.append(stock)
    union_pass_count = len(filtered)
    fill_count = 0
    fallback_used = False

    target_count = min(len(common_filtered), max(0, int(min_candidate_count)))
    if target_count and len(filtered) < target_count:
        chosen_codes = {str(stock.get("code") or "") for stock in filtered}
        for stock in common_filtered:
            code = str(stock.get("code") or "")
            if not code or code in chosen_codes:
                continue
            stock["prefilter_skill_ids"] = sorted(pass_skill_ids_by_code.get(code, []))
            stock["prefilter_source"] = "fill"
            filtered.append(stock)
            chosen_codes.add(code)
            fill_count += 1
            if len(filtered) >= target_count:
                break

    if not filtered:
        source = common_filtered or ordered_stocks
        fallback_count = min(len(source), max(12, target_count or 12))
        filtered = []
        for stock in source[:fallback_count]:
            code = str(stock.get("code") or "")
            stock["prefilter_skill_ids"] = sorted(pass_skill_ids_by_code.get(code, []))
            stock["prefilter_source"] = "fallback"
            filtered.append(stock)
        fallback_used = True

    filtered_code_set = {str(stock.get("code") or "") for stock in filtered}
    rows: list[dict[str, Any]] = []
    for stock in ordered_stocks:
        code = str(stock.get("code") or "")
        common_reason = common_reason_by_code.get(code)
        common_pass = common_reason is None
        skill_pass_ids = sorted(pass_skill_ids_by_code.get(code, []))
        if not common_pass:
            stage = "prefilter_common"
            reason = common_reason or "common_prefilter_filtered"
            passed = False
        elif code in filtered_code_set:
            source = str(stock.get("prefilter_source") or "skill_pass")
            stage = "prefilter_pass"
            reason = "技能预过滤命中" if source == "skill_pass" else f"{source} 补入候选"
            passed = True
        else:
            stage = "prefilter_skill"
            reason = "未命中任何 Skill 市场预过滤"
            passed = False
        rows.append(
            {
                "code": code,
                "name": stock.get("name"),
                "industry": stock.get("industry"),
                "stage": stage,
                "passed": passed,
                "reason": reason,
                "common_pass": common_pass,
                "common_reason": common_reason,
                "skill_pass_ids": skill_pass_ids,
                "skill_reasons": skill_reason_by_code.get(code, {}),
                "prefilter_source": stock.get("prefilter_source") if code in filtered_code_set else None,
                "price": stock.get("price") or stock.get("close"),
                "pct": stock.get("pct"),
                "amount_yi": stock.get("amount_yi"),
                "turnover_rate": stock.get("turnover_rate"),
            }
        )

    audit = {
        "mode": "skill_driven_universe_prefilter",
        "input_count": len(ordered_stocks),
        "common": {
            "pass_count": len(common_filtered),
            "excluded_count": 0,
            "exclusion_counts": {},
            "note": "全局预过滤已移除；各策略通过 strategy.yaml universe 规则自主筛选",
        },
        "per_skill": per_skill,
        "union_pass_count": union_pass_count,
        "prefilter_candidate_count": len(filtered),
        "fill_count": fill_count,
        "fallback_used": fallback_used,
        "selected_skill_count": len(selected_skills),
        "rows": rows,
    }
    return filtered, audit


def common_tradeability_exclusion_reason(stock: dict[str, Any]) -> str | None:
    amount_yi = float(stock.get("amount_yi") or 0)
    turnover_rate = float(stock.get("turnover_rate") or 0)
    pos60 = float(stock.get("pos60") or 0)
    dist60 = float(stock.get("dist60") or 0)
    off_high_pct = float(stock.get("off_high_pct") or 0)
    pct = float(stock.get("pct") or 0)
    upper_shadow_count20 = float(stock.get("upper_shadow_count20") or 0)
    max_dd20 = float(stock.get("max_dd20") or 0)

    if amount_yi < 1.5:
        return "low_amount"
    if turnover_rate < 1.0:
        return "low_turnover"
    if turnover_rate > 12.0 and pct > 0:
        return "overheated_turnover"
    if pos60 > 96.0 and dist60 > 35.0:
        return "late_acceleration"
    if off_high_pct < -18.0 and pct < 0:
        return "broken_trend"
    if upper_shadow_count20 >= 6:
        return "upper_shadow_overhang"
    if max_dd20 < -18.0:
        return "unstable_drawdown"
    return None
