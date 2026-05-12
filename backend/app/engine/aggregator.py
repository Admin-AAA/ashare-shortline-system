from collections import Counter
from typing import Any

# 保持旧接口兼容：从 agent_skill_runner 导入 enrich_signal_with_agent_review
# 调用方可以通过此模块直接访问该函数
from app.engine.agent_skill_runner import enrich_signal_with_agent_review  # noqa: F401

RISK_TEMPLATE_PENALTY = {
    "conservative": 1.4,
    "balanced": 1.0,
    "aggressive": 0.7,
}

# ---------------------------------------------------------------------------
# 权重体系：Skill Agent 成为核心评分来源
# 去掉了 setup、consensus 独立维度，它们已融入 skill_agent
# ---------------------------------------------------------------------------
DEFAULT_AGG_WEIGHTS = {
    "skill_agent": 0.50,     # Skill Agent 核心评分（曾 agent: 0.05）
    "strategy_rule": 0.25,   # Strategy.yaml 规则辅助（曾 strategy: 0.36）
    "market": 0.10,          # 市场环境（曾 0.20）
    "risk": 0.10,            # 风险调整（保持）
    "evidence": 0.05,        # 证据质量（曾 0.04）
}

# ---------------------------------------------------------------------------
# Stance 语义映射：从 Skill Agent 的 stance 映射到分数乘数
# veto 映射为字符串 "hard_veto"，表示硬否决（直接返回空推荐卡）
# ---------------------------------------------------------------------------
STANCE_MULTIPLIER = {
    "strong_support": 1.15,
    "support": 1.08,       # 与 strong_support 拉开差距，更精确区分支持力度
    "neutral_support": 1.0,
    "neutral": 0.85,
    "oppose": 0.6,
    "veto": "hard_veto",  # 硬否决，不参与分数计算
}


# ============================================================================
# 内部评分函数
# ============================================================================

def _extract_skill_agent_score(signals: list[dict[str, Any]]) -> float:
    """从 signals 中提取 Skill Agent 综合评分。

    优先使用 agent_skill_review.stance 映射的 STANCE_MULTIPLIER 调整后的分数，
    取所有信号的加权平均。veto stance 的信号分数归零。
    """
    skill_scores: list[tuple[float, float]] = []  # (score, weight)
    for signal in signals:
        review = signal.get("agent_skill_review")
        if not isinstance(review, dict):
            continue
        stance = review.get("stance", "neutral")
        score = float(signal.get("score", 0))
        weight = float(signal.get("weight", 1.0))

        multiplier = STANCE_MULTIPLIER.get(stance)
        if multiplier == "hard_veto":
            # veto 的 stock 应在 aggregate 入口就被 hard_veto 捕获
            skill_scores.append((0.0, weight))
            continue

        adjusted_score = score * float(multiplier or 1.0)
        skill_scores.append((adjusted_score, weight))

    if not skill_scores:
        return 50.0

    weighted_total = sum(s * w for s, w in skill_scores)
    weight_sum = sum(w for _, w in skill_scores)
    return weighted_total / max(weight_sum, 1e-6)


