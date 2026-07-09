from typing import Any

from app.engine.evaluator import evaluate
from app.engine.skill_loader import StrategySkill


def run_strategy(
    skill: StrategySkill,
    stock: dict[str, Any],
    main_industries: list[str],
    skip_universe_check: bool = False,
) -> dict[str, Any]:
    contract = skill.contract or {}
    # 实证权重：把 strategy.yaml 顶层 weight 注入 signal，让 aggregator 按历史胜率加权。
    # 此前该字段从未注入 → aggregator 永远用 weight=1.0（等权），净负策略与赢家同权污染 final_score。
    skill_weight = float(contract.get("weight", 1.0))
    context = dict(stock)
    industry = stock.get("industry")
    context["is_main_theme"] = bool(
        stock.get("is_main_theme")
        or (industry in set(main_industries) and industry != "未分类")
    )
    data_quality = dict(stock.get("data_quality") or {})
    missing_fields = set(data_quality.get("missing_fields") or [])
    data_completeness = float(stock.get("data_completeness_score") or data_quality.get("data_completeness_score") or 100.0)
    evidence_quality = float(stock.get("evidence_quality_score") or data_quality.get("evidence_quality_score") or 100.0)
    coverage_cap = _evidence_score_cap(
        sample_count=int(stock.get("market_sample_count") or 0),
        data_completeness=data_completeness,
        evidence_quality=evidence_quality,
        missing_fields=missing_fields,
    )

    if not skip_universe_check and not _passes_universe(contract, context):
        return _signal(skill, score=0, phase="排除", hard_veto=True, veto_reason="不在策略股票池", risk_flags=["不符合股票池"], weight=skill_weight)

    phase = "未分类"
    score_cap = 100.0
    hard_veto = False
    veto_reason = ""
    risk_flags: list[str] = []
    evidence: list[str] = []

    for rule in contract.get("phases", []):
        if evaluate(str(rule.get("when", "")), context):
            phase = str(rule.get("name", phase))
            score_cap = float(rule.get("score_cap", score_cap))
            hard_veto = bool(rule.get("hard_veto", False))
            risk_flags.extend(rule.get("risk_flags", []) or [])
            evidence.append(f"阶段命中: {phase}")
            break

    scoring = contract.get("scoring", {})
    score = float(scoring.get("base", 50))
    for field_rule in scoring.get("score_fields", []) or []:
        field = str(field_rule.get("field", "")).strip()
        if not field:
            continue
        raw_value = context.get(field, 0)
        try:
            value = float(raw_value or 0)
        except (TypeError, ValueError):
            value = 0.0
        multiplier = float(field_rule.get("multiplier", 1.0))
        cap = field_rule.get("cap")
        floor = field_rule.get("floor")
        delta = value * multiplier
        if floor is not None:
            delta = max(float(floor), delta)
        if cap is not None:
            delta = min(float(cap), delta)
        score += delta
        reason = str(field_rule.get("reason") or field)
        evidence.append(f"{reason}: {round(delta, 2)}")
    for rule in scoring.get("rules", []) or []:
        if evaluate(str(rule.get("when", "")), context):
            delta = float(rule.get("delta", 0))
            score += delta
            if reason := rule.get("reason"):
                evidence.append(str(reason))

    setup_name = ""
    entry_rule = ""
    invalid_rule = ""
    setup_quality = 50.0
    for setup in contract.get("setups", []) or []:
        if evaluate(str(setup.get("when", "")), context):
            setup_name = str(setup.get("name", ""))
            score += float(setup.get("score_delta", 0))
            setup_quality = min(100.0, 60.0 + float(setup.get("score_delta", 0)))
            entry_rule = str(setup.get("entry_rule", ""))
            invalid_rule = str(setup.get("invalid_rule", ""))
            # "观察"类 setup 命中即否决：策略自己标"观察"=不确定，不该入场
            if bool(setup.get("hard_veto", False)):
                hard_veto = True
                veto_reason = str(setup.get("veto_reason") or f"观察类 setup 命中: {setup_name}")
            evidence.append(f"买点命中: {setup_name}")
            break

    # require_setup: 策略声明"必须有明确入场 setup 才能推荐"
    # 适用于 setup 即入场必要条件的策略（如突破接力：突破但量价不达标 = 无入场理由）
    # setup_name 为空 = 没有命中任何 setup，若策略要求 require_setup 则 hard_veto
    if not setup_name and bool(contract.get("require_setup", False)):
        hard_veto = True
        veto_reason = "未命中任何入场 setup（策略要求 require_setup）"

    for rule in contract.get("risk", {}).get("rules", []) or []:
        if evaluate(str(rule.get("when", "")), context):
            risk_flags.append(str(rule.get("flag", "风险提示")))
            if "score_delta" in rule:
                score += float(rule.get("score_delta", 0))
            elif rule.get("level") == "high":
                score -= 12

    score_cap = min(score_cap, coverage_cap)
    if coverage_cap < 82:
        risk_flags.append("样本或证据覆盖不足，评分已降权")
        evidence.append(f"诚实性上限: {round(coverage_cap, 1)}")

    score = max(0.0, min(score, score_cap))
    return _signal(
        skill,
        score=score,
        phase=phase,
        hard_veto=hard_veto,
        veto_reason=veto_reason or ("硬风控阶段命中" if hard_veto else ""),
        risk_flags=risk_flags,
        setup_match=setup_name,
        entry_rule=entry_rule,
        invalid_rule=invalid_rule,
        evidence=evidence,
        setup_quality=setup_quality,
        score_cap=score_cap,
        weight=skill_weight,
    )


