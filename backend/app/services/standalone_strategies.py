"""单策略独立运行（2026-10-14 起取代「全策略组合」正式推荐）。

用户决策：不再把多个策略的信号聚合成一份组合推荐。每个有效策略每天 17:35 盘后
各自独立跑一次 formal run——自己的分数线、自己的出票上限、自己的推荐/T+1 结算/统计。

本模块是单策略口径的唯一权威定义：
- 哪些策略参与每日独立运行（enabled 且非退役、非纯风险提示技能）；
- 每个策略的默认分数线 / 出票上限（可按策略覆盖）；
- 单策略 run 使用的选股政策（在生产 v27 基础上关掉组合专属闸门）；
- 跨 run 去重（同日重复 / 近 7 日重复 / 行业集中）只在同一策略的 run 之间生效。
"""
from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, Iterable, Mapping, Sequence

# 已退役策略：永不参与独立运行（即使有人误把 enabled 打开）。
RETIRED_STRATEGY_IDS: frozenset[str] = frozenset(
    {
        "daily-stock-analysis-skill",  # 基础短线
        "trend-pullback-continuation",  # 强趋势回踩
        "overnight-holding-method",  # 隔夜法
        "dragon-tiger-reaccumulation",
        # 2026-10-14 删除：单策略近12月均值为负且轻量调参无法转正（L-170）
        "limit-up-reseal-relay",
        "low-position-first-board",
        "second-board-leader",
        "within-industry-reversal",
        "dragon-tiger-net-buy-followup",
        # 2026-10-14 删除：近12月 0 张票（数据覆盖不足 / 无有效信号，L-170）
        "northbound-accumulation",
        "institutional-survey-attention",
        # 2026-10-14 删除：单策略口径近12月均值 -0.32%，早窗选参后全窗仍 -0.01%（L-177）
        "chip-single-peak-breakout",
    }
)

# 纯风险提示 / 非选股技能（没有可执行选股合约），不单独出票。
NON_PICKING_SKILL_IDS: frozenset[str] = frozenset({"high-position-volume-stall-warning"})

DEFAULT_SCORE_LINE = 72.0
# 组合口径一天最多 6 张；单策略各自 3 张，7 个策略合计上限 21 张，单策略票数不至于太少。
DEFAULT_OUTPUT_COUNT = 3

# 按策略覆盖分数线 / 出票上限（空 = 用默认）。分数线不得低于 formal 地板 72。
STRATEGY_RUN_OVERRIDES: dict[str, dict[str, float | int]] = {}

# 单策略政策覆盖：组合层闸门里对某些策略结构性不可达的项（当前无）。
STRATEGY_POLICY_OVERRIDES: dict[str, dict[str, Any]] = {}


def standalone_strategy_ids(skills: Iterable[Any]) -> list[str]:
    """参与每日独立运行的策略：enabled、可执行、非退役、非风险提示。"""
    out: list[str] = []
    for skill in skills:
        sid = str(getattr(skill, "skill_id", "") or "")
        if not sid or sid in RETIRED_STRATEGY_IDS or sid in NON_PICKING_SKILL_IDS:
            continue
        if not bool(getattr(skill, "enabled", False)):
            continue
        if getattr(skill, "executable", True) is False:
            continue
        out.append(sid)
    return sorted(set(out))


def standalone_run_settings(skill_id: str) -> tuple[float, int]:
    override = STRATEGY_RUN_OVERRIDES.get(skill_id) or {}
    score_line = max(DEFAULT_SCORE_LINE, float(override.get("score_line") or DEFAULT_SCORE_LINE))
    output_count = int(override.get("output_count") or DEFAULT_OUTPUT_COUNT)
    return score_line, max(1, output_count)


def standalone_skill_id(selected_skills: Sequence[Mapping[str, Any]] | None) -> str | None:
    """一个 run 只选了一个策略 → 单策略 run，返回其 skill_id；否则 None（旧组合 run）。"""
    ids = {str(s.get("skill_id") or "").strip() for s in (selected_skills or []) if isinstance(s, Mapping)}
    ids.discard("")
    return next(iter(ids)) if len(ids) == 1 else None


def run_skill_ids(selected_skills_json: str | None) -> tuple[str, ...]:
    try:
        rows = json.loads(selected_skills_json or "[]")
    except (TypeError, ValueError):
        return ()
    return tuple(
        str(r.get("skill_id") or "") for r in rows if isinstance(r, dict) and r.get("skill_id")
    )


def is_run_of_strategy(selected_skills_json: str | None, skill_id: str) -> bool:
    return run_skill_ids(selected_skills_json) == (skill_id,)


def standalone_selection_policy(base: Any, skill_id: str, output_count: int) -> Any:
    """单策略 run 的选股政策：生产政策 − 组合专属闸门。

    - 共识闸门（弱势/震荡要求 ≥2 个策略背书）对单策略恒不可达 → 关闭；
    - 当日出票上限 = 该策略自己的 output_count（不再与其它策略共享 6 个名额）；
    - 其余风险否决（高潮日封线、ivol、换手、涨幅等）保留，个别策略按 STRATEGY_POLICY_OVERRIDES 豁免。
    """
    changes: dict[str, Any] = {
        "consensus_min_signals": 0,
        "adaptive_consensus_enabled": False,
        "daily_output_limit": int(output_count),
    }
    for key, value in (STRATEGY_POLICY_OVERRIDES.get(skill_id) or {}).items():
        if hasattr(base, key):
            changes[key] = value
    return replace(base, **{k: v for k, v in changes.items() if hasattr(base, k)})
