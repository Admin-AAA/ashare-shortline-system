from typing import Any

# 保持旧接口兼容：从 agent_skill_runner 导入 enrich_signal_with_agent_review
# 调用方可以通过此模块直接访问该函数
from app.engine.agent_skill_runner import enrich_signal_with_agent_review  # noqa: F401
from app.services.execution_constraints import (
    entry_no_liquidity_reason_from_card as _entry_no_liquidity_reason,
    is_one_word_limit_up_card as _is_one_word_limit_up_card,
)

RISK_TEMPLATE_PENALTY = {
    "conservative": 1.4,
    "balanced": 1.0,
    "aggressive": 0.7,
}

# ── 风险分级：按严重程度差异化扣分 ──
# high: 真实交易风险，需重点对待
# medium: 市场环境/情绪类风险
# info: 数据覆盖不足，仅展示不扣分
RISK_SEVERITY_PENALTY = {"high": 8.0, "medium": 5.0, "info": 0.0}

# 关键词 → 严重级别映射（越具体越靠前）
_RISK_SEVERITY_RULES: list[tuple[list[str], str]] = [
    # ── high: 交易面硬风险 ──
    (["乖离率偏高", "获利回吐", "bias_ma"], "high"),
    (["近期已有较大涨幅", "涨幅过大", "追高风险"], "high"),
    (["爆量", "分歧", "高换手分歧"], "high"),
    (["跌破关键支撑", "破位", "跌穿"], "high"),
    ([
        "跟风补涨",
        "跟风杂毛",
        "非龙头股",
        "非板块龙头",
        "非短线情绪龙头",
        "非绝对龙头",
        "无连续涨停带动效应",
        "非板块涨停核心",
        "缺乏板块带动效应",
        "无带队效应",
        "跟风套利风险",
        "纯技术面跟随",
        "缺乏硬逻辑支撑",
        "硬逻辑不足",
        "股票非龙头",
        "主线跟风",
        "更可能属于跟风或趋势中军",
        "板块带动效应存疑",
        "不符合龙头战法的核心要求",
        "不符合龙头战法核心要求",
    ], "high"),
    (["主力资金净流入-", "主力资金大幅流出", "持续流出"], "high"),
    (["盈亏比低于", "盈亏比不达标"], "high"),
    (["股权质押比例", "质押风险"], "high"),
    (["有限售股解禁", "解禁"], "high"),
    (["硬风控否决", "Hard Veto"], "high"),
    (["20cm 风险闸门", "20cm 隔夜波动"], "high"),
    (["入场不可成交", "无法真实成交", "无成交量", "一字涨停"], "high"),
    (["涨停排队", "封板反复", "封单金额", "尾盘封板"], "high"),
    (["未进入 Skill Agent 深审", "仅按规则信号降权输出"], "info"),
    # ── medium: 市场环境/情绪 ──
    (["情绪高潮", "高潮阶段", "追高仓位降档", "涨停过热"], "medium"),
    (["市场弱势", "防守仓位", "情绪冰点", "跌停压力"], "medium"),
    (["Skill 阶段判定矛盾", "立场多空矛盾"], "medium"),
    (["Setup 仅部分 Skill 命中"], "medium"),
    (["概念热度排名", "非绝对主线"], "medium"),
    (["量能中性", "缺乏.*信号", "方向不明"], "medium"),
    (["股价未站上MA", "短线支撑未确认"], "medium"),
    (["市场快照缺失"], "medium"),
    (["数据缺失", "关键数据缺失", "数据完整性不足", "证据覆盖不足"], "info"),
    (["Skill Agent 审查暂不可用", "Skill Agent 直接审查失败", "Agent 审查暂不可用"], "info"),
    # ── info: 数据覆盖/完整性（不扣分） ──
    (["样本或证据覆盖不足", "数据完整度", "证据质量不足"], "info"),
    (["龙虎榜数据缺失", "无龙虎榜数据", "新闻/公告未完整覆盖", "题材标签缺失", "多源报价存在冲突"], "info"),
    (["样本仅", "评分已按覆盖率降权", "评分已降权"], "info"),
    (["Agent 加分受限"], "info"),
    (["数据为估计值", "可靠性稍低"], "info"),
]


def _risk_severity(flag: str) -> str:
    """根据风险文本判断严重级别。"""
    import re
    for keywords, severity in _RISK_SEVERITY_RULES:
        for kw in keywords:
            if kw in flag or re.search(kw, flag):
                return severity
    return "medium"  # 默认中等

# ---------------------------------------------------------------------------
# 权重体系：Skill Agent 成为核心评分来源
# ---------------------------------------------------------------------------
DEFAULT_AGG_WEIGHTS = {
    "skill_agent": 0.55,
    "strategy_rule": 0.15,
    "market": 0.10,
    "risk": 0.10,
    "evidence": 0.10,
}

