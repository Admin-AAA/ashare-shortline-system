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


def market_regime_weights(snapshot: dict[str, Any] | None = None) -> dict[str, float]:
    """根据市场阶段动态调整聚合权重。

    强牛市场 → 信任 LLM 判断，降低风险和证据权重；
    弱势市场 → 信任规则引擎，提高风险和证据权重。
    """
    if not snapshot:
        return dict(DEFAULT_AGG_WEIGHTS)

    weights = dict(DEFAULT_AGG_WEIGHTS)
    sample = max(1, int(snapshot.get("sample_count") or 1))
    up_ratio = float(snapshot.get("up_count") or 0) / sample
    # 从快照数据直接推断市场情绪阶段
    limit_up = int(snapshot.get("limit_up_count") or 0)
    limit_down = int(snapshot.get("limit_down_count") or 0)
    if limit_up > 50 and (limit_down == 0 or limit_up / max(1, limit_down) > 3):
        sentiment = "高潮"
    elif limit_up > 20:
        sentiment = "活跃"
    elif limit_up >= 5:
        sentiment = "平淡"
    else:
        sentiment = "冰点"

    if up_ratio >= 0.65:
        # 强牛：LLM 判断更可靠
        weights["skill_agent"] = 0.55
        weights["risk"] = 0.05
        weights["evidence"] = 0.05
        weights["strategy_rule"] = 0.25
    elif up_ratio >= 0.50:
        # 偏多：略微倾向 LLM
        weights["skill_agent"] = 0.52
        weights["risk"] = 0.08
        weights["evidence"] = 0.05
        weights["strategy_rule"] = 0.25
    elif up_ratio >= 0.35:
        # 中性/震荡：均衡，略微提高证据权重
        weights["skill_agent"] = 0.50
        weights["strategy_rule"] = 0.25
        weights["risk"] = 0.10
        weights["evidence"] = 0.05
    else:
        # 弱势：规则引擎更可靠，风险权重提高
        weights["skill_agent"] = 0.40
        weights["strategy_rule"] = 0.30
        weights["risk"] = 0.12
        weights["evidence"] = 0.08

    # 市场情绪微调
    if sentiment == "高潮":
        weights["skill_agent"] = min(0.58, weights["skill_agent"] + 0.03)
        weights["risk"] = max(0.03, weights["risk"] - 0.02)
    elif sentiment == "冰点":
        weights["skill_agent"] = max(0.35, weights["skill_agent"] - 0.05)
        weights["risk"] = min(0.18, weights["risk"] + 0.05)
        weights["strategy_rule"] = min(0.35, weights["strategy_rule"] + 0.03)

    # 归一化
    total = sum(weights.values())
    return {k: round(v / total, 4) for k, v in weights.items()}


