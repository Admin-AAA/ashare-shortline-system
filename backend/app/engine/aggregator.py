from typing import Any

# 保持旧接口兼容：从 agent_skill_runner 导入 enrich_signal_with_agent_review
# 调用方可以通过此模块直接访问该函数
from app.engine.agent_skill_runner import enrich_signal_with_agent_review  # noqa: F401
from app.engine.board_rules import (
    is_one_word_limit_up_card as _is_one_word_limit_up_card,
)
from app.engine.return_improvement_shadow import (
    annotate_return_improvement_shadow,
)
from app.engine.execution_constraints import (
    entry_no_liquidity_reason_from_card as _entry_no_liquidity_reason,
)
from app.engine.aggregator_pure import (  # noqa: F401,E402  re-export (split)
    AGENT_PRESENT_WEIGHTS,
    AGG_WEIGHT_KEYS,
    BASE_UP_RATIO_BULL,
    BASE_UP_RATIO_BULLISH,
    BASE_UP_RATIO_RANGE,
    COMPONENT_KEYS,
    DEFAULT_AGG_WEIGHTS,
    RISK_OVERLAY_WEIGHTS,
    RISK_SEVERITY_PENALTY,
    RISK_TEMPLATE_PENALTY,
    SENTIMENT_ACTIVE_LU_LD_RATIO,
    SENTIMENT_LIMIT_DOWN_RATIO,
    SENTIMENT_LIMIT_UP_RATE_ACTIVE,
    SENTIMENT_LIMIT_UP_RATE_FLAT,
    SENTIMENT_LIMIT_UP_RATE_HOT,
    STANCE_MULTIPLIER,
    STRUCTURAL_STRONG_HARD_VETO_REGIME,
    _INDUSTRY_GROUPS,
    _MAX_PER_INDUSTRY,
    _RISK_SEVERITY_RULES,
    _SECTOR_DIVERSITY_PENALTY,
    _SECTOR_DIVERSITY_PENALTY_DEFAULT,
    _apply_sector_diversity,
    _apply_slot_reserve,
    _card_has_research_signal,
    _card_score_line_adjustment,
    _collect_risks,
    _consensus_boost,
    _consensus_score,
    _count_hit_strategies,
    _dedup_info_tags,
    _dedup_risk_flags,
    _detect_skill_conflicts,
    _empty,
    _estimated_win_rate,
    _execution_viability,
    _execution_viability_result,
    _extract_skill_agent_score,
    _float,
    _industry_group,
    _is_20cm_board,
    _market_base,
    _market_discipline,
    _market_regime_label,
    _market_sentiment,
    _quality_risks,
    _quality_score_cap,
    _rationale,
    _research_consensus_exempt,
    _research_extra_skill_ids,
    _risk_severity,
    _shortline_factor_risks,
    _shortline_factor_summary,
    _skill_independence_factor,
    _stance_summary,
    _time_int,
    apply_pool_rules,
    market_regime_weights,
    resolve_display_limit,
    resolve_score_line_context,
    skill_disagreement_metrics,
)


# ============================================================================
# 内部评分函数
# ============================================================================


def _compute_strategy_rule_score(signals: list[dict[str, Any]]) -> float:
    """计算 Strategy.yaml 规则信号综合得分（加权平均）。"""
    if not signals:
        return 50.0
    weighted_total = 0.0
    weight_sum = 0.0
    for signal in signals:
        weight = float(signal.get("weight", 1.0))
        # 策略规则使用原始分数（不经过 stance 调整）
        weighted_total += float(signal.get("score", 0)) * weight
        weight_sum += weight
    return weighted_total / weight_sum if weight_sum > 0 else 50.0


# ============================================================================
# 核心聚合
# ============================================================================