AGG_WEIGHT_KEYS = list(DEFAULT_AGG_WEIGHTS.keys())
COMPONENT_KEYS = list(DEFAULT_AGG_WEIGHTS.keys())

# 有 Agent 审查时的权重（strategy_rule 降为辅证，skill_agent 主导）
AGENT_PRESENT_WEIGHTS = {
    "skill_agent": 0.55,
    "strategy_rule": 0.15,
    "market": 0.10,
    "risk": 0.10,
    "evidence": 0.10,
}

# --- 市场阶段阈值（涨停率 / 上涨比） ---
# 基于历史数据回测：高潮胜率 62.2% vs 活跃 55.1%（Δ=+7.2pp, t=3.17）
SENTIMENT_LIMIT_UP_RATE_HOT = 0.012    # 涨停率 > 1.2% → 高潮
SENTIMENT_LIMIT_UP_RATE_ACTIVE = 0.005  # 涨停率 > 0.5% → 活跃
SENTIMENT_LIMIT_UP_RATE_FLAT = 0.001    # 涨停率 >= 0.1% → 平淡
SENTIMENT_LIMIT_DOWN_RATIO = 3          # 涨停/跌停比 > 3:1 才确认高潮
BASE_UP_RATIO_BULL = 0.65               # 上涨比 >= 65% → 强牛
BASE_UP_RATIO_BULLISH = 0.50            # 上涨比 >= 50% → 偏多
BASE_UP_RATIO_RANGE = 0.35              # 上涨比 >= 35% → 震荡


def _market_sentiment(snapshot: dict[str, Any] | None) -> str:
    """统一的市场情绪判断，全系统唯一入口。"""
    if not snapshot:
        return "平淡"
    sample = max(1, int(snapshot.get("sample_count") or 1))
    limit_up = int(snapshot.get("limit_up_count") or 0)
    limit_down = int(snapshot.get("limit_down_count") or 0)
    # 用涨停率而非绝对数，适配不同市场规模
    lu_rate = limit_up / sample
    ld_rate = limit_down / sample
    if lu_rate > SENTIMENT_LIMIT_UP_RATE_HOT and (
        ld_rate == 0 or lu_rate / max(ld_rate, 0.0001) > SENTIMENT_LIMIT_DOWN_RATIO
    ):
        return "高潮"
    if lu_rate > SENTIMENT_LIMIT_UP_RATE_ACTIVE:
        return "活跃"
    if lu_rate >= SENTIMENT_LIMIT_UP_RATE_FLAT:
        return "平淡"
    return "冰点"


def _market_base(snapshot: dict[str, Any] | None) -> str:
    """统一的市场方向判断，全系统唯一入口。"""
    if not snapshot:
        return "震荡"
    sample = max(1, int(snapshot.get("sample_count") or 1))
    up_ratio = float(snapshot.get("up_count") or 0) / sample
    if up_ratio >= BASE_UP_RATIO_BULL:
        return "强牛"
    if up_ratio >= BASE_UP_RATIO_BULLISH:
        return "偏多"
    if up_ratio >= BASE_UP_RATIO_RANGE:
        return "震荡"
    return "弱势"


def market_regime_weights(
    snapshot: dict[str, Any] | None = None,
    calibrated: dict[str, float] | None = None,
) -> dict[str, float]:
    """根据市场阶段动态调整聚合权重。

    使用校准权重（如果提供）作为基础，而非固定的默认权重。
    这确保校准的 risk=43% 等权重在阶段调整中生效。
    """
    if not snapshot:
        return dict(calibrated or DEFAULT_AGG_WEIGHTS)

    weights = dict(calibrated or DEFAULT_AGG_WEIGHTS)
    base = _market_base(snapshot)
    sentiment = _market_sentiment(snapshot)

    if base == "强牛":
        weights["skill_agent"] = 0.60
        weights["risk"] = 0.05
        weights["evidence"] = 0.08
        weights["strategy_rule"] = 0.12
    elif base == "偏多":
        weights["skill_agent"] = 0.57
        weights["risk"] = 0.08
        weights["evidence"] = 0.08
        weights["strategy_rule"] = 0.12
    elif base == "震荡":
        weights["skill_agent"] = 0.55
        weights["strategy_rule"] = 0.12
        weights["risk"] = 0.10
        weights["evidence"] = 0.08
    else:
        weights["skill_agent"] = 0.45
        weights["strategy_rule"] = 0.18
        weights["risk"] = 0.12
        weights["evidence"] = 0.10

    if sentiment == "高潮":
        weights["skill_agent"] = min(0.58, weights["skill_agent"] + 0.03)
        weights["risk"] = max(0.03, weights["risk"] - 0.02)
    elif sentiment == "冰点":
        weights["skill_agent"] = max(0.35, weights["skill_agent"] - 0.05)
        weights["risk"] = min(0.18, weights["risk"] + 0.05)
        weights["strategy_rule"] = min(0.35, weights["strategy_rule"] + 0.03)

    total = sum(weights.values())
    return {k: round(v / total, 4) for k, v in weights.items()}


