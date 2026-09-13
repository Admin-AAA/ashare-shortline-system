from typing import Any

# 保持旧接口兼容：从 agent_skill_runner 导入 enrich_signal_with_agent_review
# 调用方可以通过此模块直接访问该函数
from app.engine.agent_skill_runner import enrich_signal_with_agent_review  # noqa: F401
from app.engine.board_rules import (
    is_one_word_limit_up_card as _is_one_word_limit_up_card,
)
from app.services.analysis.return_improvement_shadow import (
    annotate_return_improvement_shadow,
)
from app.services.execution_constraints import (
    entry_no_liquidity_reason_from_card as _entry_no_liquidity_reason,
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
    (["科创板20cm涨跌停"], "medium"),
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
    (["数据缺失", "数据未覆盖", "关键数据缺失", "数据完整性不足", "证据覆盖不足"], "info"),
    (["Skill Agent 审查暂不可用", "Skill Agent 直接审查失败", "Agent 审查暂不可用"], "info"),
    # ── info: 数据覆盖/完整性（不扣分） ──
    (["样本或证据覆盖不足", "数据完整度", "证据质量不足"], "info"),
    (["龙虎榜数据缺失", "无龙虎榜数据", "新闻/公告未完整覆盖", "题材标签缺失", "多源报价存在冲突"], "info"),
    (["样本仅", "评分已按覆盖率降权", "评分已降权"], "info"),
    (["Agent 加分受限", "规则评分受限"], "info"),
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

# LLM 只作为风险 overlay：规则负责 alpha/排序，LLM 只能通过风险标签、
# veto 和执行确认影响结果。保留 skill_agent 分量为 0，避免审查分数再次
# 通过聚合器改变候选排序。
RISK_OVERLAY_WEIGHTS = {
    "skill_agent": 0.00,
    "strategy_rule": 0.55,
    "market": 0.20,
    "risk": 0.15,
    "evidence": 0.10,
}
# 旧导入名保留，避免外部调用方在迁移期间崩溃；语义已改为 risk overlay。
AGENT_PRESENT_WEIGHTS = RISK_OVERLAY_WEIGHTS

# --- 市场阶段阈值（涨停率 / 上涨比） ---
# 基于历史数据回测：高潮胜率 62.2% vs 活跃 55.1%（Δ=+7.2pp, t=3.17）
SENTIMENT_LIMIT_UP_RATE_HOT = 0.012    # 涨停率 > 1.2% → 高潮
SENTIMENT_LIMIT_UP_RATE_ACTIVE = 0.005  # 涨停率 > 0.5% → 活跃
SENTIMENT_LIMIT_UP_RATE_FLAT = 0.001    # 涨停率 >= 0.1% → 平淡
SENTIMENT_LIMIT_DOWN_RATIO = 3          # 涨停/跌停比 > 3:1 才确认高潮
# 活跃判定：涨停须明显多于跌停（否则「涨停孤岛+普跌」伪活跃）
# 实证 07-07：lu=44 ld=54 up_ratio=0.124 仍被标活跃(+3 madj)，残留毒区 弱势·活跃 核心日
SENTIMENT_ACTIVE_LU_LD_RATIO = 1.2
BASE_UP_RATIO_BULL = 0.65               # 上涨比 >= 65% → 强牛
BASE_UP_RATIO_BULLISH = 0.50            # 上涨比 >= 50% → 偏多
BASE_UP_RATIO_RANGE = 0.35              # 上涨比 >= 35% → 震荡
STRUCTURAL_STRONG_HARD_VETO_REGIME = "结构性强势"


def _market_sentiment(snapshot: dict[str, Any] | None) -> str:
    """统一的市场情绪判断，全系统唯一入口。

    双维度：涨停率 + 涨停/跌停比。仅用涨停率会把「普跌日零星涨停」标活跃，
    抬高 madj、放宽 score_line（self_check 弱势·活跃 毒区注释 / 07-07 实证）。
    """
    if not snapshot:
        return "平淡"
    sample = max(1, int(snapshot.get("sample_count") or 1))
    limit_up = int(snapshot.get("limit_up_count") or 0)
    limit_down = int(snapshot.get("limit_down_count") or 0)
    # 用涨停率而非绝对数，适配不同市场规模
    lu_rate = limit_up / sample
    ld_rate = limit_down / sample
    lu_ld = lu_rate / max(ld_rate, 0.0001)
    if lu_rate > SENTIMENT_LIMIT_UP_RATE_HOT and (
        ld_rate == 0 or lu_ld > SENTIMENT_LIMIT_DOWN_RATIO
    ):
        return "高潮"
    # 活跃：涨停率够且涨停明显多于跌停（排除普跌伪活跃）
    if lu_rate > SENTIMENT_LIMIT_UP_RATE_ACTIVE and (
        ld_rate == 0 or lu_ld >= SENTIMENT_ACTIVE_LU_LD_RATIO
    ):
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
    elif sentiment == "活跃":
        # 活跃阶段止损率 34%（vs 高潮 21%），提高风控权重
        weights["risk"] = min(0.18, weights["risk"] + 0.03)
        weights["strategy_rule"] = min(0.35, weights["strategy_rule"] + 0.02)
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
    selection_policy: Any | None = None,
) -> dict[str, Any]:
    base = float(base_score_line or 0)
    market_base = _market_base(market_snapshot)
    sentiment = _market_sentiment(market_snapshot)
    market_adjustments = dict(getattr(selection_policy, "market_base_score_adjustments", ())) or {
        "强牛": 6.0,  # 回测：强势 12 笔 0% WR，-6.01% avg（从+3.0提到+6.0，彻底过滤）
        "偏多": 0.0,
        "震荡": 0.0,
        "弱势": 2.0,
    }
    market_adjustment = market_adjustments.get(market_base, 0.0)
    # 验证（第103轮 T3，148 笔去重，score_line_meta.market_base）：
    #   弱势·高潮 78.1% 胜率（32 笔，反转信号，+7 不抑制 ✅）
    #   弱势·活跃 76.7% 胜率（30 笔，+4.3 不抑制 ✅）
    #   强牛·高潮 58.3% 胜率（12 笔，易追高，需高门槛 ✅ 方向正确）
    #   偏多·高潮 61.1% 胜率（36 笔，+10.6 已提高门槛）
    # 结论：参数方向正确（弱势放宽、强势过滤），印证第92轮教训（弱势是反转信号）
    # 高潮阶段分数线调整：强牛/偏多/震荡·高潮胜率 < 50%（43-47%），需提高门槛
    # 弱势·高潮是反转信号（75.9% 胜率），保持 +5
    _climax_extra_value = float(getattr(selection_policy, "climax_nonweak_extra", 8.0))
    _climax_extra = _climax_extra_value if market_base in ("强牛", "偏多", "震荡") else 0.0
    sentiment_adjustments = dict(getattr(selection_policy, "sentiment_score_adjustments", ())) or {
        "高潮": 5.0,
        "活跃": 3.0,  # 回测验证：+5.0 导致更多低质量交易（124笔 vs 111笔），WR 下降 4.5pp
        "平淡": 0.0,
        "冰点": -3.0 if market_base == "弱势" else 0.0,
    }
    sentiment_adjustment = sentiment_adjustments.get(sentiment, 0.0)
    if sentiment == "高潮":
        sentiment_adjustment += _climax_extra
    if sentiment == "冰点" and market_base != "弱势":
        sentiment_adjustment = 0.0
    risk_adjustment = {
        "conservative": 2.0,
        "balanced": 0.0,
        "aggressive": -1.0,
    }.get(str(risk_template or "balanced"), 0.0)
    # 上限对齐生产部署：生产版本无 95 硬上限，强牛·高潮阶段分数线可达 ~119。
    # 旧值 95 会让高潮阶段线被压低、与回测/生产不一致；提到 120 仅放开上限，
    # 不影响线 < 95 的常态场景。下限 35 保持不变。
    minimum = float(getattr(selection_policy, "score_line_minimum", 35.0))
    maximum = float(getattr(selection_policy, "score_line_maximum", 120.0))
    effective = round(max(minimum, min(maximum, base + market_adjustment + sentiment_adjustment + risk_adjustment)), 2)
    # 弱势 regime 分数线封顶（2026-08-02 修复）：弱市 gate 分普遍偏低，72 硬地板
    # + 弱势 +2 调整导致 7 月 formal 连续 0 产出；按 policy 的 weak_regime_score_cap
    # 把线压回 60 档，让弱市反转信号有产出机会。
    weak_cap = getattr(selection_policy, "weak_regime_score_cap", None)
    if weak_cap is not None and market_base == "弱势":
        effective = round(min(effective, float(weak_cap)), 2)
    # regime×情绪 组合硬覆盖（L-060，最高优先级）：对 policy 声明的组合直接用
    # 指定分数线，盖过 market_base/sentiment/climax/weak_cap 的一切叠加。用于
    # 精细放行已验证正期望的组合（具体阈值由版本化 policy artifact 持有），
    # 而保持负期望组合（偏多·高潮）的拦截。
    _combo_overrides = dict(getattr(selection_policy, "regime_sentiment_line_overrides", ()) or ())
    _combo_key = f"{market_base}·{sentiment}"
    _combo_override_applied = False
    if _combo_key in _combo_overrides:
        effective = round(float(_combo_overrides[_combo_key]), 2)
        _combo_override_applied = True
    return {
        "base_score_line": round(base, 2),
        "market_base": market_base,
        "market_sentiment": sentiment,
        "market_adjustment": round(market_adjustment + sentiment_adjustment, 2),
        "risk_template_adjustment": round(risk_adjustment, 2),
        "effective_score_line": effective,
        "regime_sentiment_override_applied": _combo_override_applied,
    }


