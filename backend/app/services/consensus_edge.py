"""L-126/L-127 自适应共识闸门：用"已结算的近 K 日多背书边际"逐日选择选股政策。

问题（2026-09-16 第 23/24 轮）：`≥3 背书`（多策略共振）是 W1/W2 的强正信号、W3 的强负信号，
而**当日可观测**的任何特征（regime × 情绪、涨停家数、中位 ivol、分数差）都无法把它切开——
同一个桶在 W1/W2 为正、在 W3 为负。唯一可用的是已结算的近期表现：

    edge(t) = mean(net | 背书数 ≥3) − mean(net | 背书数 <3)      # 只用 t 之前已结算的成交

据此逐日选择政策版本（见 `resolve_adaptive_selection_policy`）：

    edge > 0        → 正向分支（多策略共振 + 大成交额/高换手闸门）
    edge < −1.5     → 反向分支（只买 ≤2 背书，W3 这类"共振即拥挤"的行情）
    其余/样本不足   → 中性分支（只留闸门，不改共识方向）

严格因果：只读 `recommendations.trade_date < signal_date` 且 `outcome_label ∈ (success, failed)`
（即已结算）的成交；任何异常都退回默认政策，保证每日跑批不会因为统计量缺失而失败。
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

from sqlalchemy import text

# 逐日政策版本的默认映射（与 production_selection.v15/v17/v18 一致）
NEUTRAL_VERSION = 15
POSITIVE_VERSION = 17
NEGATIVE_VERSION = 18
DEFAULT_LOOKBACK_DAYS = 15
DEFAULT_NEGATIVE_THRESHOLD = -1.5
DEFAULT_MIN_SIDE = 5
SETTLED_LABELS = ("success", "failed")


def count_non_veto_signals(skill_breakdown: Any) -> int:
    """统计一张卡的"非否决策略背书数"（与 aggregator 的 consensus 闸门同口径）。"""
    if isinstance(skill_breakdown, str):
        try:
            skill_breakdown = json.loads(skill_breakdown)
        except (TypeError, ValueError):
            return 0
    if not isinstance(skill_breakdown, Iterable) or isinstance(skill_breakdown, Mapping):
        return 0
    return sum(
        1
        for item in skill_breakdown
        if isinstance(item, Mapping) and not item.get("hard_veto")
    )


def compute_consensus_edge(
    db: Any,
    signal_date: str,
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    min_side: int = DEFAULT_MIN_SIDE,
) -> float | None:
    """返回 `edge(t)`；样本任一侧不足 `min_side` 时返回 None（调用方回落到中性分支）。"""
    rows = db.execute(
        text(
            """
            SELECT r.trade_date, r.skill_breakdown_json, o.executed_return
            FROM recommendations r
            JOIN recommendation_outcomes o ON o.recommendation_id = r.recommendation_id
            WHERE r.trade_date < :signal_date
              AND o.outcome_label IN :labels
              AND o.executed_return IS NOT NULL
              AND o.entry_triggered = 1
            ORDER BY r.trade_date DESC
            """
        ).bindparams(labels=tuple(SETTLED_LABELS)),
        {"signal_date": str(signal_date)},
    ).fetchall()
    days: list[str] = []
    for trade_date, _b, _r in rows:
        d = str(trade_date)
        if d not in days:
            days.append(d)
        if len(days) >= lookback_days:
            break
    window = set(days)
    high: list[float] = []
    low: list[float] = []
    for trade_date, breakdown, net in rows:
        if str(trade_date) not in window:
            continue
        nsig = count_non_veto_signals(breakdown)
        value = float(net)
        (high if nsig >= 3 else low).append(value)
    if len(high) < min_side or len(low) < min_side:
        return None
    return sum(high) / len(high) - sum(low) / len(low)


def resolve_adaptive_selection_policy(
    db: Any,
    signal_date: str,
    default_policy: Any,
    *,
    load_policy: Any | None = None,
) -> Any:
    """按 edge 选择当日政策；未启用/统计量缺失/异常时一律返回 `default_policy`（保守）。"""
    if not getattr(default_policy, "adaptive_consensus_enabled", False):
        return default_policy
    if load_policy is None:  # 延迟导入，避免循环依赖
        from app.engine.production_policy import load_production_selection_policy

        load_policy = load_production_selection_policy
    try:
        edge = compute_consensus_edge(db, signal_date)
    except Exception:  # 统计量不可得时绝不阻塞跑批
        return default_policy
    if edge is None:
        return default_policy
    if edge > 0:
        version = POSITIVE_VERSION
    elif edge < DEFAULT_NEGATIVE_THRESHOLD:
        version = NEGATIVE_VERSION
    else:
        version = NEUTRAL_VERSION
    try:
        return load_policy(version=version)
    except Exception:
        return default_policy