def resolve_score_line_context(
    base_score_line: float,
    market_snapshot: dict[str, Any] | None = None,
    risk_template: str = "balanced",
) -> dict[str, Any]:
    base = float(base_score_line or 0)
    market_base = _market_base(market_snapshot)
    sentiment = _market_sentiment(market_snapshot)
    market_adjustment = {
        "强牛": 1.0,
        "偏多": 0.0,
        "震荡": 0.0,
        "弱势": 2.0,
    }.get(market_base, 0.0)
    sentiment_adjustment = {
        "高潮": 5.0,
        "活跃": 1.0,
        "平淡": 0.0,
        "冰点": -3.0 if market_base == "弱势" else 0.0,
    }.get(sentiment, 0.0)
    risk_adjustment = {
        "conservative": 2.0,
        "balanced": 0.0,
        "aggressive": -1.0,
    }.get(str(risk_template or "balanced"), 0.0)
    effective = round(max(35.0, min(95.0, base + market_adjustment + sentiment_adjustment + risk_adjustment)), 2)
    return {
        "base_score_line": round(base, 2),
        "market_base": market_base,
        "market_sentiment": sentiment,
        "market_adjustment": round(market_adjustment + sentiment_adjustment, 2),
        "risk_template_adjustment": round(risk_adjustment, 2),
        "effective_score_line": effective,
    }