def skill_disagreement_metrics(signals: list[dict[str, Any]]) -> dict[str, Any]:
    """计算多 Skill 之间的分歧度量。

    返回：
    - disagreement_level: "一致" / "轻微分歧" / "明显分歧" / "严重分歧"
    - score_variance: 各 Skill 评分的方差
    - stance_conflict: 是否存在 stance 冲突（多空都有）
    - stance_distribution: stance 分布计数
    """
    if len(signals) < 2:
        return {
            "disagreement_level": "一致",
            "score_variance": 0.0,
            "stance_conflict": False,
            "stance_distribution": {},
        }

    scores = [float(s.get("score", 0)) for s in signals]
    mean_score = sum(scores) / len(scores)
    variance = sum((s - mean_score) ** 2 for s in scores) / len(scores)

    stances: dict[str, int] = {}
    for signal in signals:
        review = signal.get("agent_skill_review")
        if isinstance(review, dict):
            stance = review.get("stance", "neutral")
            stances[stance] = stances.get(stance, 0) + 1

    bullish = {"strong_support", "support", "neutral_support"}
    bearish = {"oppose", "veto"}
    has_bullish = any(s in bullish for s in stances)
    has_bearish = any(s in bearish for s in stances)
    stance_conflict = has_bullish and has_bearish

    if variance < 20 and not stance_conflict:
        level = "一致"
    elif variance < 60 and not stance_conflict:
        level = "轻微分歧"
    elif variance < 120 or (stance_conflict and variance < 80):
        level = "明显分歧"
    else:
        level = "严重分歧"

    return {
        "disagreement_level": level,
        "score_variance": round(variance, 2),
        "stance_conflict": stance_conflict,
        "stance_distribution": stances,
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

    # --- 检测是否无 Agent 审查（回测/纯规则模式）---
    has_agent_reviews = any(
        isinstance(s.get("agent_skill_review"), dict) for s in signals
    )
    agent_review_count = sum(
        1 for s in signals
        if isinstance(s.get("agent_skill_review"), dict)
    )

    # --- 加权聚合（动态权重 + 校准权重覆盖） ---
    if market_snapshot:
        weights = market_regime_weights(market_snapshot)
    else:
        weights = dict(DEFAULT_AGG_WEIGHTS)
    if calibrated_weights:
        weights.update(calibrated_weights)

    # 无 Agent 审查时重平衡：策略规则为主 + 扩大分数分布映射
    if not has_agent_reviews:
        weights["skill_agent"] = 0.00
        weights["strategy_rule"] = 0.55
        weights["market"] = 0.20
        weights["risk"] = 0.15
        weights["evidence"] = 0.10
        # 策略分数扩大映射：将 20-80 范围映射到 0-100
        strategy_rule_score = max(0.0, min(100.0, (strategy_rule_score - 20) / 60 * 100))

    # 归一化
    total_w = sum(weights.values())
    weights = {k: v / total_w for k, v in weights.items()}

    raw_component_scores = {
        "skill_agent": round(skill_agent_score, 2),
        "strategy_rule": round(strategy_rule_score, 2),
        "market": round(market_score, 2),
        "risk": round(risk_adjustment_score, 2),
        "evidence": round(evidence_quality_score, 2),
    }

    weighted_base_score = (
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

    # 真实性严重不足 + SYNTHETIC 主导 → 直接降 bucket
    authenticity_bucket_downgrade = False
    if authenticity < 0.5 and synthetic_pct > 0.5:
        authenticity_bucket_downgrade = True

    final_score = min(final_score, quality_cap)
    component_scale = final_score / max(weighted_base_score, 1e-6)
    component_scores = {
        key: round(value * component_scale, 2)
        for key, value in raw_component_scores.items()
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
        top = Counter(preferred).most_common(1)[0][0]
        # 信号级别的 "证据不足" 不作为覆盖，让聚合分数来决定
        if top != "证据不足":
            bucket = top
        else:
            bucket = _bucket_for_score(final_score)
    else:
        bucket = _bucket_for_score(final_score)

    if final_score < 45:
        bucket = "证据不足"
    if final_score < 78 and bucket == "容量核心":
        bucket = _bucket_for_score(final_score)

    quality_risks = _quality_risks(stock, quality_cap)
    conflict_risks = _detect_skill_conflicts(signals)
    quality_risks = list(dict.fromkeys(quality_risks + conflict_risks))

    # --- 最佳信号 ---
    best_setup = max(signals, key=lambda signal: float(signal.get("score", 0)))

    # ── 证据质量降级：分数足够但证据不足时降一档 ──
    evidence_score = float(stock.get("evidence_quality_score") or 60.0)
    if evidence_score < 60 and agent_review_count < 2 and bucket == "高弹观察":
        bucket = "关注"
    if evidence_score < 55 and agent_review_count < 2 and bucket == "关注":
        bucket = "证据不足"

    market_discipline = _market_discipline(
        stock=stock,
        market_snapshot=market_snapshot,
        final_score=final_score,
        bucket=bucket,
        risk_template=risk_template,
        agent_review_count=agent_review_count,
    )
    final_risk_flags = list(
        dict.fromkeys(_collect_risks(signals) + quality_risks + market_discipline["risk_flags"])
    )

    return {
        "code": stock["code"],
        "name": stock["name"],
        "industry": stock.get("industry"),
        "recommend_price": stock.get("price", stock.get("close", 0)),
        "stop_loss": stock.get("stop_loss"),
        "take_profit": stock.get("take_profit"),
        "bucket": bucket,
        "final_score": round(final_score, 2),
        "consensus_score": round(consensus_score, 2),
        "setup_type": best_setup.get("setup_match") or "",
        "entry_rule": best_setup.get("entry_rule") or "等待买点确认后再参与",
        "invalid_rule": best_setup.get("invalid_rule") or "跌破关键支撑且收不回",
        "rationale": _rationale(signals),
        "risk_flags": final_risk_flags,
        "strategy_signals": signals,
        "component_scores": component_scores,
        "raw_component_scores": raw_component_scores,
        "data_authenticity": authenticity,
        "consensus_boost": round(consensus_boost, 4),
        "consensus_tags": consensus_tags,
        "stance_summary": stance_summary,
        "authenticity_bucket_downgrade": authenticity_bucket_downgrade,
        "aggregation_weights": weights,
        "component_scale": round(component_scale, 4),
        "market_regime": _market_regime_label(market_snapshot),
        "skill_disagreement": skill_disagreement_metrics(signals),
        "market_discipline": market_discipline,
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
        if card.get("bucket") == "明确回避" or float(card.get("final_score") or 0) <= 0:
            continue
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
        "stop_loss": stock.get("stop_loss"),
        "take_profit": stock.get("take_profit"),
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
    if score >= 80:
        return "容量核心"
    if score >= 64:
        return "高弹观察"
    if score >= 50:
        return "关注"
    if score <= 28:
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
    risks: list[str] = []
    sample_count = int(stock.get("market_sample_count") or 0)
    if quality_cap < 100:
        if sample_count and sample_count < 100:
            risks.append(f"市场样本仅{sample_count}只，评分已按覆盖率降权")
        else:
            risks.append("证据质量不足，评分已降权")

    data_quality = stock.get("data_quality") or {}
    missing_fields = set(data_quality.get("missing_fields") or [])
    if {"news", "announcements"} & missing_fields:
        risks.append("新闻/公告未完整覆盖，题材与黑天鹅风险需人工复核")
    if "theme_tags" in missing_fields:
        risks.append("题材标签缺失，主线识别置信度降低")
    if "dragon_tiger" in missing_fields:
        risks.append("龙虎榜数据缺失，游资合力证据不足")
    if "quote_conflict" in missing_fields:
        risks.append("多源报价存在冲突，价格证据需复核")
    return list(dict.fromkeys(risks))


def _detect_skill_conflicts(signals: list[dict[str, Any]]) -> list[str]:
    """检测跨 Skill 矛盾：阶段分歧、趋势判断分歧、setup 冲突。

    当两个 Skill 对同一维度给出相反结论时，标记为风险。
    """
    risks: list[str] = []
    if len(signals) < 2:
        return risks

    # 1. 阶段判定矛盾（如 启动 vs 加速末期）
    phases = [
        s.get("phase", "") for s in signals
        if s.get("phase") and s.get("phase") != "未分类"
    ]
    bullish_phases = {"启动", "发酵", "主升", "分歧转一致", "趋势跟踪"}
    bearish_phases = {"加速末期", "高潮", "退潮", "冰点", "过热观察"}
    has_bullish_phase = any(p in bullish_phases for p in phases)
    has_bearish_phase = any(p in bearish_phases for p in phases)
    if has_bullish_phase and has_bearish_phase:
        risks.append(f"Skill 阶段判定矛盾: {', '.join(phases[:4])}")

    # 2. setup 冲突：有 Skill 匹配 setup 而其他未匹配
    setups = [s.get("setup_match", "") for s in signals if s.get("setup_match")]
    if setups and len(setups) < len(signals):
        non_setup_skills = [
            s.get("strategy_name", "") for s in signals
            if not s.get("setup_match")
        ]
        if non_setup_skills:
            risks.append(f"Setup 仅部分 Skill 命中，未命中: {', '.join(non_setup_skills[:2])}")

    # 3. stance 多空矛盾
    stances = []
    for s in signals:
        review = s.get("agent_skill_review")
        if isinstance(review, dict):
            stances.append(review.get("stance", "neutral"))
    bullish = {"strong_support", "support"}
    bearish = {"oppose", "veto"}
    if any(s in bullish for s in stances) and any(s in bearish for s in stances):
        risks.append("Skill Agent 立场多空矛盾，置信度降低")

    return risks


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


def _market_regime_label(snapshot: dict[str, Any] | None) -> str:
    """从快照中提取市场阶段标签。"""
    if not snapshot:
        return "未知"
    sample = max(1, int(snapshot.get("sample_count") or 1))
    up_ratio = float(snapshot.get("up_count") or 0) / sample
    limit_up = int(snapshot.get("limit_up_count") or 0)
    limit_down = int(snapshot.get("limit_down_count") or 0)
    if limit_up > 50 and (limit_down == 0 or limit_up / max(1, limit_down) > 3):
        sentiment = "高潮"
    elif limit_up > 20:
        sentiment = "活跃"
    elif limit_up >= 5:
        sentiment = "平淡"
    else:
        sentiment = "冰点"
    if up_ratio >= 0.65:
        base = "强牛"
    elif up_ratio >= 0.50:
        base = "偏多"
    elif up_ratio >= 0.35:
        base = "震荡"
    else:
        base = "弱势"
    if sentiment and sentiment not in ("", "平淡"):
        return f"{base}·{sentiment}"
    return base


def _market_discipline(
    stock: dict[str, Any],
    market_snapshot: dict[str, Any] | None,
    final_score: float,
    bucket: str,
    risk_template: str,
    agent_review_count: int,
) -> dict[str, Any]:
    """Translate the score into short-line position and risk discipline."""
    label = _market_regime_label(market_snapshot)
    has_snapshot = bool(market_snapshot)
    sample = max(1, int((market_snapshot or {}).get("sample_count") or stock.get("market_sample_count") or 1))
    up_ratio = float((market_snapshot or {}).get("up_count") or 0) / sample if has_snapshot else 0.5
    limit_up = int((market_snapshot or {}).get("limit_up_count") or 0)
    limit_down = int((market_snapshot or {}).get("limit_down_count") or 0)
    risk_flags: list[str] = []

    if not has_snapshot:
        market_position_cap = 0.35
        regime_note = "市场快照缺失，按试错区间"
        risk_flags.append("市场快照缺失，仓位按试错处理")
    elif label.startswith("强牛") or label.startswith("偏多"):
        market_position_cap = 0.80
        regime_note = "可操作区间"
    elif label.startswith("震荡"):
        market_position_cap = 0.50
        regime_note = "试错区间"
    else:
        market_position_cap = 0.30
        regime_note = "防守区间"
        risk_flags.append("市场弱势，按防守仓位处理")

    if "冰点" in label:
        market_position_cap = min(market_position_cap, 0.20)
        risk_flags.append("情绪冰点，非主线非龙头不放大仓位")
    if "高潮" in label:
        market_position_cap = min(market_position_cap, 0.65)
        risk_flags.append("情绪高潮，追高仓位降档")
    if limit_down >= max(5, limit_up):
        market_position_cap = min(market_position_cap, 0.20)
        risk_flags.append("跌停压力不低于涨停，先控制回撤")

    win_rate = _estimated_win_rate(stock, final_score, bucket, agent_review_count, up_ratio)
    if win_rate < 0.60:
        base_position = 0.0
        position_label = "观望"
    elif win_rate < 0.70:
        base_position = 0.15
        position_label = "观察仓"
    elif win_rate < 0.80:
        base_position = 0.35
        position_label = "小仓"
    elif win_rate < 0.90:
        base_position = 0.55
        position_label = "中仓"
    else:
        base_position = 0.75
        position_label = "大仓"

    if bucket in {"证据不足", "明确回避"}:
        base_position = 0.0
        position_label = "观望"
    elif bucket == "关注":
        base_position = min(base_position, 0.20)
        position_label = "观察仓" if base_position > 0 else "观望"

    template_cap = {"conservative": 0.45, "balanced": 0.65, "aggressive": 0.80}.get(risk_template, 0.65)
    max_position_pct = round(min(base_position, market_position_cap, template_cap) * 100, 1)

    if max_position_pct == 0:
        action = "仅观察"
    elif max_position_pct <= 20:
        action = "轻仓试错"
    elif max_position_pct <= 45:
        action = "小仓跟踪"
    else:
        action = "确认后加仓"

    price = _float(stock.get("price") or stock.get("close") or 0)
    stop_loss = _float(stock.get("stop_loss"))
    take_profit = _float(stock.get("take_profit"))
    downside_pct = round((price - stop_loss) / price * 100, 2) if price and stop_loss else 0.0
    upside_pct = round((take_profit - price) / price * 100, 2) if price and take_profit else 0.0
    rr_ratio = round(upside_pct / max(downside_pct, 0.01), 2) if upside_pct > 0 and downside_pct > 0 else 0.0
    if rr_ratio and rr_ratio < 1.0:
        risk_flags.append("盈亏比低于1:1，除非次日快速确认否则不追")
    if agent_review_count == 0:
        risk_flags.append("未进入 Skill Agent 深审，仅按规则信号降权输出")

    return {
        "market_regime": label,
        "regime_note": regime_note,
        "estimated_win_rate": round(win_rate, 3),
        "position_label": position_label,
        "max_position_pct": max_position_pct,
        "action": action,
        "market_position_cap_pct": round(market_position_cap * 100, 1),
        "risk_reward": {
            "downside_pct": downside_pct,
            "upside_pct": upside_pct,
            "rr_ratio": rr_ratio,
        },
        "risk_flags": risk_flags,
    }


def _estimated_win_rate(
    stock: dict[str, Any],
    final_score: float,
    bucket: str,
    agent_review_count: int,
    up_ratio: float,
) -> float:
    bucket_adjust = {
        "容量核心": 0.08,
        "高弹观察": 0.03,
        "关注": -0.04,
        "证据不足": -0.12,
        "明确回避": -0.25,
    }.get(bucket, -0.08)
    evidence = float(stock.get("evidence_quality_score") or 60.0)
    authenticity = float(stock.get("data_authenticity", 1.0))
    risk_score = float(stock.get("risk_score") or 0.0)
    win_rate = 0.35 + final_score / 200.0 + bucket_adjust
    win_rate += (evidence - 60.0) / 500.0
    win_rate += (authenticity - 0.75) * 0.08
    win_rate += min(0.06, max(-0.08, (up_ratio - 0.5) * 0.18))
    win_rate += min(0.04, agent_review_count * 0.015)
    win_rate -= min(0.12, risk_score / 700.0)
    return max(0.15, min(0.93, win_rate))


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return default
