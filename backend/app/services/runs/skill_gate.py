from __future__ import annotations

from collections import Counter
from typing import Any


def resolve_prefilter_target_count(params: dict[str, Any], selected_skill_count: int) -> int:
    score_line = float(params.get("score_line") or 72.0)
    base = 36 if str(params.get("run_mode") or "formal") == "formal" else 24
    if score_line < 70:
        base += 12
    elif score_line >= 78:
        base -= 6
    return max(base, selected_skill_count * 12, 24)


def resolve_skill_review_limit(params: dict[str, Any], settings: Any, selected_skill_count: int = 1) -> int:
    if bool(params.get("disable_agent_review")):
        return 0
    skill_review_limit = int(params.get("agent_review_limit") or settings.market_skill_review_limit or 0)
    if skill_review_limit > 0:
        return skill_review_limit
    score_line = float(params.get("score_line") or 72.0)
    base = 28
    if score_line < 70:
        base += 10
    elif score_line >= 78:
        base -= 6
    base = max(base, selected_skill_count * 8)
    return min(max(base, 8), 60)


def build_skill_rule_gate(
    prelim_entries: list[dict[str, Any]],
    selected_skills: list[dict[str, Any]],
    settings: Any,
) -> dict[str, Any]:
    min_score = float(getattr(settings, "market_skill_rule_gate_min_score", 58.0) or 58.0)
    min_per_skill = max(0, int(getattr(settings, "market_skill_rule_gate_min_per_skill", 20) or 0))
    min_total = max(0, int(getattr(settings, "market_skill_rule_gate_min_total", 20) or 0))
    config_max_per_skill = max(0, int(getattr(settings, "market_skill_rule_gate_max_per_skill", 0) or 0))
    max_total = max(min_total, int(getattr(settings, "market_skill_rule_gate_max_total", 120) or 0))
    selected_ids = [str(item["skill_id"]) for item in selected_skills]
    by_code: dict[str, dict[str, Any]] = {}
    skill_rows: dict[str, list[dict[str, Any]]] = {skill_id: [] for skill_id in selected_ids}
    per_skill_audit: dict[str, dict[str, Any]] = {
        skill_id: {
            "scanned": len(prelim_entries),
            "rule_eligible": 0,
            "gate_pass": 0,
            "llm_reviewed": 0,
            "skipped_hard_veto": 0,
            "skipped_low_score": 0,
            "skipped_no_rule": 0,
            "high_quality_count": 0,
            "mid_quality_count": 0,
        }
        for skill_id in selected_ids
    }

    for entry in prelim_entries:
        stock = entry["stock"]
        code = str(stock.get("code") or "")
        by_code[code] = {
            **entry,
            "review_skill_ids": [],
            "skill_gate_reasons": {},
            "skill_gate_scores": {},
            "skill_quality_tier": {},
        }
        for skill_id in selected_ids:
            signal = (entry.get("rule_signals") or {}).get(skill_id)
            if not signal:
                per_skill_audit[skill_id]["skipped_no_rule"] += 1
                continue
            if signal.get("hard_veto"):
                per_skill_audit[skill_id]["skipped_hard_veto"] += 1
                by_code[code]["skill_gate_reasons"][skill_id] = signal.get("veto_reason") or "规则硬否决"
                continue
            gate_score = skill_gate_score(signal, entry)
            by_code[code]["skill_gate_scores"][skill_id] = gate_score
            if gate_score >= min_score:
                per_skill_audit[skill_id]["rule_eligible"] += 1
                if gate_score >= 80:
                    tier = "high"
                    per_skill_audit[skill_id]["high_quality_count"] += 1
                elif gate_score >= 70:
                    tier = "mid"
                    per_skill_audit[skill_id]["mid_quality_count"] += 1
                else:
                    tier = "standard"
                skill_rows[skill_id].append(
                    {
                        "code": code,
                        "score": gate_score,
                        "tier": tier,
                        "reason": skill_gate_reason(signal, gate_score, min_score),
                    }
                )
            else:
                per_skill_audit[skill_id]["skipped_low_score"] += 1
                by_code[code]["skill_gate_reasons"][skill_id] = f"规则门分 {round(gate_score, 2)} 低于 {round(min_score, 2)}"

    selected_pairs: list[dict[str, Any]] = []
    selected_pair_keys: set[tuple[str, str]] = set()

    for skill_id, rows in skill_rows.items():
        eligible = per_skill_audit[skill_id]["rule_eligible"]
        if not rows:
            continue
        if config_max_per_skill > 0:
            dynamic_max = max(min_per_skill, min(eligible, config_max_per_skill))
        else:
            dynamic_max = max(min_per_skill, min(eligible, max(20, int(eligible * 0.1))))
        dynamic_max = min(dynamic_max, 30)
        per_skill_audit[skill_id]["dynamic_max_per_skill"] = dynamic_max

        rows.sort(key=lambda row: float(row["score"]), reverse=True)
        high_tier = [row for row in rows if row["tier"] == "high"]
        mid_tier = [row for row in rows if row["tier"] == "mid"]
        standard_tier = [row for row in rows if row["tier"] == "standard"]

        selected_for_skill: list[dict[str, Any]] = []
        for row in high_tier:
            if len(selected_for_skill) >= dynamic_max:
                break
            selected_for_skill.append(row)

        remaining = dynamic_max - len(selected_for_skill)
        if remaining > 0 and mid_tier:
            mid_sample = min(len(mid_tier), max(len(mid_tier) // 2, remaining))
            selected_for_skill.extend(mid_tier[:mid_sample])

        remaining = dynamic_max - len(selected_for_skill)
        if remaining > 0 and standard_tier:
            selected_for_skill.extend(standard_tier[:remaining])

        if len(selected_for_skill) < min_per_skill:
            remaining_rows = [row for row in rows if row not in selected_for_skill]
            selected_for_skill.extend(remaining_rows[: min_per_skill - len(selected_for_skill)])

        for row in selected_for_skill:
            key = (row["code"], skill_id)
            if key in selected_pair_keys:
                continue
            selected_pair_keys.add(key)
            selected_pairs.append({**row, "skill_id": skill_id})
            per_skill_audit[skill_id]["gate_pass"] += 1

    selected_pairs.sort(key=lambda row: float(row["score"]), reverse=True)
    if max_total and len(selected_pairs) > max_total:
        selected_pairs = selected_pairs[:max_total]
        selected_pair_keys = {(row["code"], row["skill_id"]) for row in selected_pairs}

    if len(selected_pairs) < min_total:
        fallback_rows: list[dict[str, Any]] = []
        for skill_id, rows in skill_rows.items():
            for row in rows:
                key = (row["code"], skill_id)
                if key not in selected_pair_keys:
                    fallback_rows.append({**row, "skill_id": skill_id})
        fallback_rows.sort(key=lambda row: float(row["score"]), reverse=True)
        for row in fallback_rows[: max(0, min_total - len(selected_pairs))]:
            selected_pair_keys.add((row["code"], row["skill_id"]))
            selected_pairs.append(row)

    for row in selected_pairs:
        entry = by_code.get(row["code"])
        if not entry:
            continue
        skill_id = row["skill_id"]
        if skill_id not in entry["review_skill_ids"]:
            entry["review_skill_ids"].append(skill_id)
            entry["skill_gate_reasons"][skill_id] = row["reason"]
            entry["skill_quality_tier"][skill_id] = row.get("tier", "standard")
            per_skill_audit[skill_id]["llm_reviewed"] += 1

    skill_candidates = [entry for entry in by_code.values() if entry.get("review_skill_ids")]
    skill_candidates.sort(
        key=lambda entry: max((entry.get("skill_gate_scores") or {"_": entry.get("prelim_score", 0)}).values()),
        reverse=True,
    )
    non_skill_entries = [
        entry
        for entry in prelim_entries
        if str(entry["stock"].get("code") or "") not in {item["stock"].get("code") for item in skill_candidates}
    ]
    audit_rows = build_skill_gate_rows(by_code, selected_ids, min_score)
    audit = {
        "mode": "rule_gate_dynamic_tiered",
        "min_score": min_score,
        "min_per_skill": min_per_skill,
        "min_total": min_total,
        "max_total": max_total,
        "quality_tiers": {"high": ">=80 全部深审", "mid": ">=70 按容量采样", "standard": ">=min_score 填充剩余"},
        "skill_rule_candidate_count": len({row["code"] for row in selected_pairs}),
        "skill_gate_candidate_count": len(skill_candidates),
        "skill_agent_call_count": len(selected_pairs),
        "per_skill": per_skill_audit,
        "rows": audit_rows,
        "reason_counts": dict(Counter(row["reason_key"] for row in audit_rows if not row.get("passed"))),
    }
    return {
        "skill_candidates": skill_candidates,
        "non_skill_entries": non_skill_entries,
        "audit": audit,
    }


def build_skill_gate_rows(
    by_code: dict[str, dict[str, Any]],
    selected_ids: list[str],
    min_score: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for code, entry in by_code.items():
        stock = entry.get("stock") or {}
        rule_signals = entry.get("rule_signals") or {}
        gate_scores = {sid: float(score) for sid, score in (entry.get("skill_gate_scores") or {}).items()}
        gate_reasons = dict(entry.get("skill_gate_reasons") or {})
        review_skill_ids = [str(item) for item in (entry.get("review_skill_ids") or [])]
        best_skill_id = None
        best_gate_score = None
        if gate_scores:
            best_skill_id, best_gate_score = max(gate_scores.items(), key=lambda item: float(item[1]))

        if review_skill_ids:
            reason_key = "gate_pass"
            reason = gate_reasons.get(review_skill_ids[0]) or "规则门通过，进入 Skill 深审"
            passed = True
        else:
            present_signals = [rule_signals.get(skill_id) for skill_id in selected_ids if rule_signals.get(skill_id)]
            hard_veto_reasons = [
                str(signal.get("veto_reason") or "规则硬否决")
                for signal in present_signals
                if signal and signal.get("hard_veto")
            ]
            if not present_signals:
                reason_key = "no_rule_signal"
                reason = "无可用规则信号"
            elif best_gate_score is not None and best_gate_score >= min_score:
                reason_key = "gate_capacity"
                reason = "规则门分合格，但未进入本次 Skill 深审容量"
            elif hard_veto_reasons and len(hard_veto_reasons) >= len(present_signals):
                reason_key = "hard_veto"
                reason = "；".join(sorted(set(hard_veto_reasons))[:3])
            elif best_skill_id and best_skill_id in gate_reasons:
                reason_key = "low_gate_score"
                reason = gate_reasons[best_skill_id]
            else:
                reason_key = "low_gate_score"
                reason = f"规则门分低于 {round(min_score, 2)}"
            passed = False

        rows.append(
            {
                "code": code,
                "name": stock.get("name"),
                "industry": stock.get("industry"),
                "stage": "skill_gate",
                "passed": passed,
                "reason_key": reason_key,
                "reason": reason,
                "prelim_score": float(entry.get("prelim_score") or 0),
                "best_skill_id": best_skill_id,
                "best_gate_score": best_gate_score,
                "gate_scores": gate_scores,
                "gate_reasons": gate_reasons,
                "review_skill_ids": review_skill_ids,
                "quality_tier": entry.get("skill_quality_tier") or {},
            }
        )
    return rows


def skill_gate_score(signal: dict[str, Any], entry: dict[str, Any]) -> float:
    score = float(signal.get("score") or 0)
    if signal.get("setup_match"):
        score += 8.0
    if float(signal.get("score") or 0) >= 80:
        score += 6.0
    elif float(signal.get("score") or 0) >= 64:
        score += 3.0
    score += min(4.0, float(signal.get("setup_quality") or 50.0) / 25.0)
    score += min(2.0, float(entry.get("prelim_score") or 0) / 50.0)
    return round(score, 2)


def skill_gate_reason(signal: dict[str, Any], gate_score: float, min_score: float) -> str:
    reasons = [f"规则门分 {round(gate_score, 2)} >= {round(min_score, 2)}"]
    if signal.get("setup_match"):
        reasons.append(f"买点: {signal.get('setup_match')}")
    signal_score = float(signal.get("score") or 0)
    if signal_score >= 80:
        reasons.append(f"高分信号: {signal_score}")
    elif signal_score >= 64:
        reasons.append(f"中等信号: {signal_score}")
    return "；".join(reasons)


def finalize_skill_gate_audit(
    audit: dict[str, Any],
    skill_candidates: list[dict[str, Any]],
    explicit_review_limit_skipped: int = 0,
) -> dict[str, Any]:
    finalized = dict(audit or {})
    finalized["skill_rule_candidate_count"] = len(skill_candidates)
    finalized["skill_gate_candidate_count"] = len(skill_candidates)
    finalized["skill_agent_call_count"] = sum(len(entry.get("review_skill_ids") or []) for entry in skill_candidates)
    finalized["explicit_review_limit_skipped"] = explicit_review_limit_skipped
    per_skill = finalized.get("per_skill") or {}
    for stats in per_skill.values():
        stats["llm_reviewed"] = 0
    for entry in skill_candidates:
        for skill_id in entry.get("review_skill_ids") or []:
            if skill_id in per_skill:
                per_skill[skill_id]["llm_reviewed"] += 1
    selected_codes = {str((entry.get("stock") or {}).get("code") or "") for entry in skill_candidates}
    rows = list(finalized.get("rows") or [])
    if rows:
        reason_counts: Counter[str] = Counter()
        for row in rows:
            code = str(row.get("code") or "")
            review_selected = code in selected_codes
            row["review_selected"] = review_selected
            if row.get("passed") and not review_selected:
                row["review_limit_reason"] = "规则门通过，但被本次 Agent 深审上限截断"
                reason_counts["review_limit"] += 1
            elif not row.get("passed"):
                reason_counts[str(row.get("reason_key") or "filtered")] += 1
        finalized["rows"] = rows
        finalized["reason_counts"] = dict(reason_counts)
    return finalized