def _passes_universe(contract: dict[str, Any], context: dict[str, Any]) -> bool:
    universe = contract.get("universe", {})
    for expr in universe.get("include", []) or []:
        if not evaluate(str(expr), context, strict_missing=True):
            return False
    for expr in universe.get("exclude", []) or []:
        if evaluate(str(expr), context, strict_missing=True):
            return False
    # prefilter.market.exclude 总是执行（即使 skip_universe_check=True）
    # 回测与实盘一致性：avg_cost>200 等硬过滤规则不应被跳过
    market_prefilter = (contract.get("prefilter") or {}).get("market") or {}
    for rule in market_prefilter.get("exclude", []) or []:
        if evaluate(_rule_expr(rule), context, strict_missing=True):
            return False
    return True


def evaluate_skill_prefilter(
    skill: StrategySkill,
    stock: dict[str, Any],
    stage: str = "market",
) -> tuple[bool, str]:
    contract = skill.contract or {}
    context = dict(stock)
    universe = contract.get("universe", {}) or {}
    prefilter = ((contract.get("prefilter") or {}).get(stage) or {})

    # universe/prefilter 用 strict_missing：字段缺失时 `dist20 >= 0` 等规则
    # 不再因 _coerce(None)=0.0 静默放行（死猫跳 dist20 缺失被当多头排列放行致止损）。
    # include 缺失→False→拒绝（安全）；exclude 缺失→False→不排除→放行，但
    # exclude 用到的字段（is_st/twenty_cm_risk_gate/dragon_tiger_is_on_list 等）
    # 均由 _build_stock_from_bars 必然赋值，不会缺失，故 exclude 行为不变。
    for rule in universe.get("include", []) or []:
        if not evaluate(_rule_expr(rule), context, strict_missing=True):
            return False, _rule_reason(rule, "universe_include")
    for rule in universe.get("exclude", []) or []:
        if evaluate(_rule_expr(rule), context, strict_missing=True):
            return False, _rule_reason(rule, "universe_exclude")

    for rule in prefilter.get("include", []) or []:
        if not evaluate(_rule_expr(rule), context, strict_missing=True):
            return False, _rule_reason(rule, "prefilter_include")

    include_any = prefilter.get("include_any", []) or []
    if include_any and not any(evaluate(_rule_expr(rule), context, strict_missing=True) for rule in include_any):
        return False, "prefilter_include_any"

    for rule in prefilter.get("exclude", []) or []:
        if evaluate(_rule_expr(rule), context, strict_missing=True):
            return False, _rule_reason(rule, "prefilter_exclude")

    return True, "pass"


def _rule_expr(rule: Any) -> str:
    if isinstance(rule, dict):
        return str(rule.get("when", "")).strip()
    return str(rule).strip()


def _rule_reason(rule: Any, fallback: str) -> str:
    if isinstance(rule, dict):
        return str(rule.get("reason") or rule.get("name") or fallback)
    return fallback


def _signal(
    skill: StrategySkill,
    score: float,
    phase: str,
    hard_veto: bool,
    veto_reason: str = "",
    risk_flags: list[str] | None = None,
    setup_match: str = "",
    entry_rule: str = "",
    invalid_rule: str = "",
    evidence: list[str] | None = None,
    setup_quality: float = 50.0,
    score_cap: float = 100.0,
    weight: float = 1.0,
) -> dict[str, Any]:
    score = min(score, score_cap)
    return {
        "strategy_id": skill.skill_id,
        "strategy_name": skill.name,
        "version_id": f"{skill.skill_id}:{skill.version}:{skill.contract_hash[:8] if skill.contract_hash else 'none'}",
        "strategy_independence": _strategy_independence(skill),
        "weight": round(float(weight), 3),
        "phase": phase,
        "score": round(score, 2),
        "score_cap": round(score_cap, 2),
        "confidence": round(min(0.95, max(0.2, score / 100)), 2),
        "setup_match": setup_match,
        "entry_rule": entry_rule,
        "invalid_rule": invalid_rule,
        "setup_quality": setup_quality,
        "hard_veto": hard_veto,
        "veto_reason": veto_reason,
        "risk_flags": risk_flags or [],
        "evidence": evidence or [],
    }


def _strategy_independence(skill: StrategySkill) -> float:
    meta = (getattr(skill, "contract", None) or {}).get("meta") or {}
    try:
        return max(0.0, min(1.0, float(meta.get("strategy_independence", 0.85))))
    except (TypeError, ValueError):
        return 0.85


def _evidence_score_cap(
    sample_count: int,
    data_completeness: float,
    evidence_quality: float,
    missing_fields: set[str],
) -> float:
    cap = 100.0
    if sample_count and sample_count < 20:
        cap = min(cap, 68.0)
    elif sample_count and sample_count < 100:
        cap = min(cap, 78.0)
    if data_completeness < 65:
        cap = min(cap, 72.0)
    if evidence_quality < 55:
        cap = min(cap, 76.0)
    if {"industry", "news", "announcements"}.issubset(missing_fields):
        cap = min(cap, 74.0)
    return cap