def resolve_display_limit(
    output_count: int | None,
    market_snapshot: dict[str, Any] | None = None,
) -> int | None:
    if output_count is None:
        return None
    count = int(output_count or 0)
    if count <= 0:
        return None
    market_base = _market_base(market_snapshot)
    sentiment = _market_sentiment(market_snapshot)
    if sentiment == "高潮":
        return max(1, min(count, int((count + 1) // 2)))
    if market_base == "弱势" and sentiment == "冰点":
        return min(count, 2)
    return None


def _card_score_line_adjustment(card: dict[str, Any]) -> dict[str, Any]:
    evidence_score = float(
        card.get("raw_component_scores", {}).get("evidence")
        or card.get("evidence_quality_score")
        or 0.0
    )
    completeness = float(card.get("data_completeness_score") or 100.0)
    authenticity = float(card.get("data_authenticity") or 1.0)
    missing_fields = set((card.get("data_quality") or {}).get("missing_fields") or [])

    evidence_adjustment = 0.0
    reasons: list[str] = []
    if evidence_score < 70:
        evidence_adjustment += 0.15
        reasons.append("evidence<70")
    if evidence_score < 55:
        evidence_adjustment += 0.2
        reasons.append("evidence<55")
    if completeness < 75:
        evidence_adjustment += 0.15
        reasons.append("completeness<75")
    if completeness < 50:
        evidence_adjustment += 0.2
        reasons.append("completeness<50")
    if authenticity < 0.75:
        evidence_adjustment += 0.2
        reasons.append("authenticity<0.75")
    if authenticity < 0.55:
        evidence_adjustment += 0.5
        reasons.append("authenticity<0.55")
    if {"news", "announcements", "dragon_tiger"} & missing_fields:
        evidence_adjustment += 0.05
        reasons.append("critical_aux_missing")
    strategy_signals = card.get("strategy_signals") or []
    if any(
        isinstance(signal, dict)
        and (
            signal.get("runtime") == "agent_skill_fallback_prelim"
            or signal.get("agent_review_unavailable")
        )
        for signal in strategy_signals
    ):
        evidence_adjustment += 8.5
        reasons.append("agent_unavailable_fallback")
    return {
        "evidence_adjustment": round(evidence_adjustment if evidence_adjustment > 1.0 else min(0.75, evidence_adjustment), 2),
        "reasons": reasons,
    }


def _execution_viability(card: dict[str, Any]) -> dict[str, Any]:
    details = {
        "is_limit_up": bool(card.get("is_limit_up")),
        "open_times": int(_float(card.get("limit_up_open_times"), 0)),
        "first_time": card.get("limit_up_first_time") or card.get("limit_up_seal_time"),
        "last_time": card.get("limit_up_last_time"),
        "fd_amount_yi": _float(card.get("limit_up_fd_amount_yi"), 0.0),
        "turnover_ratio": _float(card.get("limit_up_turnover_ratio"), 0.0),
    }
    no_liquidity_reason = _entry_no_liquidity_reason(card)
    if no_liquidity_reason:
        return _execution_viability_result(
            "blocked",
            "入场不可成交",
            no_liquidity_reason,
            [no_liquidity_reason],
            99.0,
            details,
        )
    if _is_one_word_limit_up_card(card):
        reason = "入场日一字涨停，按真实成交约束视为买不进"
        return _execution_viability_result(
            "blocked",
            "入场不可成交",
            reason,
            [reason],
            99.0,
            details,
        )
    if not bool(card.get("is_limit_up")):
        return _execution_viability_result(
            "tradable",
            "可按普通成交约束观察",
            None,
            [],
            0.0,
            details,
        )

    open_times = details["open_times"]
    fd_amount_yi = details["fd_amount_yi"]
    last_time = _time_int(details["last_time"])
    adjustment = 3.0
    risks = ["当日已涨停，入场存在涨停排队与滑点风险"]
    if open_times >= 10:
        adjustment += 2.0
        risks.append(f"涨停封板反复打开 {open_times} 次，分歧较大")
    elif open_times > 0:
        adjustment += 1.0
        risks.append(f"涨停盘中打开 {open_times} 次，需确认承接")
    else:
        adjustment += 2.0
        risks.append("涨停未记录开板，需确认不是一字排队")
    if 0 < fd_amount_yi < 0.5:
        adjustment += 1.5
        risks.append(f"封单金额仅 {fd_amount_yi:.2f} 亿，封板承接偏弱")
    if last_time and last_time >= 143000:
        adjustment += 1.5
        risks.append("尾盘封板，次日分歧风险较高")
    return _execution_viability_result(
        "risky",
        "涨停执行风险",
        None,
        list(dict.fromkeys(risks)),
        min(adjustment, 8.0),
        details,
    )


def _execution_viability_result(
    status: str,
    label: str,
    block_reason: str | None,
    risk_flags: list[str],
    score_line_adjustment: float,
    details: dict[str, Any],
) -> dict[str, Any]:
    return {
        "status": status,
        "label": label,
        "block_reason": block_reason,
        "risk_flags": risk_flags,
        "score_line_adjustment": round(score_line_adjustment, 2),
        "details": details,
    }


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
        weight = float(signal.get("weight", 1.0))

        multiplier = STANCE_MULTIPLIER.get(stance)
        if multiplier == "hard_veto":
            skill_scores.append((0.0, weight))
            continue

        # 以 Agent 自身评分为主，stance 乘数做方向确认
        agent_score = float(review.get("score", 50))
        agent_confidence = float(review.get("confidence", 0.5))
        # 低置信度时向中性 50 回归
        regressed = 50 + (agent_score - 50) * agent_confidence
        adjusted = regressed * float(multiplier or 1.0)
        skill_scores.append((adjusted, weight * agent_confidence))

    if not skill_scores:
        return 50.0

    weighted_total = sum(s * w for s, w in skill_scores)
    weight_sum = sum(w for _, w in skill_scores)
    return weighted_total / max(weight_sum, 1e-6)


def _skill_independence_factor(signals: list[dict[str, Any]]) -> float:
    """计算技能独立性修正因子。

    当多个技能依赖相同数据源/方法论时，高分一致性加成应衰减，
    避免同一技术特征被重复计数放大噪音。
    """
    skill_ids = [s.get("strategy_id", s.get("strategy_name", "")) for s in signals]
    if len(set(skill_ids)) < 2:
        return 1.0
    indeps: list[float] = []
    for signal in signals:
        try:
            indeps.append(max(0.0, min(1.0, float(signal.get("strategy_independence", 0.85)))))
        except (TypeError, ValueError):
            indeps.append(0.85)
    return sum(indeps) / max(1, len(indeps))


def _consensus_boost(
    signals: list[dict[str, Any]],
    stance_summary: list[dict[str, Any]] | None = None,
) -> tuple[float, list[str]]:
    """共识加权 + 立场协同/冲突机制。

    包含四个维度：
    1. 高分一致性加成（经技能独立性衰减）
    2. 联合否决打击
    3. 一致方向加成
    4. 严重分歧惩罚
    """
    consensus_tags: list[str] = []
    boost = 1.0
    indep_factor = _skill_independence_factor(signals)

    # ── 1. 高分一致性加成（独立性衰减） ──
    agreeing_skills = sum(
        1 for signal in signals
        if float(signal.get("score", 0)) >= 70
    )
    if agreeing_skills >= 2:
        raw_boost = 0.1 * (agreeing_skills - 1)
        boost = 1.0 + raw_boost * indep_factor
        consensus_tags.append("high_agreement")

    # ── 2. 联合否决打击 ──
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
        all_bullish = all(s.get("stance") in bullish_stances for s in stance_summary)
        all_bearish = all(s.get("stance") in bearish_stances for s in stance_summary)
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
            "info_tags": [],
            "consensus_score": 0.0,
        }

    # --- 各维度评分 ---
    # Skill Agent 核心评分：从 agent_skill_review.stance 提取信号进行加权
    skill_agent_score = _extract_skill_agent_score(signals)
    # Strategy.yaml 规则辅助评分
    strategy_rule_score = _compute_strategy_rule_score(signals)
    # 风险调整分：按严重级别分级扣分，info 级不扣分
    shortline_factor_risks = _shortline_factor_risks(stock)
    all_risks = _collect_risks(signals) + shortline_factor_risks
    risk_penalty = sum(
        RISK_SEVERITY_PENALTY.get(_risk_severity(r), 5.0) for r in all_risks
    ) * RISK_TEMPLATE_PENALTY.get(risk_template, 1.0)
    risk_adjustment_score = max(0.0, 100.0 - min(45.0, risk_penalty))
    # 共识分（保留旧机制用于信息展示，但不再单独加权）
    consensus_score = _consensus_score(signals)
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
        isinstance(s.get("agent_skill_review"), dict) for s in signals
    )
    agent_review_count = sum(
        1 for s in signals
        if isinstance(s.get("agent_skill_review"), dict)
    )

    # --- 加权聚合（校准权重为基底 + 阶段动态调整） ---
    if market_snapshot:
        weights = market_regime_weights(market_snapshot, calibrated=calibrated_weights)
    else:
        weights = dict(calibrated_weights or DEFAULT_AGG_WEIGHTS)

    # 有 Agent 审查时 skill_agent 主导，策略规则降为辅证
    if has_agent_reviews:
        weights.update(AGENT_PRESENT_WEIGHTS)
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
    conflict_risks = _detect_skill_conflicts(signals)

    # --- 最佳信号 ---
    best_setup = max(signals, key=lambda signal: float(signal.get("score", 0)))

    market_discipline = _market_discipline(
        stock=stock,
        market_snapshot=market_snapshot,
        final_score=final_score,
        risk_template=risk_template,
        agent_review_count=agent_review_count,
    )

    # 将所有候选标记按严重级别分流：high/medium → risk_flags，info → info_tags
    all_candidate_flags = list(dict.fromkeys(
        all_risks + quality_risks + conflict_risks + market_discipline["risk_flags"] + quality_infos
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
        "consensus_score": round(consensus_score, 2),
        "setup_type": best_setup.get("setup_match") or "",
        "entry_rule": best_setup.get("entry_rule") or "等待买点确认后再参与",
        "invalid_rule": best_setup.get("invalid_rule") or "跌破关键支撑且收不回",
        "rationale": _rationale(signals),
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
        "component_scale": round(component_scale, 4),
        "market_regime": _market_regime_label(market_snapshot),
        "skill_disagreement": skill_disagreement_metrics(signals),
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
    }


def apply_pool_rules(
    cards: list[dict[str, Any]],
    output_count: int | None = None,
    min_score: float = 45.0,
    *,
    score_line: float | None = None,
    market_snapshot: dict[str, Any] | None = None,
    risk_template: str = "balanced",
    display_limit: int | None = None,
) -> list[dict[str, Any]]:
    """按分数线过滤正式推荐，展示上限不参与核心选股决策。"""
    if display_limit is None and output_count is not None:
        display_limit = resolve_display_limit(output_count, market_snapshot)
    score_line_context = resolve_score_line_context(
        score_line if score_line is not None else min_score,
        market_snapshot=market_snapshot,
        risk_template=risk_template,
    )
    ordered = sorted(
        cards,
        key=lambda card: float(card.get("final_score", 0)),
        reverse=True,
    )
    # 代码去重：同一只股票只保留最高分的推荐
    seen_codes: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for card in ordered:
        code = str(card.get("code") or "")
        if code and code in seen_codes:
            continue
        if code:
            seen_codes.add(code)
        deduped.append(card)
    result: list[dict[str, Any]] = []
    top_final_score = 0.0
    for card in deduped:
        final_score = float(card.get("final_score") or 0)
        if final_score <= 0:
            continue
        if final_score > top_final_score:
            top_final_score = final_score
        card_adjustment = _card_score_line_adjustment(card)
        execution_viability = _execution_viability(card)
        effective_score_line = round(
            score_line_context["effective_score_line"]
            + card_adjustment["evidence_adjustment"]
            + execution_viability["score_line_adjustment"],
            2,
        )
        card["score_line_meta"] = {
            **score_line_context,
            **card_adjustment,
            "execution_adjustment": execution_viability["score_line_adjustment"],
            "execution_status": execution_viability["status"],
            "execution_blocked": execution_viability["status"] == "blocked",
            "block_reason": execution_viability["block_reason"],
            "effective_score_line": effective_score_line,
            "passed": final_score >= effective_score_line and execution_viability["status"] != "blocked",
        }
        if execution_viability["risk_flags"]:
            card["risk_flags"] = _dedup_risk_flags(
                list(card.get("risk_flags") or []) + execution_viability["risk_flags"]
            )
        card["execution_viability"] = execution_viability
        if execution_viability["status"] == "blocked":
            continue
        if final_score < effective_score_line:
            continue
        result.append(card)

    # 动态降线：无候选过线时，以 top_score - 0.5 为软下界，避免零推荐
    if not result and ordered and top_final_score > 0:
        soft_floor = round(top_final_score - 1.0, 2)
        base_floor = round(score_line_context["base_score_line"] - 8.0, 2)
        dynamic_line = max(soft_floor, base_floor, 35.0)
        # top_score 距有效线 20 分以内启用降线（防止质量过差时强行出票）
        gap = score_line_context["effective_score_line"] - top_final_score
        if 0 < gap <= 20.0:
            score_line_context["dynamic_soft_floor_applied"] = True
            score_line_context["dynamic_soft_floor"] = dynamic_line
            score_line_context["soft_floor_gap"] = round(gap, 2)
            for card in ordered:
                final_score = float(card.get("final_score") or 0)
                if final_score <= 0:
                    continue
                sm = card.get("score_line_meta", {})
                ev = card.get("execution_viability", {})
                if ev.get("status") == "blocked":
                    continue
                old_effective = sm.get("effective_score_line", 99)
                new_effective = min(old_effective, dynamic_line)
                sm["effective_score_line"] = new_effective
                sm["dynamic_soft_floor_applied"] = True
                sm["passed"] = final_score >= new_effective
                if final_score >= new_effective:
                    result.append(card)
    result = _apply_sector_diversity(result)
    if display_limit is None:
        return result
    if display_limit <= 0:
        return result
    return result[:display_limit]


# ============================================================================
# 内部辅助函数
# ============================================================================

_SECTOR_DIVERSITY_PENALTY = {1: 0.0, 2: 5.0}  # 第 N 只同行业股票的分数惩罚（第3只起扣10分）


def _apply_sector_diversity(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """板块去重：同行业第二只推荐扣 5 分，第三只起扣 10 分。

    因果机制：同行业股票高度相关，集中持有没有分散效果。
    跳过未知行业（无 industry 字段的卡片不参与去重）。
    """
    industry_count: dict[str, int] = {}
    for card in cards:
        industry = str(card.get("industry") or "")
        if not industry or industry == "unknown":
            continue
        count = industry_count.get(industry, 0) + 1
        industry_count[industry] = count
        penalty = _SECTOR_DIVERSITY_PENALTY.get(count, 6.0)
        if penalty > 0:
            card["sector_diversity_penalty"] = penalty
            card["final_score"] = round(float(card.get("final_score") or 0) - penalty, 2)
    return sorted(cards, key=lambda c: float(c.get("final_score", 0)), reverse=True)


def _empty(stock: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "code": stock.get("code"),
        "name": stock.get("name"),
        "industry": stock.get("industry"),
        "recommend_price": stock.get("price", stock.get("close", 0)),
        "stop_loss": stock.get("stop_loss"),
        "take_profit": stock.get("take_profit"),
        "final_score": 0.0,
        "consensus_score": 0.0,
        "setup_type": "",
        "entry_rule": "",
        "invalid_rule": reason,
        "rationale": reason,
        "risk_flags": [reason],
        "strategy_signals": [],
    }


def _dedup_info_tags(tags: list[str]) -> list[str]:
    """合并语义相近的 info_tags，避免重复提示。"""
    if len(tags) <= 1:
        return tags
    result: list[str] = []
    seen_groups: set[str] = set()
    for tag in tags:
        group = "coverage" if any(kw in tag for kw in ("样本", "评分已降权", "证据质量不足", "Agent 加分受限")) else tag
        if group in seen_groups:
            continue
        seen_groups.add(group)
        if group == "coverage":
            result.append("样本或证据覆盖不足，评分与 Agent 加分已降权")
        else:
            result.append(tag)
    return result


def _collect_risks(signals: list[dict[str, Any]]) -> list[str]:
    risks: list[str] = []
    for signal in signals:
        risks.extend(signal.get("risk_flags", []) or [])
    return list(dict.fromkeys(risks))


def _dedup_risk_flags(tags: list[str]) -> list[str]:
    """合并语义相近的风险标签，避免同一风险在一只票上重复刷屏。"""
    if len(tags) <= 1:
        return tags
    result: list[str] = []
    seen_groups: set[str] = set()
    for tag in tags:
        group = tag
        if any(kw in tag for kw in ("情绪高潮", "高潮期", "退潮", "追高仓位降档")):
            group = "market_heat"
        elif any(kw in tag for kw in ("换手率", "流动性", "大资金进出")):
            group = "liquidity"
        elif any(kw in tag for kw in ("乖离", "偏离MA", "超买", "回踩", "均线")):
            group = "trend_extension"
        elif any(kw in tag for kw in ("龙虎榜", "主力资金", "资金流", "合力信号", "席位")):
            group = "capital_flow"
        elif any(kw in tag for kw in ("非龙头", "跟风", "杂毛", "带动效应", "龙头地位")):
            group = "leader_quality"
        elif any(kw in tag for kw in ("质押", "解禁", "硬风控否决")):
            group = "structural_risk"
        elif any(kw in tag for kw in ("阶段判定矛盾", "Setup 仅部分 Skill 命中", "立场多空矛盾")):
            group = "model_conflict"
        if group in seen_groups:
            continue
        seen_groups.add(group)
        result.append(tag)
    return result


def _rationale(signals: list[dict[str, Any]]) -> str:
    fallback_count = 0
    normal_agent_count = 0
    evidence: list[str] = []
    for signal in signals:
        review = signal.get("agent_skill_review")
        review_runtime = str(review.get("runtime") or "") if isinstance(review, dict) else ""
        is_fallback = bool(signal.get("agent_review_unavailable")) or review_runtime == "agent_skill_fallback_prelim"
        if is_fallback:
            fallback_count += 1
            continue
        review = signal.get("agent_skill_review")
        if isinstance(review, dict):
            normal_agent_count += 1
            summary = review.get("summary")
            if summary:
                evidence.append(str(summary))
        evidence.extend(signal.get("evidence", []) or [])
    if fallback_count and normal_agent_count == 0:
        return "Skill Agent 暂不可用，本次仅按规则预审与市场数据降级输出，不提供语义审查摘要。请结合分数组成、规则命中和风险标签复核。"
    if fallback_count:
        prefix = "部分 Skill Agent 暂不可用，以下摘要仅包含仍可用的审查结论。"
        detail = "；".join(list(dict.fromkeys(evidence))[:5])
        return f"{prefix}{'；' + detail if detail else ''}"
    return "；".join(list(dict.fromkeys(evidence))[:5]) or "策略信号不足，维持观察。"


def _consensus_score(signals: list[dict[str, Any]]) -> float:
    """计算共识得分（信息维度，不再参与加权，仅用于展示）。"""
    positive = sum(1 for signal in signals if float(signal.get("score", 0)) >= 70)
    total = max(1, len(signals))
    if total == 1:
        # 单 Skill 时用规则评分与 Agent 评分的调和均值，不再硬编码 65
        signal = signals[0]
        rule_score = float(signal.get("score", 50))
        review = signal.get("agent_skill_review")
        if isinstance(review, dict):
            agent_score = float(review.get("score", rule_score))
            agent_conf = float(review.get("confidence", 0.5))
            _merged = rule_score * (1 - agent_conf) + agent_score * agent_conf
            return round(min(100.0, max(0.0, _merged)), 2)
        return round(rule_score, 2)
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
    # 新增数据质量评估
    if float(stock.get("pledge_ratio") or 0) > 40:
        cap = min(cap, 72.0)
    if stock.get("has_upcoming_float") and float(stock.get("upcoming_float_ratio") or 0) > 5:
        cap = min(cap, 76.0)
    return cap


def _quality_risks(stock: dict[str, Any], quality_cap: float) -> tuple[list[str], list[str]]:
    """返回 (risk_flags, info_tags)。数据覆盖类归入 info_tags，交易风险归入 risk_flags。"""
    risks: list[str] = []
    infos: list[str] = []

    sample_count = int(stock.get("market_sample_count") or 0)
    if quality_cap < 100:
        if sample_count and sample_count < 100:
            infos.append(f"市场样本仅{sample_count}只，评分已按覆盖率降权")
        else:
            infos.append("证据质量不足，评分已降权")

    data_quality = stock.get("data_quality") or {}
    missing_fields = set(data_quality.get("missing_fields") or [])

    # 合并多维度数据缺失为一条提示
    data_gap_fields = missing_fields & {"news", "announcements", "theme_tags", "dragon_tiger", "auction"}
    if len(data_gap_fields) >= 3:
        infos.append("多维度数据缺失（公告/龙虎榜/题材等），置信度受影响")
    elif len(data_gap_fields) >= 1:
        label_map = {
            "news": "新闻", "announcements": "公告", "theme_tags": "题材标签",
            "dragon_tiger": "龙虎榜", "auction": "集合竞价",
        }
        names = [label_map.get(f, f) for f in sorted(data_gap_fields)]
        infos.append(f"{'/'.join(names)}数据未覆盖，相关风险需人工复核")

    if "quote_conflict" in missing_fields:
        infos.append("多源报价存在冲突，价格证据需复核")

    # 交易面风险（不进 info，进 risk）
    pledge_ratio = float(stock.get("pledge_ratio") or 0)
    if pledge_ratio > 40:
        risks.append(f"股权质押比例{pledge_ratio:.0f}%，大股东流动性风险较高")
    elif pledge_ratio > 30:
        risks.append(f"股权质押比例{pledge_ratio:.0f}%，需关注质押风险")
    if stock.get("has_upcoming_float"):
        float_ratio = float(stock.get("upcoming_float_ratio") or 0)
        if float_ratio > 5:
            risks.append(f"近期有限售股解禁（占总股本{float_ratio:.1f}%），短期抛压风险")

    return list(dict.fromkeys(risks)), list(dict.fromkeys(infos))


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


def _shortline_factor_summary(stock: dict[str, Any]) -> dict[str, Any]:
    return {
        "limit_up_overheat": {
            "score": stock.get("limit_up_overheat_score"),
            "level": stock.get("limit_up_overheat_level"),
        },
        "sector_ladder": {
            "score": stock.get("sector_ladder_completeness_score"),
            "level": stock.get("sector_ladder_level"),
            "limit_up_count": stock.get("sector_ladder_limit_up_count"),
            "height": stock.get("sector_ladder_height"),
        },
        "capital_resonance": {
            "score": stock.get("capital_resonance_score"),
            "signal": stock.get("capital_resonance_signal"),
            "source": stock.get("capital_resonance_source"),
        },
        "twenty_cm_risk": {
            "is_20cm_stock": bool(stock.get("is_20cm_stock")),
            "score": stock.get("twenty_cm_risk_score"),
            "gate": stock.get("twenty_cm_risk_gate"),
            "reason": stock.get("twenty_cm_risk_reason"),
        },
    }


def _shortline_factor_risks(stock: dict[str, Any]) -> list[str]:
    risks: list[str] = []
    if _float(stock.get("limit_up_overheat_score")) >= 70:
        risks.append("涨停过热，聚合风控降档")
    if stock.get("twenty_cm_risk_gate") == "block":
        risks.append("20cm 风险闸门阻断")
    elif stock.get("twenty_cm_risk_gate") == "caution":
        risks.append("20cm 风险闸门提示")
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
    """从快照中提取市场阶段标签。使用统一的判断函数。"""
    base = _market_base(snapshot)
    sentiment = _market_sentiment(snapshot)
    if sentiment and sentiment not in ("", "平淡"):
        return f"{base}·{sentiment}"
    return base


def _market_discipline(
    stock: dict[str, Any],
    market_snapshot: dict[str, Any] | None,
    final_score: float,
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
        regime_note = "冰点试错区间"
        risk_flags.append("情绪冰点，仅允许小仓试错主线龙头")
    if "高潮" in label:
        market_position_cap = min(market_position_cap, 0.20)
        regime_note = "高潮收缩区间"
        risk_flags.append("情绪高潮，提高门槛并减少输出，追高只允许轻仓")
    if limit_down >= max(5, limit_up):
        market_position_cap = min(market_position_cap, 0.20)
        risk_flags.append("跌停压力不低于涨停，先控制回撤")

    win_rate = _estimated_win_rate(stock, final_score, agent_review_count, up_ratio)
    if final_score < 40:
        base_position = 0.0
        position_label = "观望"
    elif win_rate < 0.60:
        base_position = 0.15
        position_label = "观察仓"
    elif win_rate < 0.70:
        base_position = 0.35
        position_label = "小仓"
    elif win_rate < 0.80:
        base_position = 0.55
        position_label = "中仓"
    else:
        base_position = 0.75
        position_label = "大仓"

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
    agent_review_count: int,
    up_ratio: float,
) -> float:
    evidence = float(stock.get("evidence_quality_score") or 60.0)
    authenticity = float(stock.get("data_authenticity", 1.0))
    risk_score = float(stock.get("risk_score") or 0.0)
    # 分数越高胜率越高，基准不再依赖 bucket
    win_rate = 0.30 + final_score / 200.0
    win_rate += (evidence - 60.0) / 400.0
    win_rate += (authenticity - 0.70) * 0.10
    win_rate += min(0.06, max(-0.08, (up_ratio - 0.5) * 0.18))
    win_rate += min(0.05, agent_review_count * 0.02)
    win_rate -= min(0.12, risk_score / 700.0)
    return max(0.10, min(0.93, win_rate))


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _time_int(value: Any) -> int:
    raw = "".join(ch for ch in str(value or "") if ch.isdigit())
    if not raw:
        return 0
    return int(raw[:6].ljust(6, "0"))