def resolve_display_limit(
    output_count: int | None,
    market_snapshot: dict[str, Any] | None = None,
    selection_policy: Any | None = None,
) -> int | None:
    if selection_policy is not None and not bool(selection_policy.output_limit_enabled):
        return None
    if output_count is None:
        return None
    count = int(output_count or 0)
    if count <= 0:
        return None
    market_base = _market_base(market_snapshot)
    sentiment = _market_sentiment(market_snapshot)
    if sentiment == "高潮":
        divisor = max(1, int(getattr(selection_policy, "climax_output_divisor", 2)))
        return max(1, min(count, int((count + divisor - 1) // divisor)))
    if market_base == "弱势" and sentiment == "冰点":
        return min(count, int(getattr(selection_policy, "weak_ice_output_limit", 2)))
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
        # 不再抬高分数线：LLM 暂不可用时抬高线会让有效线不可达，
        # 任何瞬时故障都必然零推荐（违反北极星「每天可见推荐」）。
        # 降级状态已通过 reasons 透出，并由 _rationale 生成明确警告文本，
        # 规则层分数照常参与排序与软降线兜底。
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
    "neutral": 0.6,        # 实测 neutral 41 笔 WR 14.6% avg -3.18%，与 oppose 等同
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
        if not isinstance(review, dict) or str(review.get("runtime") or "") == "agent_skill_fallback_prelim":
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

    # ── 3. 严重分歧惩罚 ──
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
        # MA 支撑信号（用于 apply_pool_rules 中的市场阶段×信号交叉调整）
        "support_ma5": bool(stock.get("support_ma5")),
        "support_ma10": bool(stock.get("support_ma10")),
    }


def _is_20cm_board(code: str) -> bool:
    """Return whether a code belongs to the 20% daily-limit universe."""

    return str(code or "").startswith(("300", "301", "688", "689"))


def apply_pool_rules(
    cards: list[dict[str, Any]],
    output_count: int | None = None,
    min_score: float = 45.0,
    *,
    score_line: float | None = None,
    market_snapshot: dict[str, Any] | None = None,
    risk_template: str = "balanced",
    display_limit: int | None = None,
    return_watchlist: bool = False,
    watchlist_top_n: int = 8,
    watchlist_min_hit: int = 3,
    same_day_industry_counts: dict[str, int] | None = None,
    same_day_recommendation_count: int = 0,
    board_universe: str | None = None,
    selection_policy: Any | None = None,
) -> list[dict[str, Any]] | dict[str, Any]:
    """按分数线过滤正式推荐，展示上限不参与核心选股决策。

    LLM 运行状态不属于选择策略输入；可用性仅由审查阶段单独记录。
    """
    if display_limit is None and output_count is not None:
        display_limit = resolve_display_limit(output_count, market_snapshot, selection_policy)
    score_line_context = resolve_score_line_context(
        score_line if score_line is not None else min_score,
        market_snapshot=market_snapshot,
        risk_template=risk_template,
        selection_policy=selection_policy,
    )
    # 高潮/强牛阶段禁用动态降线：此阶段宁可零推荐也不降线，避免追高陷阱。
    # 观察池仅在禁用降线的阶段（即真正的高潮/强牛）作为兜底，其余阶段由软降线处理。
    _ctx_base = score_line_context.get("market_base")
    _ctx_sentiment = score_line_context.get("market_sentiment")
    _disable_soft_floor = _ctx_sentiment == "高潮" or _ctx_base == "强牛"
    # LLM 审查是风险 overlay，不是规则选股的硬依赖。深审失败的卡片已经在
    # _build_recommendation_card 中回退到 strategy.yaml 信号；继续让它们进入
    # 正式筛选，避免模型熔断/余额不足把整批推荐变成 0 条。
    formal_cards = list(cards)
    ordered = sorted(
        formal_cards,
        key=lambda card: (
            -float(card.get("final_score", 0)),
            str(card.get("code") or ""),
        ),
    )
    if selection_policy is not None:
        board_universe = selection_policy.board_universe
    if board_universe == "20cm":
        kept_by_board: list[dict[str, Any]] = []
        for card in ordered:
            if _is_20cm_board(str(card.get("code") or "")):
                kept_by_board.append(card)
                continue
            card["policy_drop_owner"] = "board_universe"
            card.setdefault("policy_verdict_steps", []).append({
                "rule_id": "board_universe",
                "action": "drop",
                "reason": "formal universe 仅允许 20cm 板块",
            })
        ordered = kept_by_board
        # 20cm 策略过滤：daily-stock-analysis-skill / chao-gu-yang-jia-skill 在 20cm
        # 表现显著弱于其他策略（DSA clean≥72 n=19 WR=42% avg=-1.17%；炒股养家
        # clean≥72 n=15 WR=33% avg=-1.43%，跨9交易日分散非单日聚集），其逻辑在
        # 高波动 20cm 股票上失效。在 20cm universe 中过滤掉这些策略的推荐，
        # 但保留其 10cm 推荐不受影响。
        EXCLUDED_20CM_BUCKETS = frozenset(
            selection_policy.excluded_20cm_buckets
            if selection_policy is not None
            else {"daily-stock-analysis-skill", "chao-gu-yang-jia-skill"}
        )
        kept_by_bucket: list[dict[str, Any]] = []
        for card in ordered:
            if str(card.get("bucket") or "") not in EXCLUDED_20CM_BUCKETS:
                kept_by_bucket.append(card)
                continue
            card["policy_drop_owner"] = "excluded_strategy_bucket"
            card.setdefault("policy_verdict_steps", []).append({
                "rule_id": "excluded_strategy_bucket",
                "action": "drop",
                "reason": f"20cm 排除策略桶 {card.get('bucket') or ''}",
            })
        ordered = kept_by_bucket
    seen_codes: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for card in ordered:
        code = str(card.get("code") or "")
        if code and code in seen_codes:
            continue
        if code:
            seen_codes.add(code)
        deduped.append(card)
    # 通用横截面尾部否决（本轮新增，默认关闭）：与 L-093 强牛 ivol 否决同构，但因子/方向/阈值
    # 由 policy artifact 声明（extra_tail_veto_field/direction/pct_threshold）。卡片分位由选择
    # 咽喉预附到 extra_tail_veto_field；缺字段（数据不全）保守放行。
    if (
        selection_policy is not None
        and getattr(selection_policy, "extra_tail_veto_enabled", False)
        and _ctx_base in tuple(getattr(selection_policy, "extra_tail_veto_regimes", ()) or ())
    ):
        _etv_field = str(getattr(selection_policy, "extra_tail_veto_field", "extra_tail_veto_pct"))
        _etv_thr = float(getattr(selection_policy, "extra_tail_veto_pct_threshold", 0.80))
        _etv_dir = str(getattr(selection_policy, "extra_tail_veto_direction", "high"))
        kept_after_etv: list[dict[str, Any]] = []
        for card in deduped:
            _etv_pct = card.get(_etv_field)
            if _etv_pct is None:
                kept_after_etv.append(card)
                continue
            try:
                _etv_pct = float(_etv_pct)
            except (TypeError, ValueError):
                kept_after_etv.append(card)
                continue
            _etv_hit = (_etv_pct >= _etv_thr) if _etv_dir == "high" else (_etv_pct <= _etv_thr)
            if _etv_hit:
                card["policy_drop_owner"] = "extra_tail_veto"
                card.setdefault("policy_verdict_steps", []).append({
                    "rule_id": "extra_tail_veto",
                    "action": "drop",
                    "reason": f"{_ctx_base}{_etv_field} 分位 {_etv_pct:.2f} 落在 {_etv_dir} 尾 ({_etv_thr:.2f})",
                    "eliminated": True,
                })
                continue
            kept_after_etv.append(card)
        deduped = kept_after_etv
    # L-093 强牛 regime × 高特质波动横截面风险否决：强牛当日把全市场 ivol20 分位最高的
    # 一档候选硬剔除、由其后按 final_score 排序的下一名自然递补（不空仓、不整日 abstain）。
    # 机制：强牛高潮单元里个股特质风险（剔除市场 beta 后的残差波动）越高，次日跳空止损/
    # 尾部大亏概率越高；该规则是横截面否决而非 L-080 的指数趋势择时，三窗口 canonical 同向。
    # 卡片 ivol20_pct 由选择咽喉（live adapters / 回测 policy_experiment）经
    # risk_features.attach_ivol20_pct 预附；缺字段（数据不全）保守不动，保持 v1/v2 行为。
    if (
        selection_policy is not None
        and getattr(selection_policy, "strong_high_ivol_veto_enabled", False)
        and _ctx_base in tuple(getattr(selection_policy, "strong_high_ivol_regimes", ()) or ())
    ):
        _ivol_thr = float(selection_policy.strong_high_ivol_pct_threshold)
        kept_after_ivol: list[dict[str, Any]] = []
        for card in deduped:
            _ivol_pct = card.get("ivol20_pct")
            if _ivol_pct is None:
                kept_after_ivol.append(card)
                continue
            try:
                _ivol_pct = float(_ivol_pct)
            except (TypeError, ValueError):
                kept_after_ivol.append(card)
                continue
            if _ivol_pct >= _ivol_thr:
                card["policy_drop_owner"] = "strong_high_ivol_veto"
                card.setdefault("policy_verdict_steps", []).append({
                    "rule_id": "strong_high_ivol_veto",
                    "action": "drop",
                    "reason": f"{_ctx_base}高特质波动 ivol20 分位 {_ivol_pct:.2f}≥{_ivol_thr:.2f}",
                    "eliminated": True,
                })
                continue
            kept_after_ivol.append(card)
        deduped = kept_after_ivol
    result: list[dict[str, Any]] = []
    watchlist: list[dict[str, Any]] = []
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
        # 结构性强势/强势阶段 × 非强势入场信号 交叉惩罚：
        # 实盘去重数据（90 笔）：强势WR=14.3%(21笔)，结构性强势WR=21.4%(14笔)，弱势WR=77.4%(53笔)
        # vs support_ma5=False WR=60.5%（n=38），差距 -21pp
        # 因果机制：强势市追强不追弱，有 MA 支撑意味着已回落均线（追弱）
        # 扩大惩罚范围：从"结构性强势"扩展到"强势" + support_ma5（新增强势交叉调整）
        # regime 来自 market_data.py 的 regime 字段（结构性强势/强势/震荡/弱势），
        # 写入 snapshot 后被持久化到 feature_snapshot_json.market_regime
        _snapshot_regime = (
            (market_snapshot or {}).get("market_regime")
            or (market_snapshot or {}).get("regime")
            or ((market_snapshot or {}).get("breadth") or {}).get("regime")
            or ""
        )
        # L-013：决策时 regime 标签必须持久化，禁止读回溯 market_regime 做归因。
        # _snapshot_regime 即聚合时实际使用的决策时 regime，此处显式落进 score_line_meta，
        # 经 persistence 写入 feature_snapshot_json，供下游 regime 否决/χ² 分桶/最差桶归因
        # 以决策时口径读取，不受任何回溯重标影响。regime_strong_penalty 默认 0.0，
        # 仅在结构性强势(+4)/强势(+8) 分支被覆盖（见下方 if/elif）。
        card["_regime_strong_penalty"] = 0.0
        # L-060: 市场级 regime×情绪 override 已放行该组合时,跳过 per-card 的
        # 强势/结构性强势/support_ma5 regime 惩罚——否则市场级放行会被 per-card
        # 惩罚(结构性强势+4等)抵消,override 失效。override 组合已经大样本 OOS
        # 验证正期望,该组合下不再叠加 regime 侧门槛。
        _combo_relaxed = bool(score_line_context.get("regime_sentiment_override_applied"))
        # 涨停股豁免 support_ma5 交叉惩罚：涨停=+10% 必然贴近/高于 MA5，
        # 这是动能副产品而非"回落均线追弱"信号。惩罚误伤 limit-up-reseal-relay
        # 等涨停动能策略（1186 outcomes 中 0 笔，根因：92 分候选被 82+8+6=96 拒绝）。
        _is_limit_up_card = bool(card.get("is_limit_up"))
        _support_penalty = (
            float(selection_policy.strong_support_ma5_penalty)
            if selection_policy is not None else 6.0
        )
        if not _combo_relaxed and _snapshot_regime in ("结构性强势", "强势") and card.get("support_ma5") and not _is_limit_up_card:
            # 提高门槛 6 分（约等于 21pp 胜率差距对应的合理惩罚）
            effective_score_line = round(effective_score_line + _support_penalty, 2)
            card["_regime_support_ma5_penalty"] = _support_penalty
        # 结构性强势/强势阶段全局门槛：实盘去重 90 笔验证
        # 强势 WR=14.3%(21笔,avg=-2.23%)，结构性强势 WR=28.6%(14笔,avg=-2.15%)
        # 第一性原理：强势阶段已涨完，次日均值回归；任何分数都难盈利
        # 数据验证门槛效果（默认 score_line=72）：
        #   - 结构性强势 +4→76：14 笔全过滤，0 误伤（最高 74.5<76）✅
        #   - 强势 +4→76：放过 1 笔(score=77.5, 仍亏 -6.68%)，需更激进
        #   - 强势 +8→80：过滤全部 21 笔，净省 -6.68% 亏损
        # 两阶段因果机制不同：结构性强势主线品种仍有筛选价值，强势普涨追高必亏
        _structural_veto = (
            bool(selection_policy.structural_strong_hard_veto)
            if selection_policy is not None else False
        )
        _strong_penalty = (
            float(selection_policy.strong_score_penalty)
            if selection_policy is not None else 8.0
        )
        if _snapshot_regime == STRUCTURAL_STRONG_HARD_VETO_REGIME:
            # 结构性强势：+4 提线（历史验证的筛选价值）与 veto（999 全拦）解耦——
            # 2026-08-02 用户决策默认关闭 veto，但 +4 门槛仍保留。
            # L-060: 市场级 regime×情绪 override 已放行该组合时跳过此 +4（大样本
            # OOS 验证结构性强势日放行 +1.77%/WR51%,与旧小样本 n=14 结论相反）。
            card["_regime_strong_penalty"] = 4.0
            if _structural_veto:
                effective_score_line = 999.0
                card["_regime_struct_strong_hard_veto"] = True
            elif not _combo_relaxed:
                effective_score_line = round(effective_score_line + 4.0, 2)
        elif _snapshot_regime == "强势" and not _combo_relaxed:
            effective_score_line = round(effective_score_line + _strong_penalty, 2)
            card["_regime_strong_penalty"] = _strong_penalty
        # 震荡 regime 全局 hard veto（第021轮 LIVE formal 因果重放闭环）：
        # LIVE formal 20cm clean≥72: 震荡 n=14 WR=29% avg=-2.94% PF=0.25
        # causal replay: hard veto 后 n=55→37 avg_net +0.62%→+2.20% cum -4.57%→+7.61%
        # WR 50.9%→62.2% PF 1.26→2.25 MDD -21.92%→-15.18%
        # evaluate_challenger 五项全改善零退化→PRODUCTION
        _card_pos60 = float(card.get("pos60") or card.get("feature_snapshot", {}).get("pos60") or 0)
        _oscillation_veto = (
            bool(selection_policy.oscillation_hard_veto)
            if selection_policy is not None else False
        )
        if _snapshot_regime == "震荡" and _oscillation_veto:
            effective_score_line = 999.0
            card["_regime_oscillation_hard_veto"] = True
        # 弱势 regime × chg20<15 交叉惩罚（第150轮 T3 失败归因闭环）：
        # 实盘去重 LIVE formal 96 笔（弱势 regime 全历史）：chg20<15 WR=33.3%（33笔 avg=-0.58%），
        # chg20>=15 WR=65.5%（55笔 avg=2.10%），差距 32pp，且 [0,10)/[10,15)/[15,20)/[20,30)/[30,+)
        # 五档完全单调（33/33/56/61/76%）。6 月强势市场 chg20<15 WR=71%——效果 regime-dependent。
        # 因果：弱势市场资金避险抱团少数强势龙头，chg20<15 的"半强股"无资金关注继续跌；
        # 强势市场普涨，低位补涨股弹性更大。涨停股豁免（limit-up-reseal-relay 策略靠封板动能
        # 不靠 chg20，且炸板回封 chg20 可能为负/低，惩罚会误伤）。
        # 门槛 +12（72→84）模拟：过滤 31 笔（WR=35.5%），放行 10 笔，整体 WR 56.6%→63.1%。
        _card_chg20 = float(card.get("chg20") or card.get("feature_snapshot", {}).get("chg20") or 0)
        _weak_threshold = (
            float(selection_policy.weak_low_chg20_threshold)
            if selection_policy is not None else 15.0
        )
        _weak_penalty = (
            float(selection_policy.weak_low_chg20_penalty)
            if selection_policy is not None else 12.0
        )
        if _snapshot_regime == "弱势" and 0 <= _card_chg20 < _weak_threshold and not _is_limit_up_card:
            effective_score_line = round(effective_score_line + _weak_penalty, 2)
            card["_regime_weak_low_chg20_penalty"] = _weak_penalty
        # 全局恐慌日否决（第146轮 T3 D项攻击，LIVE formal 全策略 May-Jul 302笔去重）：
        # limit_down_count>100 = 系统性恐慌蔓延日，全市场强平传染，均值回归与趋势跟踪齐失效。
        #   ≤50: WR 65.2% / 51-80: WR 67.5% / 81-100: WR 46.4% / >100: WR 11.1%（完全单调）
        #   2026-05-29(ld=134) 27笔 LIVE formal 全失败 WR=0% avg=-4.93%，最高分 76.24。
        # 反事实（+12→84）：过滤 8 笔全 WR=0%（gross=-45.52%），零误报（panic 日最高分 76.24<84），
        #   整体 passed WR 53.5%→56.6% avg +0.546%→+0.912% gross +78%→+124%。
        # 因果：>100只跌停=强平缺口下杀，止损被吞、次日续跌；非 regime-dependent 弱信号
        #   （ld 81-100 dragon-tiger/chen-xiaoqun 仍 WR 90%+，留策略层处理），是硬不可交易条件，
        #   符合「全局只保留 ST/停牌/无数据 等硬性不可交易过滤」原则。
        _panic_limit_down = int((market_snapshot or {}).get("limit_down_count") or 0)
        _panic_threshold = (
            int(selection_policy.panic_limit_down_threshold)
            if selection_policy is not None else 100
        )
        _panic_penalty = (
            float(selection_policy.panic_score_penalty)
            if selection_policy is not None else 12.0
        )
        if _panic_limit_down > _panic_threshold:
            effective_score_line = round(effective_score_line + _panic_penalty, 2)
            card["_panic_day_penalty"] = _panic_penalty
        card["score_line_meta"] = {
            **score_line_context,
            **card_adjustment,
            "execution_adjustment": execution_viability["score_line_adjustment"],
            "execution_status": execution_viability["status"],
            "execution_blocked": execution_viability["status"] == "blocked",
            "block_reason": execution_viability["block_reason"],
            "regime_support_ma5_penalty": card.get("_regime_support_ma5_penalty", 0.0),
            "regime_oscillation_low_pos_penalty": card.get("_regime_oscillation_low_pos_penalty", 0.0),
            "regime_oscillation_hard_veto": card.get("_regime_oscillation_hard_veto", False),
            "regime_weak_low_chg20_penalty": card.get("_regime_weak_low_chg20_penalty", 0.0),
            "panic_day_penalty": card.get("_panic_day_penalty", 0.0),
            # L-013：决策时 regime 标签（honest label）——下游归因一律以此为准，
            # 不得读 feature_snapshot_json.market_regime / run.market_snapshot_json（回溯重算）。
            "decision_regime": _snapshot_regime,
            "regime_strong_penalty": card.get("_regime_strong_penalty", 0.0),
            "regime_struct_strong_hard_veto": card.get("_regime_struct_strong_hard_veto", False),
            "effective_score_line": effective_score_line,
            "selection_score": final_score,
            "portfolio_rank_score": final_score,
            "passed": final_score >= effective_score_line and execution_viability["status"] != "blocked",
        }
        if execution_viability["risk_flags"]:
            card["risk_flags"] = _dedup_risk_flags(
                list(card.get("risk_flags") or []) + execution_viability["risk_flags"]
            )
        card["execution_viability"] = execution_viability
        # 高潮/强牛阶段观察池：未正式过线、未封死成交的候选，作为轻仓观察兜底，
        # 不降低分数线，仅避免系统在长期高潮中完全静默（不推荐 ≠ 无任何信号）。
        if return_watchlist and _disable_soft_floor:
            _wl_blocked = execution_viability["status"] == "blocked"
            _wl_passed = final_score >= effective_score_line and not _wl_blocked
            if not _wl_blocked and not _wl_passed:
                _wl_gap = round(final_score - effective_score_line, 2)
                _wl_hit = _count_hit_strategies(card)
                if _wl_gap >= -15.0 or _wl_hit >= watchlist_min_hit:
                    _wl_card = dict(card)
                    _wl_card["watchlist"] = True
                    _wl_card["watchlist_gap"] = _wl_gap
                    _wl_card["watchlist_hit_strategies"] = _wl_hit
                    _wl_card["watchlist_note"] = "高潮/强牛阶段不正式推荐，仅作轻仓观察"
                    watchlist.append(_wl_card)
        if execution_viability["status"] == "blocked":
            continue
        if final_score < effective_score_line:
            continue
        result.append(card)

    # 动态降线：无候选过线时，以 top_score - 0.5 为软下界，避免零推荐
    # 高潮/强牛阶段禁用降线：回测验证高潮阶段降线导致 7 笔 0% WR 交易（6/9 偏多·高潮）
    # 高潮阶段宁可零推荐也不降线，避免追高陷阱（_disable_soft_floor 已在函数前部计算）
    # 常规软降线：非高潮/强牛阶段、top 距有效线 ≤20 分时启用，避免零推荐。
    # 高潮/强牛阶段禁用（宁可零推荐也不追高）。LLM 可用性不参与该决策。
    #
    # 例外（2026-07-21 T3/T13，LIVE formal 去重）：震荡 全档 soft floor 净负。
    # - 震荡·高潮 soft 12 笔@07-09 WR=33.3% avg=-1.85%（有效线~85→~65）
    # - 震荡·活跃 soft 11 笔@07-16 WR=27.3% avg=-2.04%（同机制）
    # 合 2 独立日 23 笔 WR=30.4% avg=-1.94%；剔除后 clean WR 70.5%→77.8% avg 2.08→2.81。
    # 因果：震荡市无延续、soft 降线放行半吊子票 → 入场即套/硬止损；与高潮/活跃无关，
    # 是 regime=震荡 本身。弱势·高潮 soft 反转信号（9 笔 100%）必须保留；
    # 强牛·高潮 soft 偏正且是 A 项断档兜底，不动。
    _disabled_soft_regimes = set(
        getattr(selection_policy, "dynamic_soft_floor_disabled_regimes", ("震荡",))
    )
    _osc_regime = _ctx_base in _disabled_soft_regimes
    _soft_enabled = bool(getattr(selection_policy, "dynamic_soft_floor_enabled", True))
    _soft_floor_allowed = _soft_enabled and (not _disable_soft_floor) and (not _osc_regime)
    if not result and ordered and top_final_score > 0 and _soft_floor_allowed:
        soft_floor = round(
            top_final_score - float(getattr(selection_policy, "dynamic_soft_floor_offset", 1.0)), 2
        )
        base_floor = round(
            min(
                score_line_context["base_score_line"]
                - float(getattr(selection_policy, "dynamic_soft_floor_base_discount", 8.0)),
                # 弱势 regime 封顶联动（2026-08-02 修复）：cap 生效时（弱势 60），
                # base_floor 若仍按 72-8=64 会让 top<64 的弱市候选永远无法软降线
                # 放行（7 月零推荐链路：gate<58 → top<60 → 线 64 > top）。
                # 取 effective（已含 cap）减 discount，仅在 cap 压低时生效。
                score_line_context["effective_score_line"]
                - float(getattr(selection_policy, "dynamic_soft_floor_base_discount", 8.0)),
            ),
            2,
        )
        dynamic_line = max(
            soft_floor,
            base_floor,
            float(getattr(selection_policy, "score_line_minimum", 35.0)),
        )
        # top 相对市场级有效线的差距：
        #   gap > 0 → top 低于市场线；gap <= 0 → top 已达/超过市场线
        # 旧条件 `0 < gap <= 20` 会漏掉 gap<=0：top 已过市场线，但 per-card
        # 软惩罚（execution_adjustment / evidence_adjustment）把单票有效线抬到
        # top 之上 → 全场 0 推荐且软降线永不触发。
        # 实证：limit-up 执行惩罚 +7 时 top=82/market=77/card_eff=84 → gap=-5，
        # 旧逻辑 rec=0；修复后 gap<=20 也启用。硬否决（panic/弱势×chg20/震荡×pos60）
        # 仍跳过，不放松 D 项毒区。
        gap = score_line_context["effective_score_line"] - top_final_score
        _max_soft_gap = float(getattr(selection_policy, "dynamic_soft_floor_max_gap", 20.0))
        # gap<=20 含 gap<=0（top 已过市场线但被软惩罚挡光）。
        if gap <= _max_soft_gap:
            score_line_context["dynamic_soft_floor_applied"] = True
            score_line_context["dynamic_soft_floor"] = dynamic_line
            score_line_context["soft_floor_gap"] = round(gap, 2)
            score_line_context["soft_floor_reason"] = "per_card_penalty" if gap <= 0 else "near_market_line"
            for card in ordered:
                final_score = float(card.get("final_score") or 0)
                if final_score <= 0:
                    continue
                sm = card.get("score_line_meta", {})
                ev = card.get("execution_viability", {})
                if ev.get("status") == "blocked":
                    continue
                # 震荡 × pos60<50 硬否决：不参与软降线（否则惩罚被降线覆盖，WR=0% 的股票又混进来）
                if sm.get("regime_oscillation_low_pos_penalty"):
                    continue
                if sm.get("regime_oscillation_hard_veto"):
                    continue
                # 弱势 × chg20<15 硬否决：同上，惩罚不被降线覆盖（WR=33% 的半强股不兜底出票）
                if sm.get("regime_weak_low_chg20_penalty"):
                    continue
                # 全局恐慌日否决：ld>100 = WR 0% 系统性恐慌，不参与软降线兜底（否则降线后 76 分的毒区票混进推荐）
                if sm.get("panic_day_penalty"):
                    continue
                # 结构性强势/强势 regime 门槛与 support_ma5 交叉惩罚：
                # soft floor 的 min(old, dynamic) 会把这些 regime 惩罚抹平，
                # 使 support_ma5 毒区票与干净票同线通过（gap<=0 时尤其明显）。
                # 这两类是基于 WR 实证的 regime 侧门槛，不是 evidence/execution 软惩罚，
                # 不参与 soft floor 兜底（与 panic/weak/osc 硬否决同级）。
                if sm.get("regime_support_ma5_penalty"):
                    continue
                if sm.get("regime_strong_penalty"):
                    continue
                if sm.get("regime_struct_strong_hard_veto"):
                    continue
                old_effective = sm.get("effective_score_line", 99)
                new_effective = min(old_effective, dynamic_line)
                sm["effective_score_line"] = new_effective
                sm["dynamic_soft_floor_applied"] = True
                sm["soft_floor_reason"] = score_line_context["soft_floor_reason"]
                sm["passed"] = final_score >= new_effective
                if final_score >= new_effective:
                    result.append(card)
    result = _apply_sector_diversity(result, same_day_industry_counts=same_day_industry_counts)
    daily_output_limit = int(getattr(selection_policy, "daily_output_limit", 10))
    remaining_daily_slots = max(
        0,
        daily_output_limit - max(0, int(same_day_recommendation_count)),
    )
    for card in result[remaining_daily_slots:]:
        card["policy_drop_owner"] = "daily_top_n"
        card.setdefault("policy_verdict_steps", []).append({
            "rule_id": "daily_top_n",
            "action": "drop",
            "reason": f"当日正式推荐最多 {daily_output_limit} 个",
            "eliminated": True,
        })
    result = result[:remaining_daily_slots]
    # 小样本中的名次/形态假设与新增竞价维度只做前瞻 shadow 采集；
    # 未经冻结 OOS + Canary 前不得改变正式排序、选股或仓位。
    annotate_return_improvement_shadow(result)
    # 仅把未进入正式结果的降级卡保留到观察池；已按规则正式出票的降级卡仍会
    # 带着 review_status=degraded 持久化，前端可见降级提示，不会被重复归为观察票。
    selected_card_ids = {id(card) for card in result}
    degraded_watchlist = sorted(
        (
            card
            for card in cards
            if card.get("review_status") == "degraded" and id(card) not in selected_card_ids
        ),
        key=lambda card: float(card.get("final_score", 0)),
        reverse=True,
    )[: max(1, int(output_count or watchlist_top_n))]
    if return_watchlist:
        watchlist.sort(
            key=lambda c: (c.get("watchlist_gap", 0.0), float(c.get("final_score") or 0)),
            reverse=True,
        )
        watchlist = watchlist[:watchlist_top_n]
        _recs = result[:display_limit] if (display_limit and display_limit > 0) else result
        return {
            "recommendations": _recs,
            "watchlist": watchlist,
            "degraded_watchlist": degraded_watchlist,
            "board_universe": board_universe,
        }
    if display_limit is None:
        return result
    if display_limit <= 0:
        return result
    return result[:display_limit]


def _count_hit_strategies(card: dict[str, Any]) -> int:
    """统计实际触发（score>=60）的策略数量，用于高潮观察池筛选。

    不依赖具体 preset 的命中口径，仅以策略信号得分作为命中代理，
    保证观察池偏向「被多策略共同认可、仅因高潮门槛过高而未过线」的候选。
    """
    signals = card.get("strategy_signals") or []
    if not signals:
        return 0
    return sum(1 for s in signals if float(s.get("score", 0)) >= 60.0)


# ============================================================================
# 内部辅助函数
# ============================================================================

_SECTOR_DIVERSITY_PENALTY = {1: 0.0, 2: 5.0}  # 第 N 只同行业股票的分数惩罚
_SECTOR_DIVERSITY_PENALTY_DEFAULT = 10.0         # 第 3 只起的默认惩罚
_MAX_PER_INDUSTRY = 5                             # 单一行业最多推荐 5 只（硬性上限）
# 数据驱动（T5 第79轮反事实验证）：MAX=3 剔除的第4只同行业股 WR=100% avg+5.24%（误杀）
# 放宽到 5：保留 WR 57.6% avg+0.27%（vs MAX=3 的 56.5%/+0.12%），净收益 +2.56%→+26.03%

# 关联行业组：不同 industry 但高度相关（同涨同跌），去重时归为一组
# 因果机制：元器件/半导体/通信设备 共享供应链和终端需求，分散持仓效果有限
# 2026-06-22 实例：元器件4只+半导体2只+通信设备1只 = 7只科技硬件，industry 去重无法识别
_INDUSTRY_GROUPS: dict[str, str] = {}
for _ind in ("元器件", "半导体", "通信设备", "消费电子", "电子制造"):
    _INDUSTRY_GROUPS[_ind] = "科技硬件"
for _ind in ("电池", "新能源车", "汽车零部件", "光伏"):
    _INDUSTRY_GROUPS[_ind] = "新能源"
for _ind in ("证券", "保险", "银行"):
    _INDUSTRY_GROUPS[_ind] = "金融"


def _industry_group(industry: str) -> str:
    """返回行业所属的关联组（无映射则返回原始 industry）。"""
    return _INDUSTRY_GROUPS.get(industry, industry)



def _apply_sector_diversity(
    cards: list[dict[str, Any]],
    *,
    same_day_industry_counts: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """板块去重：同行业第二只推荐扣 5 分，第三只起扣 10 分，第六只起硬性剔除。

    因果机制：同行业股票高度相关，集中持有没有分散效果。
    跳过未知行业（无 industry 字段的卡片不参与去重）。
    使用关联行业组（_industry_group）：元器件+半导体+通信设备 等高度相关行业归为"科技硬件"组，
    在组内做去重，避免不同 industry 但同涨同跌的股票集中持仓。
    same_day_industry_counts 用于把同日其他 formal run 已推荐的行业组数量计入基数，
    防止单 run 内不过线、跨 run 累积后单一板块过度集中。
    """
    industry_count = dict(same_day_industry_counts or {})
    capped: list[dict[str, Any]] = []
    for card in cards:
        selection_score = round(
            float(card.get("selection_score", card.get("final_score") or 0)),
            2,
        )
        card["selection_score"] = selection_score
        card["portfolio_rank_score"] = selection_score
        fs = card.get("feature_snapshot")
        if isinstance(fs, dict):
            fs["selection_score"] = selection_score
            fs["portfolio_rank_score"] = selection_score
        score_line_meta = card.get("score_line_meta")
        if isinstance(score_line_meta, dict):
            score_line_meta["selection_score"] = selection_score
            score_line_meta["portfolio_rank_score"] = selection_score
        industry = str(card.get("industry") or "")
        if not industry or industry == "unknown":
            capped.append(card)
            continue
        group = _industry_group(industry)
        count = industry_count.get(group, 0) + 1
        industry_count[group] = count
        if count > _MAX_PER_INDUSTRY:
            # 第六只起硬性剔除，避免单一行业过度集中
            continue
        penalty = _SECTOR_DIVERSITY_PENALTY.get(count, _SECTOR_DIVERSITY_PENALTY_DEFAULT)
        if penalty > 0:
            card["sector_diversity_penalty"] = penalty
            # 同步到 feature_snapshot，避免持久化路径变更时丢失（card 顶层与 snapshot 双写）
            if isinstance(fs, dict):
                fs["sector_diversity_penalty"] = penalty
            portfolio_rank_score = round(selection_score - penalty, 2)
            card["portfolio_rank_score"] = portfolio_rank_score
            if isinstance(fs, dict):
                fs["portfolio_rank_score"] = portfolio_rank_score
            if isinstance(score_line_meta, dict):
                score_line_meta["portfolio_rank_score"] = portfolio_rank_score
        capped.append(card)
    return sorted(capped, key=lambda c: float(c.get("portfolio_rank_score", 0)), reverse=True)


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
    if not tags:
        return []
    result: list[str] = []
    seen_groups: set[str] = set()
    for tag in tags:
        group = "coverage" if any(kw in tag for kw in ("样本", "评分已降权", "证据质量不足", "Agent 加分受限", "规则评分受限")) else tag
        if group in seen_groups:
            continue
        seen_groups.add(group)
        if group == "coverage":
            result.append("样本或证据覆盖不足，规则评分与风险 overlay 均受限")
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
        return "Skill Agent 暂不可用，本次由规则与市场数据独立输出，不提供语义审查摘要。请结合分数组成、规则命中和风险标签复核。"
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
    missing_fields.update(data_quality.get("optional_missing_fields") or [])

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
    # 科创板额外风险：20% 涨跌停 → 跳空缺口风险远大于主板
    if bool(stock.get("is_kcb")):
        risks.append("科创板20cm涨跌停，跳空缺口风险更高")
    # 筹码套牢盘风险（avg_cost ratio < 0.95 → 0% 历史胜率）
    price = _float(stock.get("price") or stock.get("close"))
    avg_cost = _float(stock.get("avg_cost"))
    if price > 0 and avg_cost > 0:
        cost_ratio = price / avg_cost
        if cost_ratio < 0.90:
            risks.append("深度套牢盘，获利回吐压力极大")
        elif cost_ratio < 0.95:
            risks.append("浅度套牢盘，解套抛压上升")
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

    win_rate = _estimated_win_rate(stock, final_score, up_ratio)
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
    return {
        "market_regime": label,
        "regime_note": regime_note,
        "estimated_win_rate": round(win_rate, 3),
        "position_label": position_label,
        "max_position_pct": max_position_pct,
        "action": action,
        "market_position_cap_pct": round(market_position_cap * 100, 1),
        "risk_template_cap_pct": round(template_cap * 100, 1),
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
