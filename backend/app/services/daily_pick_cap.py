"""每个策略每日新推荐数量上限（用户 2026-10-26 决策：每策略每天最多推荐 5 只，历史不动）。

- 上限来自 settings.max_daily_picks_per_strategy（默认 5，0 = 不限制），不在各处硬编码。
- 只作用于**新产生的实盘推荐**：短线走 ProductionRecommendationAssembly（仅 live formal run），
  中长线走 midterm_service.generate_picks(apply_daily_cap=True)（仅定时任务 / 手动补跑 API / 晚间大宗任务）。
  回放、回测、历史重建、单股分析不经此处，行为不变。
- 取前 N 的依据永远是该策略**自己已有的排序**（本模块不重排，只切片）：
  短线 = 选股政策输出顺序（portfolio_rank_score 降序，即 final_score 扣行业分散惩罚后）；中长线 = 各策略 candidates 函数返回顺序（见 midterm 各 meta 的排序键）。
- 同分顺延（用户 2026-10-27）：上限按「前 N 个不同分数」计，同分的票一起保留；全部同分时可超过 N 只，分数各不相同时就是前 N 只。
- 这是「当日新推荐数」上限，不是持仓上限；中长线 20 仓位（回测口径）、持有 20/60 日等与之无关。
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

DAILY_CAP_RULE_ID = "daily_strategy_cap"


def daily_pick_cap() -> int:
    """当前生效的每策略每日上限；<=0 表示不限制。读配置失败时保守回退为默认值 5。"""
    try:
        from app.core.config import get_settings

        return int(get_settings().max_daily_picks_per_strategy)
    except Exception:  # noqa: BLE001
        return 5


def _score_key(item: Mapping[str, Any], fields: Sequence[str]) -> Any:
    """取排序分（按 fields 顺序第一个非空值，四舍五入到 6 位比较）；取不到时每只各算一个独立分数档。"""
    for f in fields:
        v = item.get(f)
        if v is None:
            continue
        try:
            return round(float(v), 6)
        except (TypeError, ValueError):
            continue
    return object()


def cap_ordered_picks(picks: Sequence[Mapping[str, Any]], cap: int | None = None) -> tuple[list[Any], dict[str, Any] | None]:
    """按给定顺序保留「前 cap 个分数档」的全部股票（用户 2026-10-27：同分顺延）。

    picks 已按策略自身排序（同分必相邻）。沿顺序数不同的 score 值，第 cap+1 个新分数出现时截断；
    同分的票一起保留，所以全部同分时可超过 cap 只。没有 score 字段的票各自算一档（等同旧的取前 N 只）。
    返回 (保留列表, 审计)；未截断或 cap<=0 时审计为 None、列表原样。"""
    cap = daily_pick_cap() if cap is None else int(cap)
    items = list(picks)
    if cap <= 0 or len(items) <= cap:
        return items, None
    levels: list[Any] = []
    cut = len(items)
    for i, p in enumerate(items):
        k = _score_key(p, ("score",))
        if not levels or k != levels[-1]:
            if len(levels) >= cap:
                cut = i
                break
            levels.append(k)
    if cut >= len(items):
        return items, None
    dropped = items[cut:]
    audit = {
        "rule": DAILY_CAP_RULE_ID,
        "cap": cap,
        "cap_unit": "score_levels",
        "candidates_before_cap": len(items),
        "kept_count": cut,
        "truncated_count": len(dropped),
        "truncated_codes": [str(p.get("code") or "") for p in dropped],
        "order_basis": "strategy_own_ranking",
    }
    return items[:cut], audit


def is_live_formal_run(db: Any, run_id: str) -> bool:
    """run 行存在、run_mode=formal 且不是历史重建/回放证据 → 实盘每日出票。无此 run 行（回测回放、单股分析）一律 False。"""
    try:
        from sqlalchemy import select

        from app.models.recommendation import RecommendationRun

        row = db.execute(
            select(
                RecommendationRun.run_mode,
                RecommendationRun.evidence_class,
                RecommendationRun.reconstruction_batch_id,
            ).where(RecommendationRun.run_id == str(run_id))
        ).first()
    except Exception:  # noqa: BLE001
        return False
    if row is None:
        return False
    run_mode, evidence_class, recon = row
    if str(run_mode or "") != "formal" or recon:
        return False
    return evidence_class in (None, "", "live_forward")


def _supporters(card: Mapping[str, Any], default_ids: Sequence[str]) -> set[str]:
    """卡片归属哪些策略：有非 hard_veto 信号的策略；取不到时回退为 default_ids。"""
    ids = {
        str(s.get("strategy_id") or "")
        for s in (card.get("strategy_signals") or [])
        if isinstance(s, Mapping) and not s.get("hard_veto") and s.get("strategy_id")
    }
    ids &= set(default_ids) if default_ids else ids
    return ids or set(default_ids)


def cap_cards_per_strategy(
    formal_cards: Sequence[dict[str, Any]],
    *,
    strategy_ids: Sequence[str],
    cap: int,
    already_counts: Mapping[str, int] | None = None,
    scored_cards: Sequence[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """对短线正式推荐卡（已按政策顺序排好）按策略施加每日上限。

    上限按「分数档」计（用户 2026-10-27：同分顺延）：每个策略最多取前 cap 个不同的 portfolio_rank_score，
    同分的卡一起保留，所以全部同分时可超过 cap 只。
    - 单策略 run：strategy_ids=[sid]，already_counts={sid: 当日同策略已有推荐数}；已落库的每只各算一档，保证同日多次 run 合计档数 ≤ cap。
    - 多策略 run（旧组合口径/手动 /run）：卡片按「有信号的策略」归属；只有当它所属的策略**全部**拒收才丢弃，
      否则保留并给其所有归属策略记档（一张卡同时背书多个策略时不会因其中一个满额被误杀）。
    被截断的卡在 formal_cards/scored_cards 对应 dict 上写 policy_drop_owner + policy_verdict_steps。
    """
    cap = int(cap)
    if cap <= 0 or not strategy_ids:
        return list(formal_cards), None
    base = {sid: int((already_counts or {}).get(sid, 0)) for sid in strategy_ids}
    levels: dict[str, set[Any]] = {sid: set() for sid in strategy_ids}
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    single = len(strategy_ids) == 1
    fields = ("portfolio_rank_score", "selection_score", "final_score")

    def _accepts(s: str, k: Any) -> bool:
        return k in levels[s] or base[s] + len(levels[s]) < cap

    for card in formal_cards:
        sup = set(strategy_ids) if single else _supporters(card, strategy_ids)
        k = _score_key(card, fields)
        if sup and not any(_accepts(s, k) for s in sup):
            dropped.append(card)
            continue
        for s in sup:
            if _accepts(s, k):
                levels[s].add(k)
        kept.append(card)
    if not dropped:
        return kept, None
    dropped_codes = [str(c.get("code") or "") for c in dropped]
    drop_set = set(dropped_codes)
    reason = f"每个策略每日最多新推荐前 {cap} 个分数（同分顺延，按策略自身排序）"
    for pool in (formal_cards, scored_cards or ()):
        for card in pool:
            if str(card.get("code") or "") in drop_set:
                card["policy_drop_owner"] = DAILY_CAP_RULE_ID
                steps = card.setdefault("policy_verdict_steps", [])
                if not any(isinstance(x, dict) and x.get("rule_id") == DAILY_CAP_RULE_ID for x in steps):
                    steps.append({"rule_id": DAILY_CAP_RULE_ID, "action": "drop", "reason": reason, "eliminated": True})
    audit = {
        "rule": DAILY_CAP_RULE_ID,
        "cap": cap,
        "cap_unit": "score_levels",
        "strategy_ids": list(strategy_ids),
        "already_counts": {k: int(v) for k, v in (already_counts or {}).items()},
        "candidates_before_cap": len(formal_cards),
        "kept_count": len(kept),
        "truncated_count": len(dropped),
        "truncated_codes": dropped_codes,
        "order_basis": "policy_output_order(portfolio_rank_score desc)",
    }
    return kept, audit