def aggregate(
    stock: dict[str, Any],
    signals: list[dict[str, Any]],
    market_score: float,
    agent_review_score: float,
    risk_template: str = "balanced",
    calibrated_weights: dict[str, float] | None = None,
    market_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """聚合所有维度分数，产生最终推荐卡片。

    参数签名保持向后兼容：
        agent_review_score 作为 Skill Agent 评分的基线参与计算。
        market_snapshot 用于动态调整聚合权重（可选，不传则用默认权重）。
    """
    if not signals:
        return _empty(stock, "无策略信号")

    # --- 硬否决：检查 hard_veto 标记（含 stance=veto 的 signal） ---
    # "不在策略股票池" 只表示该策略不适用，不是风险否决；fill/fallback 路径会给
    # 未过 prefilter 的兄弟策略打上该标记，若当成 hard_veto 会把整张卡 final_score
    # 置 0（研究技能永远出不了分）。与 skill_pass 路径（根本不挂这类 signal）对齐：忽略。
    _UNIVERSE_MISS = "不在策略股票池"
    # 只有策略规则 hard_veto 能挡票；LLM stance=veto/oppose 只写风险备注，不进 hard_vetoes。
    hard_vetoes: list[dict[str, Any]] = []
    for signal in signals:
        if signal.get("hard_veto"):
            if str(signal.get("veto_reason") or "") == _UNIVERSE_MISS:
                continue
            # LLM 曾经把 stance=veto 写成 hard_veto；出票路径忽略（L-171）
            if str(signal.get("llm_influence_mode") or "") == "risk_overlay" and not signal.get("veto_reason"):
                continue
            if str(signal.get("veto_reason") or "").startswith("Agent Skill"):
                continue
            hard_vetoes.append(signal)
    # 评分只用"适用"信号，避免 universe-miss 的 score=0 拉低 strategy_rule
    signals_for_score = [
        s for s in signals
        if not (s.get("hard_veto") and str(s.get("veto_reason") or "") == _UNIVERSE_MISS)
    ]
    if not signals_for_score:
        return {
            **_empty(stock, "无适用策略信号"),
            "strategy_signals": signals,
            "risk_flags": _collect_risks(signals),
            "info_tags": [],
            "consensus_score": 0.0,
        }
    if hard_vetoes:
        signal = hard_vetoes[0]
        review = signal.get("agent_skill_review")
        veto_reason = signal.get("veto_reason")
        if not veto_reason and isinstance(review, dict):
            veto_reason = review.get("summary", "硬风控否决")
        return {
            **_empty(stock, veto_reason or "硬风控否决"),
            "strategy_signals": signals,
            "risk_flags": _collect_risks(signals),
            "info_tags": [],
            "consensus_score": 0.0,
        }

    # --- 各维度评分 ---
    # Skill Agent 核心评分：从 agent_skill_review.stance 提取信号进行加权
    skill_agent_score = _extract_skill_agent_score(signals_for_score)
    # Strategy.yaml 规则辅助评分
    strategy_rule_score = _compute_strategy_rule_score(signals_for_score)
    # 风险调整分：按严重级别分级扣分，info 级不扣分
    shortline_factor_risks = _shortline_factor_risks(stock)
    all_risks = _collect_risks(signals_for_score) + shortline_factor_risks
    risk_penalty = sum(
        RISK_SEVERITY_PENALTY.get(_risk_severity(r), 5.0) for r in all_risks
    ) * RISK_TEMPLATE_PENALTY.get(risk_template, 1.0)
    risk_adjustment_score = max(0.0, 100.0 - min(45.0, risk_penalty))
    # 共识分（保留旧机制用于信息展示，但不再单独加权）
    consensus_score = _consensus_score(signals_for_score)
    # 证据质量
    evidence_quality_score = float(stock.get("evidence_quality_score") or 60.0)
    # 券商金股共识加分
    broker_recs = int(stock.get("broker_rec_count") or 0)
    if broker_recs >= 3:
        evidence_quality_score = min(100.0, evidence_quality_score + 8)
    elif broker_recs >= 1:
        evidence_quality_score = min(100.0, evidence_quality_score + 3)
    quality_cap = _quality_score_cap(stock)

    # --- 检测是否无 Agent 审查（回测/纯规则模式）---
    has_agent_reviews = any(
        isinstance(s.get("agent_skill_review"), dict)
        and str((s.get("agent_skill_review") or {}).get("runtime") or "") != "agent_skill_fallback_prelim"
        for s in signals
    )
    # --- 加权聚合（校准权重为基底 + 阶段动态调整） ---
    if market_snapshot:
        weights = market_regime_weights(market_snapshot, calibrated=calibrated_weights)
    else:
        weights = dict(calibrated_weights or DEFAULT_AGG_WEIGHTS)

    # LLM 审查只做风险 overlay；策略规则始终负责 alpha 和排序。
    if has_agent_reviews:
        weights.update(RISK_OVERLAY_WEIGHTS)
    else:
        weights["skill_agent"] = 0.00
        weights["strategy_rule"] = 0.55
        weights["market"] = 0.20
        weights["risk"] = 0.15
        weights["evidence"] = 0.10

    # 校准权重已作为基底传入 market_regime_weights，不再二次覆盖

    # 归一化
    total_w = sum(weights.values())
    weights = {k: v / total_w for k, v in weights.items()}

    # 防御性守卫：任意组件分偶发为 None（如上游客观评分/市场分未就绪，
    # 曾于 07-22 03:52 正式 run 因市场分 None 触发
    # `TypeError: type NoneType doesn't define __round__` 使整条管线崩溃）时，
    # 不得以 None 进入 round()。中性默认值保持评分逻辑不变，纯故障隔离，
    # 不改动策略/门线/聚合权重。
    skill_agent_score = float(skill_agent_score if skill_agent_score is not None else 50.0)
    strategy_rule_score = float(strategy_rule_score if strategy_rule_score is not None else 50.0)
    market_score = float(market_score if market_score is not None else 50.0)
    risk_adjustment_score = float(risk_adjustment_score if risk_adjustment_score is not None else 50.0)
    evidence_quality_score = float(evidence_quality_score if evidence_quality_score is not None else 60.0)

    raw_component_scores = {
        "skill_agent": round(skill_agent_score, 2),
        "strategy_rule": round(strategy_rule_score, 2),
        "market": round(market_score, 2),
        "risk": round(risk_adjustment_score, 2),
        "evidence": round(evidence_quality_score, 2),
    }
    weighted_component_scores = {
        key: round(weights.get(key, 0.0) * value, 2)
        for key, value in raw_component_scores.items()
    }

    weighted_base_score = (
        weights.get("skill_agent", 0.50) * skill_agent_score
        + weights.get("strategy_rule", 0.25) * strategy_rule_score
        + weights.get("market", 0.10) * market_score
        + weights.get("risk", 0.10) * risk_adjustment_score
        + weights.get("evidence", 0.05) * evidence_quality_score
    )

    # --- 提取 stance 摘要 ---
    stance_summary = _stance_summary(signals_for_score)

    # --- 共识加权：多 Skill 一致高分/联合否决/方向对齐 → 加成或惩罚 ---
    consensus_boost, consensus_tags = _consensus_boost(signals_for_score, stance_summary)
    final_score = weighted_base_score * consensus_boost

    # --- 真实性折扣 ---
    authenticity = float(stock.get("data_authenticity", 1.0))
    data_quality = stock.get("data_quality") or {}
    source_breakdown = data_quality.get("source_breakdown", {})
    synthetic_ratio = float(source_breakdown.get("SYNTHETIC", 0))
    total_sources = sum(float(v) for v in source_breakdown.values()) if source_breakdown else 1.0
    synthetic_pct = synthetic_ratio / max(total_sources, 1.0)

    authenticity_discount_applied = False
    if authenticity < 0.7:
        final_score *= 0.85
        authenticity_discount_applied = True
    if authenticity < 0.5:
        final_score *= 0.75
        authenticity_discount_applied = True

    # 真实性严重不足 + SYNTHETIC 主导 → 标记严重折扣
    authenticity_severe_discount = False
    if authenticity < 0.5 and synthetic_pct > 0.5:
        authenticity_severe_discount = True

    final_score = min(final_score, quality_cap)
    component_scale = final_score / max(weighted_base_score, 1e-6)
    component_scores = {
        key: round(value * component_scale, 2)
        for key, value in raw_component_scores.items()
    }

    quality_risks, quality_infos = _quality_risks(stock, quality_cap)
    conflict_risks = _detect_skill_conflicts(signals_for_score)

    # --- 最佳信号 ---
    best_setup = max(signals_for_score, key=lambda signal: float(signal.get("score", 0)))

    market_discipline = _market_discipline(
        stock=stock,
        market_snapshot=market_snapshot,
        final_score=final_score,
        risk_template=risk_template,
    )

    # 将所有候选标记按严重级别分流：high/medium → risk_flags，info → info_tags
    all_candidate_flags = list(dict.fromkeys(
        all_risks
        + quality_risks
        + conflict_risks
        + market_discipline["risk_flags"]
        + quality_infos
    ))
    final_risk_flags: list[str] = []
    info_tags: list[str] = []
    for flag in all_candidate_flags:
        sev = _risk_severity(flag)
        if sev == "info":
            info_tags.append(flag)
        else:
            final_risk_flags.append(flag)

    final_risk_flags = _dedup_risk_flags(final_risk_flags)
    # 去重合并 info_tags：语义相近的只保留一条
    info_tags = _dedup_info_tags(info_tags)

    return {
        "code": stock["code"],
        "name": stock["name"],
        "industry": stock.get("industry"),
        "recommend_price": stock.get("price", stock.get("close", 0)),
        "stop_loss": stock.get("stop_loss"),
        "take_profit": stock.get("take_profit"),
        "final_score": round(final_score, 2),
        "selection_score": round(final_score, 2),
        "portfolio_rank_score": round(final_score, 2),
        "consensus_score": round(consensus_score, 2),
        "setup_type": best_setup.get("setup_match") or "",
        # 没有策略给出明确入场规则时保持为空；前端会诚实标注“未保存明确买点”，
        # 避免“等待买点确认”这种无法执行、也无法验证的循环提示。
        "entry_rule": best_setup.get("entry_rule") or "",
        "invalid_rule": best_setup.get("invalid_rule") or "跌破关键支撑且收不回",
        "entry_mode": best_setup.get("entry_mode") or "target_touch",
        # 出场归属策略（Sprint3 §67）：逐策略 exit_overrides 按它套用。
        "exit_strategy_id": best_setup.get("strategy_id"),
        "rationale": _rationale(signals_for_score),
        "risk_flags": final_risk_flags,
        "info_tags": info_tags,
        "shortline_factors": _shortline_factor_summary(stock),
        "shortline_factor_explanations": stock.get("shortline_factor_explanations") or {},
        "strategy_signals": signals,
        "component_scores": component_scores,
        "raw_component_scores": raw_component_scores,
        "weighted_component_scores": weighted_component_scores,
        "score_breakdown": {
            "raw_scores": raw_component_scores,
            "weighted_scores": weighted_component_scores,
            "weights": {key: round(value, 4) for key, value in weights.items()},
            "llm_influence_mode": "risk_overlay" if has_agent_reviews else "rule_only",
            "consensus_boost": round(consensus_boost, 4),
            "quality_cap": round(quality_cap, 2),
            "final_score": round(final_score, 2),
        },
        "evidence_quality_score": round(evidence_quality_score, 2),
        "data_completeness_score": float(stock.get("data_completeness_score") or 0.0),
        "data_quality": data_quality,
        "data_authenticity": authenticity,
        "consensus_boost": round(consensus_boost, 4),
        "consensus_tags": consensus_tags,
        "stance_summary": stance_summary,
        "authenticity_severe_discount": authenticity_severe_discount,
        "aggregation_weights": weights,
        "llm_influence_mode": "risk_overlay" if has_agent_reviews else "rule_only",
        "component_scale": round(component_scale, 4),
        "market_regime": _market_regime_label(market_snapshot),
        "skill_disagreement": skill_disagreement_metrics(signals_for_score),
        "market_discipline": market_discipline,
        "open": stock.get("open"),
        "high": stock.get("high"),
        "low": stock.get("low"),
        "close": stock.get("close"),
        "pct": stock.get("pct"),
        "volume": stock.get("volume"),
        "amount_yi": stock.get("amount_yi"),
        "is_limit_up": bool(stock.get("is_limit_up")),
        "limit_up_first_time": stock.get("limit_up_first_time"),
        "limit_up_seal_time": stock.get("limit_up_seal_time"),
        "limit_up_last_time": stock.get("limit_up_last_time"),
        "limit_up_open_times": stock.get("limit_up_open_times"),
        "limit_up_fd_amount_yi": stock.get("limit_up_fd_amount_yi"),
        "limit_up_turnover_ratio": stock.get("limit_up_turnover_ratio"),
        # MA 支撑信号（用于 apply_pool_rules 中的市场阶段×信号交叉调整）
        "support_ma5": bool(stock.get("support_ma5")),
        "support_ma10": bool(stock.get("support_ma10")),
    }


# ============================================================================
# 内部辅助函数
# ============================================================================

# 数据驱动（T5 第79轮反事实验证）：MAX=3 剔除的第4只同行业股 WR=100% avg+5.24%（误杀）
# 放宽到 5：保留 WR 57.6% avg+0.27%（vs MAX=3 的 56.5%/+0.12%），净收益 +2.56%→+26.03%

for _ind in ("元器件", "半导体", "通信设备", "消费电子", "电子制造"):
    _INDUSTRY_GROUPS[_ind] = "科技硬件"
for _ind in ("电池", "新能源车", "汽车零部件", "光伏"):
    _INDUSTRY_GROUPS[_ind] = "新能源"
for _ind in ("证券", "保险", "银行"):
    _INDUSTRY_GROUPS[_ind] = "金融"