def _consensus_boost(
    signals: list[dict[str, Any]],
    stance_summary: list[dict[str, Any]] | None = None,
) -> tuple[float, list[str]]:
    """共识加权 + 立场协同/冲突机制。

    返回 (boost_multiplier, consensus_tags)。

    包含四个维度：
    1. 高分一致性加成：多 Skill 一致高分（>= 70）时额外加成
    2. 联合否决打击（strike counting）：2+ Skill 都给出 oppose 时降低得分
    3. 一致方向加成：所有 Skill 方向一致时提升置信度
    4. 严重分歧惩罚：strong_support 与 veto 同时出现时标记争议
    """
    consensus_tags: list[str] = []
    boost = 1.0

    # ── 1. 高分一致性加成 ──
    agreeing_skills = sum(
        1 for signal in signals
        if float(signal.get("score", 0)) >= 70
    )
    if agreeing_skills >= 2:
        boost = 1.0 + 0.1 * (agreeing_skills - 1)
        consensus_tags.append("high_agreement")

    # ── 2. 联合否决打击：2+ Skill 给出 oppose 时额外扣分 ──
    oppose_count = sum(
        1 for signal in signals
        if (review := signal.get("agent_skill_review"))
        and isinstance(review, dict)
        and review.get("stance") == "oppose"
    )
    if oppose_count >= 2:
        boost *= 0.90
        consensus_tags.append("multi_oppose_strike")

    # ── 3. 一致方向加成 ──
    if stance_summary and len(stance_summary) >= 2:
        bullish_stances = {"strong_support", "support", "neutral_support"}
        bearish_stances = {"oppose", "veto"}

        all_bullish = all(
            s.get("stance") in bullish_stances for s in stance_summary
        )
        all_bearish = all(
            s.get("stance") in bearish_stances for s in stance_summary
        )

        if all_bullish:
            boost *= 1.05
            consensus_tags.append("all_bullish_aligned")
        elif all_bearish:
            boost *= 1.03
            consensus_tags.append("all_bearish_aligned")

    # ── 4. 严重分歧惩罚 ──
    if stance_summary:
        has_strong_support = any(s.get("stance") == "strong_support" for s in stance_summary)
        has_veto = any(s.get("stance") == "veto" for s in stance_summary)
        if has_strong_support and has_veto:
            boost *= 0.88
            consensus_tags.append("high_controversy")

    return boost, consensus_tags


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
    return weighted_total / max(weight_sum, 1.0)


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
) -> dict[str, Any]:
    """聚合所有维度分数，产生最终推荐卡片。

    参数签名保持向后兼容：
        agent_review_score 作为 Skill Agent 评分的基线参与计算。
    """
    if not signals:
        return _empty(stock, "无策略信号")

    # --- 硬否决：检查 hard_veto 标记（含 stance=veto 的 signal） ---
    hard_vetoes: list[dict[str, Any]] = []
    for signal in signals:
        if signal.get("hard_veto"):
            hard_vetoes.append(signal)
            continue
        review = signal.get("agent_skill_review")
        if isinstance(review, dict):
            stance = review.get("stance", "")
            if STANCE_MULTIPLIER.get(stance) == "hard_veto":
                hard_vetoes.append(signal)
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
            "consensus_score": 0.0,
        }

    # --- 各维度评分 ---
    # Skill Agent 核心评分：从 agent_skill_review.stance 提取信号进行加权
    skill_agent_score = _extract_skill_agent_score(signals)
    # Strategy.yaml 规则辅助评分
    strategy_rule_score = _compute_strategy_rule_score(signals)
    # 风险调整分：风险越多扣分越重，风险模板放大惩罚
    risk_penalty = len(_collect_risks(signals)) * 5.0 * RISK_TEMPLATE_PENALTY.get(risk_template, 1.0)
    risk_adjustment_score = max(0.0, 100.0 - min(45.0, risk_penalty))
    # 共识分（保留旧机制用于信息展示，但不再单独加权）
    consensus_score = _consensus_score(signals)
    # 证据质量
    evidence_quality_score = float(stock.get("evidence_quality_score") or 60.0)
    quality_cap = _quality_score_cap(stock)

    # --- 加权聚合 ---
    weights = dict(DEFAULT_AGG_WEIGHTS)
    if calibrated_weights:
        weights.update(calibrated_weights)

    final_score = (
        weights.get("skill_agent", 0.50) * skill_agent_score
        + weights.get("strategy_rule", 0.25) * strategy_rule_score
        + weights.get("market", 0.10) * market_score
        + weights.get("risk", 0.10) * risk_adjustment_score
        + weights.get("evidence", 0.05) * evidence_quality_score
    )

    # --- 提取 stance 摘要 ---
    stance_summary = _stance_summary(signals)

    # --- 共识加权：多 Skill 一致高分/联合否决/方向对齐 → 加成或惩罚 ---
    consensus_boost, consensus_tags = _consensus_boost(signals, stance_summary)
    final_score *= consensus_boost

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

    # 真实性严重不足 + SYNTHETIC 主导 → 直接降 bucket
    authenticity_bucket_downgrade = False
    if authenticity < 0.5 and synthetic_pct > 0.5:
        authenticity_bucket_downgrade = True

    final_score = min(final_score, quality_cap)

    # --- 组件分 ---
    component_scores = {
        "skill_agent": round(skill_agent_score, 2),
        "strategy_rule": round(strategy_rule_score, 2),
        "market": round(market_score, 2),
        "risk": round(risk_adjustment_score, 2),
        "evidence": round(evidence_quality_score, 2),
    }

    # --- Bucket 分桶 ---
    preferred = [
        signal.get("bucket_preference")
        for signal in signals
        if signal.get("bucket_preference")
    ]
    if authenticity_bucket_downgrade:
        bucket = "证据不足"
    elif preferred:
        bucket = Counter(preferred).most_common(1)[0][0]
    else:
        bucket = _bucket_for_score(final_score)

    if final_score < 58:
        bucket = "证据不足"
    if final_score < 82 and bucket == "容量核心":
        bucket = _bucket_for_score(final_score)

    quality_risks = _quality_risks(stock, quality_cap)

    # --- 最佳信号 ---
    best_setup = max(signals, key=lambda signal: float(signal.get("score", 0)))

    return {
        "code": stock["code"],
        "name": stock["name"],
        "industry": stock.get("industry"),
        "recommend_price": stock.get("price", stock.get("close", 0)),
        "bucket": bucket,
        "final_score": round(final_score, 2),
        "consensus_score": round(consensus_score, 2),
        "setup_type": best_setup.get("setup_match") or "",
        "entry_rule": best_setup.get("entry_rule") or "等待买点确认后再参与",
        "invalid_rule": best_setup.get("invalid_rule") or "跌破关键支撑且收不回",
        "rationale": _rationale(signals),
        "risk_flags": list(dict.fromkeys(_collect_risks(signals) + quality_risks)),
        "strategy_signals": signals,
        "component_scores": component_scores,
        "data_authenticity": authenticity,
        "consensus_boost": round(consensus_boost, 4),
        "consensus_tags": consensus_tags,
        "stance_summary": stance_summary,
        "authenticity_bucket_downgrade": authenticity_bucket_downgrade,
    }


