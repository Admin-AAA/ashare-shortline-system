"""三窗入场规则离线筛查（面板口径，path A，只读、零 DB）。

面板由 `scripts/research_strategy_ablation_fast.py --targets none --panel-dir` 产出：
每个交易日的 policy **输入态**卡（含 ivol 分位与 regime 内部标记）+ 当日 market_snapshot
+ 每张候选卡的因果净收益 `net_by_code`。因此"只改选中集合、不改候选池"的入场规则
都能离线重跑真实 `evaluate_selection_policy` 并直接得到组合指标，不必重放 1 小时。

纪律：
- 前置断言（L-112a）：identity 规则必须逐日复现面板里的 `selected`，且三窗指标与
  `ablate_fast_baseline_W*.json` 的 A 臂一致；不一致直接抛错，不允许解释候选 Δ。
- 每个候选必须三窗都过 `evaluate_challenger`（L-010）才算 PASS，并报逐月同号（L-058）。
- 只测"事前假设驱动"的少量规则，不做大网格（避免把噪声调成信号）。

用法：
  cd backend && .venv/bin/python scripts/research_entry_rule_panel.py --rules all
  cd backend && .venv/bin/python scripts/research_entry_rule_panel.py --rules panic40,consensus2
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

from app.engine.production_policy import (
    evaluate_selection_policy,
    load_production_selection_policy,
    SelectionPolicyContext,
)
from app.services.backtest.challenger_decision import (
    PerformanceSnapshot,
    evaluate_challenger,
)

PANEL_DIR = B / "output" / "research" / "entry_panel"
BASE_DIR = B / "output" / "research" / "per_strategy"
OUT = B / "output" / "research" / "entry_rule_panel.json"
WINDOWS = ("W1", "W2", "W3")
SCORE_LINE = 72.0
OUTPUT_COUNT = 10


# --------------------------------------------------------------------------- #
# 规则注册表：每个规则 = 纯函数 (cards, day) -> 新 cards（可返回空表 = 当日不入场）
# --------------------------------------------------------------------------- #
def _fs(card: dict) -> dict:
    return card.get("feature_snapshot") or {}


def _non_veto_signals(card: dict) -> list[dict]:
    return [s for s in (card.get("strategy_signals") or [])
            if isinstance(s, dict) and not s.get("hard_veto")]


def _dominant(card: dict) -> str:
    """卡片的"主导策略" = 非否决策略里分最高的那个（与落库 bucket=best_strategy_id 同义）。

    注意：面板卡是 policy 的输入态，`bucket` 字段此时还没写（那是持久化阶段由
    best_strategy_id 赋的），所以必须从 strategy_signals 现算，不能读 card["bucket"]。
    """
    sigs = _non_veto_signals(card)
    if not sigs:
        return ""
    return str(max(sigs, key=lambda s: float(s.get("score") or 0)).get("strategy_id") or "")


def _base_sentiment(snapshot: dict) -> tuple[str, str]:
    """复用生产自身的 regime 判定（不另写一套，避免口径漂移）。"""
    from app.engine.aggregator import _market_base, _market_sentiment

    return str(_market_base(snapshot or {}) or ""), str(_market_sentiment(snapshot or {}) or "")


def _climax_top_n(n: int, bases: tuple[str, ...] | None = None):
    """高潮日只留分数最高的 n 张（可按市场基态限定）。

    生产现有旋钮 climax_output_divisor 只按"情绪=高潮"生效；W3 的亏损集中在**强牛·高潮**，
    而 W2 的高潮日多为偏多·高潮——所以把"减输出"限定到强牛基态是本轮的假设。
    """

    def rule(cards, day):
        base, sent = _base_sentiment(day.get("market_snapshot") or {})
        if sent != "高潮" or (bases is not None and base not in bases):
            return cards
        return sorted(
            cards,
            key=lambda c: -float(c.get("selection_score") or c.get("final_score") or 0.0),
        )[:n]

    return rule


def r_identity(cards, day):
    return cards


def _make_day_gate(predicate):
    def rule(cards, day):
        return [] if predicate(day["market_snapshot"]) else cards
    return rule


def _limit_down_ge(thr):
    def pred(ms):
        return float(ms.get("limit_down_count") or 0) >= thr
    return pred


def _up_ratio_lt(thr):
    def pred(ms):
        ms = dict(ms or {})
        breadth = ms.get("breadth") or {}
        ratio = breadth.get("up_ratio")
        if ratio is None:
            up = float(ms.get("up_count") or 0.0)
            down = float(ms.get("down_count") or 0.0)
            if up + down <= 0:
                return False
            ratio = up / (up + down)
        return float(ratio) < thr
    return pred


def _limit_up_ge(thr):
    def pred(ms):
        return float(ms.get("limit_up_count") or 0) >= thr
    return pred


def _card_filter(predicate):
    def rule(cards, day):
        return [c for c in cards if predicate(c, day)]
    return rule


def _apply_score_delta(card: dict, delta: float) -> dict:
    """给卡片降/加分：**必须同时改 final_score / selection_score / portfolio_rank_score**。

    坑：`apply_pool_rules` 读的是 `selection_score`（缺省才回落到 final_score），
    只改 final_score 会让"降分"变体变成静默空操作（实测 Δ≡0 才发现）。
    """
    cc = dict(card)
    for key in ("final_score", "selection_score", "portfolio_rank_score"):
        if key in cc or key == "final_score":
            cc[key] = float(cc.get(key) or cc.get("final_score") or 0.0) + delta
    return cc


# 聚合权重轴：`final_score = min(Σ w_i·raw_i · consensus_boost · 真实性折扣, quality_cap)`。
# 上面这条恒等式已用面板逐卡复算验证（W3 1858/1858 一致，误差 <0.02），因此权重变体可以
# **离线**评估，不必重放。默认权重取自生产卡片自身（aggregation_weights）。
DEFAULT_W = {"skill_agent": 0.0, "strategy_rule": 0.55, "market": 0.20, "risk": 0.15, "evidence": 0.10}
WEIGHT_VARIANTS: dict[str, dict] = {
    "w_current": dict(DEFAULT_W),                                   # 保真度对照，必须 Δ≡0
    "w_ev15_sr50": {**DEFAULT_W, "strategy_rule": 0.50, "evidence": 0.15},
    "w_ev15_risk150": {**DEFAULT_W, "strategy_rule": 0.50, "evidence": 0.15, "market": 0.20},
    "w_risk20_sr50": {**DEFAULT_W, "strategy_rule": 0.50, "risk": 0.20},
    "w_mkt15_sr60": {**DEFAULT_W, "strategy_rule": 0.60, "market": 0.15},
    "w_mkt25_sr50": {**DEFAULT_W, "strategy_rule": 0.50, "market": 0.25},
    "w_sr65_mkt15_risk12_ev08": {"skill_agent": 0.0, "strategy_rule": 0.65, "market": 0.15,
                                 "risk": 0.12, "evidence": 0.08},
    "w_sr45_mkt25_risk15_ev15": {"skill_agent": 0.0, "strategy_rule": 0.45, "market": 0.25,
                                 "risk": 0.15, "evidence": 0.15},
}


def _weight_rule(weights: dict):
    """按给定权重复算 final_score/selection_score/portfolio_rank_score（缺字段的卡原样放行）。"""

    def rule(cards, _day):
        out = []
        for c in cards:
            raw = c.get("raw_component_scores")
            if not isinstance(raw, dict):
                out.append(c)
                continue
            base = sum(float(weights.get(k, 0.0)) * float(raw.get(k, 0.0) or 0.0) for k in weights)
            score = base * float(c.get("consensus_boost") or 1.0)
            auth = float(c.get("data_authenticity") or 1.0)
            if auth < 0.7:
                score *= 0.85
            if auth < 0.5:
                score *= 0.75
            cap = (c.get("score_breakdown") or {}).get("quality_cap")
            if cap is not None:
                score = min(score, float(cap))
            out.append({**c, "final_score": round(score, 2), "selection_score": round(score, 2),
                        "portfolio_rank_score": round(score, 2)})
        return out

    return rule




def _penalty(predicate, points):
    def rule(cards, day):
        out = []
        for c in cards:
            out.append(_apply_score_delta(c, -points) if predicate(c, day) else c)
        return out
    return rule


def _vol5_20_ge(thr):
    def pred(card, _day):
        v = _fs(card).get("vol5_20")
        return v is not None and float(v) >= thr
    return pred


def _non_main_theme(_card=None, _day=None):
    raise NotImplementedError


def _regime_of(card: dict) -> str:
    return str(card.get("market_regime") or "")


def _low_consensus(card: dict, min_signals: int = 2, min_score: float | None = None) -> bool:
    sigs = _non_veto_signals(card)
    if min_score is not None:
        sigs = [s for s in sigs if float(s.get("score") or 0) >= min_score]
    return len(sigs) < min_signals


def _consensus_gate_in(regimes: tuple[str, ...]):
    """只在指定 regime 卡上施加"至少 N 个策略背书"，其余 regime 原样（避免削掉强趋势窗）。"""

    def rule(cards, _day):
        out = []
        for c in cards:
            regime = _regime_of(c)
            if any(r in regime for r in regimes) and _low_consensus(c):
                continue
            out.append(c)
        return out

    return rule


def _consensus_soft(points: float, regime_in: tuple[str, ...] | None = None):
    def rule(cards, _day):
        out = []
        for c in cards:
            regime = _regime_of(c)
            if (regime_in is None or any(r in regime for r in regime_in)) and _low_consensus(c):
                out.append(_apply_score_delta(c, -points))
            else:
                out.append(c)
        return out

    return rule


def _consensus_boost(
    points: float,
    regime_in: tuple[str, ...] | None = None,
    min_signals: int = 2,
):
    """给满足"至少 min_signals 个非否决策略背书"的卡加分（可限定 regime）。"""

    def rule(cards, _day):
        out = []
        for c in cards:
            regime = _regime_of(c)
            if (regime_in is None or any(r in regime for r in regime_in)) and not _low_consensus(
                c, min_signals=min_signals
            ):
                out.append(_apply_score_delta(c, points))
            else:
                out.append(c)
        return out

    return rule


def _industry_not_in_main(card, day):
    ms = day["market_snapshot"] or {}
    main = ms.get("main_industries") or []
    if not main:
        return False
    return str(card.get("industry") or "") not in {str(x) for x in main}


RULES: dict[str, "callable"] = {
    "identity": r_identity,
    # ① 大盘广度/恐慌闸门：跌停蔓延日不进场（事前可得：T 收盘统计）
    "panic20": _make_day_gate(_limit_down_ge(20)),
    "panic40": _make_day_gate(_limit_down_ge(40)),
    "panic60": _make_day_gate(_limit_down_ge(60)),
    # ② 赚钱效应闸门：上涨家数占比过低（普跌日）不进场
    "upratio035": _make_day_gate(_up_ratio_lt(0.35)),
    "upratio045": _make_day_gate(_up_ratio_lt(0.45)),
    # ③ 情绪过热闸门：涨停家数过多（追高区）不进场
    "euphoria120": _make_day_gate(_limit_up_ge(120)),
    "euphoria150": _make_day_gate(_limit_up_ge(150)),
    # ④ 追量闸门：个股放量倍数过高（追高）剔除
    "vol5_20_ge20": _card_filter(_vol5_20_ge(2.0)),
    "vol5_20_ge25": _card_filter(_vol5_20_ge(2.5)),
    # ⑤ 共识闸门：只留至少 2 个非否决策略背书
    "consensus2": _card_filter(lambda c, _d: len(_non_veto_signals(c)) >= 2),
    "consensus3": _card_filter(lambda c, _d: len(_non_veto_signals(c)) >= 3),
    # ⑤b 共识闸门的 regime 条件化与软化变体：consensus2 已在三窗把 avg/sum 全部抬高，
    # 只因 W2 同时退化 WR 与 MDD 两项辅助被闸门否掉（L-010 允许至多一项辅助退化）。
    # 这三个变体分别检验"只在弱/震荡施加""软降分而非剔除""只认高分背书"。
    "consensus2_weak": _consensus_gate_in(("弱势",)),
    "consensus2_weak_osc": _consensus_gate_in(("弱势", "震荡")),
    "consensus2_soft6": _consensus_soft(6.0),
    "consensus2_soft6_weak": _consensus_soft(6.0, regime_in=("弱势", "震荡")),
    "consensus2_hi70": _card_filter(lambda c, _d: not _low_consensus(c, min_score=70.0)),
    # 高潮日专用（W3 亏损集中在"强牛·高潮"）：高潮里要求至少 3 个策略背书。
    "consensus3_climax": _card_filter(
        lambda c, _d: not ("高潮" in _regime_of(c) and _low_consensus(c, min_signals=3))
    ),
    # 高潮减输出（regime 条件化）：W3 亏损集中在强牛·高潮；W1/W2 的高潮日更杂。
    "climax_top3_strong": _climax_top_n(3, ("强牛",)),
    "climax_top4_strong": _climax_top_n(4, ("强牛",)),
    "climax_top3_all": _climax_top_n(3),
    "climax_top4_all": _climax_top_n(4),
    # DSA 的"回撤特征"迁移（用户 2026-09-15 建议）：W2 里 DSA 相关卡的共同点是
    # 小/中市值 + 有 MA 支撑（sup5 0.37 vs 无 DSA 0.16）；W3 里 DSA 主导卡的亏损卡
    # 集中在高 ivol(0.80) + 高位(pos60 68/dist60 12)；W3 的赢家是低位(pos60 45/dist60 1.4)+低 ivol(0.65)。
    # → 检验"高波动/高位的卡必须有均线支撑，否则不进"这一类跨策略条件。
    "gate_support_hiivol": _card_filter(
        lambda c, _d: not (
            (float(c.get("ivol20_pct") or 0) >= 0.70)
            and not (c.get("support_ma5") or c.get("support_ma10"))
        )
    ),
    "gate_support_hidn": _card_filter(
        lambda c, _d: not (
            (float(c.get("ivol_dn20_pct") or 0) >= 0.50)
            and not (c.get("support_ma5") or c.get("support_ma10"))
        )
    ),
    "gate_support_extended": _card_filter(
        lambda c, _d: not (
            (_fs(c).get("pos60") is not None and float(_fs(c).get("pos60")) >= 70.0)
            and not (c.get("support_ma5") or c.get("support_ma10"))
        )
    ),
    "gate_support_hiivol_ext": _card_filter(
        lambda c, _d: not (
            (float(c.get("ivol20_pct") or 0) >= 0.70)
            and (_fs(c).get("pos60") is not None and float(_fs(c).get("pos60")) >= 65.0)
            and not (c.get("support_ma5") or c.get("support_ma10"))
        )
    ),
    "penalize_no_support6": _penalty(lambda c, _d: not (c.get("support_ma5") or c.get("support_ma10")), 6.0),
    # ⑤c 反向杠杆：不剔除低共识卡，而是给"多策略背书"加分（保留全部候选，
    # 只改排序/过线）。假设：背书数在弱势/震荡里是质量信号，在强趋势里失效。
    "consensus_boost6": _consensus_boost(6.0),
    "consensus_boost10": _consensus_boost(10.0),
    "consensus_boost6_weak_osc": _consensus_boost(6.0, regime_in=("弱势", "震荡")),
    "consensus_boost10_weak_osc": _consensus_boost(10.0, regime_in=("弱势", "震荡")),
    "consensus3_boost10": _consensus_boost(10.0, min_signals=3),
    # ⑥ 主线闸门（LIVE 反事实强候选；本轮首次在三窗全区间可测，因为全管线重放的
    #    market_snapshot 带 main_industries，而面板口径的 W2/W3 快照没有）
    "non_main_p4": _penalty(_industry_not_in_main, 4.0),
    "non_main_p8": _penalty(_industry_not_in_main, 8.0),
    "non_main_drop": _card_filter(lambda c, d: not _industry_not_in_main(c, d)),
}

# 阈值敏感性：只剔"最高 ivol 且无支撑"的更小一档（W3 WR 掉太多的备选）
for _thr in (0.75, 0.80, 0.85, 0.90):
    RULES[f"gate_support_hiivol_{int(_thr*100)}"] = _card_filter(
        lambda c, _d, _t=_thr: not (
            (float(c.get("ivol20_pct") or 0) >= _t)
            and not (c.get("support_ma5") or c.get("support_ma10"))
        )
    )

# 组合/口径变体：只改 policy 调用上下文（score_line / output_count），不改卡也不改政策字段。
# 动机：生产盘前预设 `preset_formal_opening` 实际是 **Top6 + score_line 72**，而本筛查器的
# canonical 口径是 Top10；"每天只留前 N 名"本身就是一个直接的胜率杠杆。
CONTEXT_VARIANTS: dict[str, dict] = {
    "oc8": {"output_count": 8},
    "oc6": {"output_count": 6},
    "oc4": {"output_count": 4},
    "sl76": {"score_line": 76.0},
    "sl80": {"score_line": 80.0},
    "oc6_sl76": {"output_count": 6, "score_line": 76.0},
    "oc6_sl80": {"output_count": 6, "score_line": 80.0},
}


for _wname, _wmap in WEIGHT_VARIANTS.items():
    RULES.setdefault(_wname, _weight_rule(_wmap))


# 逐策略信号权重轴：`strategy_rule = Σ(w_s·score_s)/Σ(w_s)`（已用面板多信号卡 1211/1211 验证），
# 再把 strategy_rule 代回 final_score 公式。单信号卡不受影响（权重在分子分母里约掉），
# 因此这条轴只改变"多策略联合背书"卡的相对排序——正好用来压低"自己主导但胜率低"的技能。
SIGNAL_WEIGHT_VARIANTS: dict[str, dict] = {
    "sigw_identity": {},                                  # 保真度对照，必须 Δ≡0
    "sigw_dsa05": {"daily-stock-analysis-skill": 0.5},
    "sigw_dsa025": {"daily-stock-analysis-skill": 0.25},
    "sigw_chaogu15": {"chao-gu-yang-jia-skill": 1.5},
    "sigw_chen15": {"chen-xiaoqun-skill": 1.5},
    "sigw_dsa05_chaogu15": {"daily-stock-analysis-skill": 0.5, "chao-gu-yang-jia-skill": 1.5},
    "sigw_dsa05_chen15": {"daily-stock-analysis-skill": 0.5, "chen-xiaoqun-skill": 1.5},
    "sigw_dsa05_chaogu15_chen15": {"daily-stock-analysis-skill": 0.5, "chao-gu-yang-jia-skill": 1.5,
                                   "chen-xiaoqun-skill": 1.5},
}


def _signal_weight_rule(overrides: dict):
    """按逐技能权重重算 strategy_rule → final_score（缺组件分的卡原样放行）。"""

    def rule(cards, _day):
        out = []
        for c in cards:
            raw = c.get("raw_component_scores")
            sigs = [s for s in (c.get("strategy_signals") or [])
                    if isinstance(s, dict) and not s.get("hard_veto")]
            if not isinstance(raw, dict) or not sigs:
                out.append(c)
                continue
            num = den = 0.0
            for s in sigs:
                w = float(overrides.get(str(s.get("strategy_id")), 1.0))
                num += float(s.get("score") or 0.0) * w
                den += w
            if den <= 0:
                out.append(c)
                continue
            new_raw = dict(raw)
            new_raw["strategy_rule"] = num / den
            weights = c.get("aggregation_weights") or {}
            base = sum(float(weights.get(k, 0.0)) * float(new_raw.get(k, 0.0) or 0.0)
                       for k in ("skill_agent", "strategy_rule", "market", "risk", "evidence"))
            score = base * float(c.get("consensus_boost") or 1.0)
            auth = float(c.get("data_authenticity") or 1.0)
            if auth < 0.7:
                score *= 0.85
            if auth < 0.5:
                score *= 0.75
            cap = (c.get("score_breakdown") or {}).get("quality_cap")
            if cap is not None:
                score = min(score, float(cap))
            out.append({**c, "final_score": round(score, 2), "selection_score": round(score, 2),
                        "portfolio_rank_score": round(score, 2)})
        return out

    return rule


# "YAML 声明权重真正生效"配置：把每个 skill 的 strategy.yaml `weight:` 当作信号权重
# （现状是全部 1.0 —— 声明被静默忽略，见 L-120）。
SIGNAL_WEIGHT_VARIANTS.update({
    "sigw_yaml_declared": {
        "chao-gu-yang-jia-skill": 1.1, "chen-xiaoqun-skill": 1.5, "chip-single-peak-breakout": 0.2,
        "daily-stock-analysis-skill": 0.3, "high-range-breakout-momentum": 0.35,
        "limit-up-reseal-relay": 0.6, "overnight-holding-method": 0.35,
        "sector-relative-strength-leader": 1.5, "trend-pullback-continuation": 0.2,
    },
    "sigw_yaml_declared_chaogu1": {
        "chao-gu-yang-jia-skill": 1.0, "chen-xiaoqun-skill": 1.5, "chip-single-peak-breakout": 0.2,
        "daily-stock-analysis-skill": 0.3, "high-range-breakout-momentum": 0.35,
        "limit-up-reseal-relay": 0.6, "overnight-holding-method": 0.35,
        "sector-relative-strength-leader": 1.5, "trend-pullback-continuation": 0.2,
    },
    "sigw_declared_dsa05": {
        "chao-gu-yang-jia-skill": 1.1, "chen-xiaoqun-skill": 1.5, "chip-single-peak-breakout": 0.2,
        "daily-stock-analysis-skill": 0.5, "high-range-breakout-momentum": 0.35,
        "limit-up-reseal-relay": 0.6, "overnight-holding-method": 0.35,
        "sector-relative-strength-leader": 1.5, "trend-pullback-continuation": 0.2,
    },
})
# 生产 preset_formal_opening 的逐技能权重（LIVE 走 signal.weight）以及其上的候选改动。
_PRESET_W = {"chao-gu-yang-jia-skill": 1.1, "chen-xiaoqun-skill": 1.5, "chip-single-peak-breakout": 0.2, "daily-stock-analysis-skill": 0.3, "high-range-breakout-momentum": 0.95, "limit-up-reseal-relay": 0.9, "overnight-holding-method": 0.85, "sector-relative-strength-leader": 1.5, "trend-pullback-continuation": 0.2}
SIGNAL_WEIGHT_VARIANTS.update({
    "sigw_preset": dict(_PRESET_W),
    "sigw_preset_dsa05": {**_PRESET_W, "daily-stock-analysis-skill": 0.5},
    "sigw_preset_dsa05_others1": {"daily-stock-analysis-skill": 0.5, "chen-xiaoqun-skill": 1.5},
    "sigw_preset_no_low": {k: (1.0 if v < 0.9 else v) for k, v in _PRESET_W.items()},
})
for _n, _m in SIGNAL_WEIGHT_VARIANTS.items():
    RULES.setdefault(_n, _signal_weight_rule(_m))

# 政策级变体（不筛卡，只改 policy 字段）：W3 的亏损集中在"强牛·高潮"交易日，
# 现有政策已有 climax_nonweak_extra（高潮抬线）与 climax_output_divisor（高潮减半输出），
# 这里检验把它们推得更紧是否能在三窗同时改善；以及把共识阈值在弱势/震荡推到 3。
POLICY_VARIANTS: dict[str, dict] = {
    "climax_extra_12": {"climax_nonweak_extra": 12.0},
    "climax_extra_16": {"climax_nonweak_extra": 16.0},
    "climax_extra_24": {"climax_nonweak_extra": 24.0},
    "climax_div_3": {"climax_output_divisor": 3},
    "consensus3_weak": {"consensus_min_signals": 3, "consensus_regimes": ("弱势",)},
    "consensus3_weak_osc": {"consensus_min_signals": 3, "consensus_regimes": ("弱势", "震荡")},
    "consensus3_strong": {"consensus_min_signals": 3, "consensus_regimes": ("强牛",)},
    "climax16_consensus3strong": {
        "climax_nonweak_extra": 16.0,
        "consensus_min_signals": 3,
        "consensus_regimes": ("强牛",),
    },
    "climax16_consensus3_weakosc": {
        "climax_nonweak_extra": 16.0,
        "consensus_min_signals": 3,
        "consensus_regimes": ("弱势", "震荡"),
    },
    # 组合：两个在单轴上各自 2/3 通过的改动叠在一起（climax 减输出 + 更严共识）
    "climax_div_3_consensus3_weak": {
        "climax_output_divisor": 3,
        "consensus_min_signals": 3,
        "consensus_regimes": ("弱势",),
    },
    "climax_div_3_consensus3_weak_osc": {
        "climax_output_divisor": 3,
        "consensus_min_signals": 3,
        "consensus_regimes": ("弱势", "震荡"),
    },
    "climax_div_4": {"climax_output_divisor": 4},
    # (卡片规则, policy 覆盖)：高潮减少输出 + 高潮要求 ≥3 背书，两条同轴的组合
    "climax_div_3_consensus3climax": ("consensus3_climax", {"climax_output_divisor": 3}),
    "consensus3_climax_only": ("consensus3_climax", {}),
    # 近期窗口（W3）导向的加严变体：在 v6 的"强牛·高潮只留前 3"之上再收紧
    "top2_strong": {"strong_climax_top_n": 2},
    "top3_consensus3climax": ("consensus3_climax", {}),
    "top2_consensus3climax": ("consensus3_climax", {"strong_climax_top_n": 2}),
}


# --------------------------------------------------------------------------- #
def _metrics(rows: list[dict], all_days: list[str]) -> dict | None:
    if not rows:
        return None
    df = pd.DataFrame(rows)
    net = df.net
    wins, losses = net[net > 0], net[net < 0]
    daily = df.groupby("dt").net.mean().reindex(all_days).fillna(0.0)
    cum = daily.cumsum()
    return {
        "n": int(len(df)),
        "wr": float((net > 0).mean()),
        "avg": float(net.mean()),
        "sum": float(net.sum()),
        "pf": float(wins.sum() / max(abs(losses.sum()), 1e-9)),
        "mdd": float((cum - cum.cummax()).min()),
        "cohort": float(daily.mean()),
        "days": int(df.dt.nunique()),
        "daily": daily,
    }


def _boot_ci(delta: np.ndarray, draws: int = 4000, seed: int = 7):
    rng = np.random.default_rng(seed)
    n = len(delta)
    means = np.array([delta[rng.integers(0, n, n)].mean() for _ in range(draws)])
    return float(delta.mean()), float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


GLOBAL_SIGNAL_WEIGHTS: dict | None = None   # 非 None 时，规则臂先用这套逐技能权重重算卡片分数


def run_rule(rule, panel: dict, pol, ctx_overrides: dict | None = None,
             apply_global_weights: bool = True) -> tuple[dict | None, list[dict]]:
    overrides = ctx_overrides or {}
    score_line = float(overrides.get("score_line", SCORE_LINE))
    output_count = int(overrides.get("output_count", OUTPUT_COUNT))
    rows: list[dict] = []
    all_days: list[str] = []
    for day in panel["days"]:
        all_days.append(day["signal_date"])
        base_cards = [dict(c) for c in day["cards"]]
        if apply_global_weights and GLOBAL_SIGNAL_WEIGHTS:
            base_cards = _signal_weight_rule(GLOBAL_SIGNAL_WEIGHTS)(base_cards, day)
        cards = rule(base_cards, day)
        if not cards:
            continue
        res = evaluate_selection_policy(
            cards,
            SelectionPolicyContext(
                score_line=score_line,
                output_count=output_count,
                market_snapshot=dict(day["market_snapshot"]),
                risk_template="balanced",
                same_day_industry_counts=day["same_day_industry_counts"],
                same_day_recommendation_count=day["same_day_recommendation_count"],
            ),
            pol,
        )
        for c in res.recommendations:
            code = str(c.get("code"))
            net = day["net_by_code"].get(code)
            if net is None:
                continue
            rows.append({"dt": day["signal_date"], "code": code, "net": float(net),
                         "exit": day["exit_by_code"].get(code)})
    return _metrics(rows, all_days), rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", default="all")
    ap.add_argument(
        "--single-skill",
        action="store_true",
        help="额外测每个技能的**单策略组合**（只保留该 skill 有非否决策略信号的卡，再走真实 policy）",
    )
    ap.add_argument(
        "--baseline-version",
        type=int,
        default=None,
        help="面板导出时的 production_selection 版本（默认=当前 default），只用于"
             "「离线 identity 是否复现面板 selected」这一步对账。Δ 的基线始终用当前 default。",
    )
    ap.add_argument(
        "--signal-weights",
        default="equal",
        help="equal（默认）| preset（读 preset_formal_opening 权重）| JSON 字符串；"
             "非 equal 时所有规则臂先用该逐技能权重重算卡分数（用于让页面/项目口径与生产一致）",
    )
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    names = list(RULES) if args.rules == "all" else [r.strip() for r in args.rules.split(",") if r.strip()]
    if args.rules == "policy":
        names = list(POLICY_VARIANTS)
    elif args.rules == "all":
        names = list(RULES) + list(POLICY_VARIANTS)
    pol = load_production_selection_policy()
    base_pol = (
        load_production_selection_policy(version=args.baseline_version)
        if args.baseline_version is not None
        else pol
    )
    global GLOBAL_SIGNAL_WEIGHTS
    _sw = str(args.signal_weights or "equal").strip()
    if _sw == "equal":
        GLOBAL_SIGNAL_WEIGHTS = None
    elif _sw == "preset":
        from sqlalchemy import create_engine, text as _text

        from app.core.config import get_settings as _gs

        url = _gs().database_url
        eng = create_engine(url)
        with eng.connect() as conn:
            rows = conn.execute(_text(
                "SELECT skill_id, weight FROM preset_skills WHERE preset_id='preset_formal_opening'"
            )).all()
        GLOBAL_SIGNAL_WEIGHTS = {str(r[0]): float(r[1]) for r in rows}
    else:
        GLOBAL_SIGNAL_WEIGHTS = json.loads(_sw)
    if GLOBAL_SIGNAL_WEIGHTS:
        print(f"[weights] 规则臂使用逐技能权重：{GLOBAL_SIGNAL_WEIGHTS}")
    panels = {w: json.loads((PANEL_DIR / f"entry_panel_{w}.json").read_text()) for w in WINDOWS}
    def harness_baseline(w: str) -> dict:
        """harness A 臂基线：优先读 ablation JSON；缺失时用面板自带的 selected+net 复原
        （两者同源：selected 来自 harness 的 formal_cards，net 来自同一次因果出场模拟）。"""
        path = BASE_DIR / f"ablate_fast_baseline_{w}.json"
        if path.exists():
            return json.loads(path.read_text())[w]["A"]
        rows = []
        for d in panels[w]["days"]:
            for code in d["selected"]:
                net = d["net_by_code"].get(code)
                if net is not None:
                    rows.append({"dt": d["signal_date"], "code": code, "net": float(net)})
        m = _metrics(rows, [d["signal_date"] for d in panels[w]["days"]])
        assert m is not None
        return {k: v for k, v in m.items() if k != "daily"}

    baselines_expected = {w: harness_baseline(w) for w in WINDOWS}

    print("=== 基线前置断言（identity 必须复现面板 selected 与 harness A 臂） ===")
    base_metrics: dict[str, dict] = {}
    base_rows: dict[str, list[dict]] = {}
    for w in WINDOWS:
        # 1) 对账：面板的 selected 是在 panel_pol（升级前的默认）下产生的
        m_recon, _ = run_rule(RULES["identity"], panels[w], base_pol, apply_global_weights=False)
        # 2) Δ 基线：当前默认策略（升级后 = 新生产默认）
        m, rows = run_rule(RULES["identity"], panels[w], pol, apply_global_weights=False)
        exp = baselines_expected[w]
        assert m is not None and m_recon is not None, w
        sel_panel = sum(len(d["selected"]) for d in panels[w]["days"])
        # 离线对账基线 vs harness 基线：允许极小边界日噪声（面板 JSON 里记录 days），
        # 但量级必须一致——不一致就不能解释任何候选 Δ（L-112a）。
        d_avg = m_recon["avg"] - exp["avg"]
        d_n = m_recon["n"] - exp["n"]
        mismatch_days = panels[w].get("fidelity_mismatch_days") or []
        # 容差按"边界日噪声"设定：实测 W2 有 3 天离线重跑与 harness 在第 10 张卡上互换
        # 1 张（n 相同、成分不同）→ Δavg 约 0.035pp。超过 0.10pp 说明不是边界噪声而是
        # 面板本身失真，必须停。
        ok = abs(d_avg) <= 0.10 and abs(d_n) <= 3
        print(f"  {w}: 对账基线 n={m_recon['n']} avg={m_recon['avg']:+.4f} "
              f"| harness n={exp['n']} avg={exp['avg']:+.4f} WR={exp['wr']*100:.1f}% "
              f"| Δn={d_n:+d} Δavg={d_avg:+.4f} 边界日={len(mismatch_days)} "
              f"→ {'OK' if ok else 'MISMATCH'}｜Δ基线 v{pol.version}: n={m['n']} "
              f"avg={m['avg']:+.4f} WR={m['wr']*100:.1f}%")
        if not ok:
            raise RuntimeError(f"{w} 对账不一致：offline={m_recon} vs harness={exp}")
        base_metrics[w] = m
        base_rows[w] = rows

    def _register_per_skill_rules() -> list[str]:
        skills = sorted({
            str(s.get("strategy_id"))
            for w in WINDOWS for d in panels[w]["days"] for c in d["cards"]
            for s in (c.get("strategy_signals") or [])
            if isinstance(s, dict) and s.get("strategy_id")
        })
        for sid in skills:
            RULES[f"only:{sid}"] = (
                lambda cards, _d, _sid=sid: [
                    c for c in cards
                    if any(str(s.get("strategy_id")) == _sid and not s.get("hard_veto")
                           for s in (c.get("strategy_signals") or []) if isinstance(s, dict))
                ]
            )
            RULES[f"dominant:{sid}"] = (
                lambda cards, _d, _sid=sid: [c for c in cards if _dominant(c) == _sid]
            )
            # 主导策略剔除（可限定 regime）：v5 之后 W3 的亏损几乎全部集中在
            # DSA 主导的卡上（n=27 avg −1.59 sum −43.0），需要按"卡片主导策略"这条轴
            # 而不是"是否出现该策略信号"来筛。
            RULES[f"drop_dom:{sid}"] = (
                lambda cards, _d, _sid=sid: [c for c in cards if _dominant(c) != _sid]
            )
            RULES[f"drop_dom_weakosc:{sid}"] = (
                lambda cards, _d, _sid=sid: [
                    c for c in cards
                    if not (any(r in _regime_of(c) for r in ("弱势", "震荡")) and _dominant(c) == _sid)
                ]
            )
            # 软降权版（L-089 偏好软权重而非硬剔除）：主导分过高、实际边缘不足的策略，
            # 降分而不删卡，保留它作为背书策略的价值。
            for _p in (6.0, 10.0):
                RULES[f"penalize_dom_{int(_p)}:{sid}"] = (
                    lambda cards, _d, _sid=sid, _pts=_p: [
                        (_apply_score_delta(c, -_pts) if _dominant(c) == _sid else c)
                        for c in cards
                    ]
                )
        return skills

    # 逐策略规则（only/dominant/drop_dom/penalize_dom）**无条件注册**：它们只依赖面板卡，
    # 之前挂在 --single-skill 分支下，导致用 --rules 直接点名时报 KeyError 或被静默降级。
    per_skill_names = [
        n for n in _register_per_skill_rules()
        for n in (f"only:{n}", f"dominant:{n}", f"drop_dom:{n}", f"drop_dom_weakosc:{n}",
                  f"penalize_dom_6:{n}", f"penalize_dom_10:{n}")
    ]
    if args.single_skill:
        names = names + [
            n for n in RULES
            if n.startswith(("only:", "dominant:", "drop_dom", "drop_dom_weakosc", "penalize_dom_"))
        ]
        print(f"\n[per-skill] 已注册 {len(per_skill_names) // 6} 个技能的单策略组合规则")

    # 未知规则名必须显式报错：静默回落到 identity 会让"变体无效"伪装成"Δ≡0"，
    # 本轮就这么被骗过一次（penalize_dom_* 当时未注册）。
    unknown = [
        n for n in names
        if n not in RULES and n not in POLICY_VARIANTS and n not in CONTEXT_VARIANTS
    ]
    if unknown:
        raise SystemExit(
            f"未知规则名 {unknown[:5]}（共 {len(unknown)} 个）。可用：RULES 前缀 "
            f"only:/dominant:/drop_dom:/drop_dom_weakosc:/penalize_dom_* 与 POLICY_VARIANTS "
            f"{sorted(POLICY_VARIANTS)[:6]}…"
        )

    print(f"\n=== 规则筛查（{len(names)} 个；PASS = 三窗全过 evaluate_challenger） ===")
    results: dict[str, dict] = {}
    for name in names:
        spec = POLICY_VARIANTS.get(name)
        card_rule_name = name
        if isinstance(spec, tuple):
            card_rule_name, variant = spec
        else:
            variant = spec or {}
        rule = RULES.get(card_rule_name, RULES["identity"])
        if variant:
            policy_for_rule = pol
            for key, value in variant.items():
                policy_for_rule = policy_for_rule.with_rule_patch(key, value)
        else:
            policy_for_rule = pol
        per_window: dict[str, dict] = {}
        passes = 0
        for w in WINDOWS:
            m, rows = run_rule(
                rule, panels[w], policy_for_rule, ctx_overrides=CONTEXT_VARIANTS.get(name)
            )
            if m is None:
                per_window[w] = {"n": 0, "decision": "FALSIFIED(无成交)"}
                continue
            b = base_metrics[w]
            dlt = (m["daily"] - b["daily"]).dropna()
            md, lo, hi = _boot_ci(dlt.values)
            monthly = dlt.groupby(dlt.index.str[:7]).mean()
            pos, neg = int((monthly > 0).sum()), int((monthly < 0).sum())
            dec = evaluate_challenger(
                # MDD 必须传**负值**（(cum-cummax()).min()）：evaluate_challenger 的约定是
                # "less-negative max drawdown is an improvement"，即 delta = B-A > 0 才算改善。
                # 曾误传 abs(mdd) 让极性反转，把"回撤变小"判成退化 —— 会误杀正确候选。
                PerformanceSnapshot(b["avg"], b["sum"], b["wr"] * 100, b["pf"], b["mdd"]),
                PerformanceSnapshot(m["avg"], m["sum"], m["wr"] * 100, m["pf"], m["mdd"]),
            )
            passes += int(dec.should_publish)
            per_window[w] = {
                "n": m["n"], "wr": m["wr"], "avg": m["avg"], "sum": m["sum"], "pf": m["pf"],
                "mdd": m["mdd"], "cohort": m["cohort"], "days": m["days"],
                "d_cohort": md, "ci": [lo, hi],
                "month_same_pos": pos / max(pos + neg, 1), "n_months": pos + neg,
                "decision": dec.decision, "improvements": list(dec.improvements),
                "regressions": list(dec.regressions),
            }
        results[name] = {"windows": per_window, "passes": passes}
        cells = []
        for w in WINDOWS:
            v = per_window[w]
            if not v.get("n"):
                cells.append(f"{w} 无成交")
                continue
            cells.append(f"{w} Δ{v['d_cohort']:+.4f} CI[{v['ci'][0]:+.3f},{v['ci'][1]:+.3f}] "
                         f"WR{v['wr']*100:.1f}% PF{v['pf']:.3f} sum{v['sum']:+.1f} "
                         f"月{v['month_same_pos']:.2f} {v['decision']}")
        print(f"\n[{name}] passes={passes}/3")
        for c in cells:
            print("   ", c)

    out = Path(args.out)
    out.write_text(json.dumps(
        {"policy": "production_selection.v4", "execution": "execution_policy.v4",
         "score_line": SCORE_LINE, "output_count": OUTPUT_COUNT,
         "baseline": {w: {k: v for k, v in base_metrics[w].items() if k != "daily"} for w in WINDOWS},
         "rules": results},
        ensure_ascii=False, indent=1, default=str))
    print(f"\n[saved] {out}")


if __name__ == "__main__":
    main()