def apply_pool_rules(
    cards: list[dict[str, Any]],
    bucket_limits: dict[str, int],
    output_count: int,
) -> list[dict[str, Any]]:
    """Pool-level bucket rules: enforce per-bucket output limits.

    按 final_score 降序排列，每桶不超过 bucket_limits 限制，
    总输出不超过 output_count。
    """
    counts: Counter[str] = Counter()
    ordered = sorted(
        cards,
        key=lambda card: float(card.get("final_score", 0)),
        reverse=True,
    )
    result: list[dict[str, Any]] = []
    for card in ordered:
        bucket = card.get("bucket", "证据不足")
        limit = bucket_limits.get(bucket) if bucket_limits else None
        if limit is not None and counts[bucket] >= limit:
            continue
        counts[bucket] += 1
        result.append(card)
        if len(result) >= output_count:
            break
    return result


# ============================================================================
# 内部辅助函数
# ============================================================================

def _empty(stock: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "code": stock.get("code"),
        "name": stock.get("name"),
        "industry": stock.get("industry"),
        "recommend_price": stock.get("price", stock.get("close", 0)),
        "bucket": "明确回避",
        "final_score": 0.0,
        "consensus_score": 0.0,
        "setup_type": "",
        "entry_rule": "",
        "invalid_rule": reason,
        "rationale": reason,
        "risk_flags": [reason],
        "strategy_signals": [],
    }


def _bucket_for_score(score: float) -> str:
    if score >= 84:
        return "容量核心"
    if score >= 72:
        return "高弹观察"
    if score <= 30:
        return "明确回避"
    return "证据不足"


def _collect_risks(signals: list[dict[str, Any]]) -> list[str]:
    risks: list[str] = []
    for signal in signals:
        risks.extend(signal.get("risk_flags", []) or [])
    return list(dict.fromkeys(risks))


def _rationale(signals: list[dict[str, Any]]) -> str:
    evidence: list[str] = []
    for signal in signals:
        review = signal.get("agent_skill_review")
        if isinstance(review, dict):
            summary = review.get("summary")
            if summary:
                evidence.append(str(summary))
        evidence.extend(signal.get("evidence", []) or [])
    return "；".join(list(dict.fromkeys(evidence))[:5]) or "策略信号不足，维持观察。"


def _consensus_score(signals: list[dict[str, Any]]) -> float:
    """计算共识得分（信息维度，不再参与加权，仅用于展示）。"""
    positive = sum(1 for signal in signals if float(signal.get("score", 0)) >= 70)
    total = max(1, len(signals))
    if total == 1:
        return 65.0
    ratio = positive / total
    return min(100.0, 40.0 + ratio * 60.0)


def _quality_score_cap(stock: dict[str, Any]) -> float:
    cap = 100.0
    sample_count = int(stock.get("market_sample_count") or 0)
    data_completeness = float(stock.get("data_completeness_score") or 100.0)
    evidence_quality = float(stock.get("evidence_quality_score") or 100.0)
    missing_fields = set((stock.get("data_quality") or {}).get("missing_fields") or [])
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


def _quality_risks(stock: dict[str, Any], quality_cap: float) -> list[str]:
    if quality_cap >= 100:
        return []
    sample_count = int(stock.get("market_sample_count") or 0)
    if sample_count and sample_count < 100:
        return [f"市场样本仅{sample_count}只，评分已按覆盖率降权"]
    return ["证据质量不足，评分已降权"]


def _stance_summary(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """提取所有 Skill Agent 的 stance 摘要。"""
    summary = []
    for signal in signals:
        review = signal.get("agent_skill_review")
        if not isinstance(review, dict):
            continue
        summary.append({
            "skill_name": signal.get("strategy_name", signal.get("skill_name", "")),
            "stance": review.get("stance", "neutral"),
            "score": float(signal.get("score", 0)),
            "confidence": float(review.get("confidence", 0)),
        })
    return summary
