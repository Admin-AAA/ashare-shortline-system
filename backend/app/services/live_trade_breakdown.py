"""实盘（canonical live_forward）推荐的分口径统计：组合时代与单策略分开报，成交分「确认买入」与「开盘模拟」。

2026-10-27 诊断：短线「实盘 9 笔每笔 −5.1%」全部来自已删除的全策略组合（preset_formal_opening）的 run，
并且没有一笔经过盘中买入确认——结算按次日开盘价模拟成交就算进了「实盘」。把它和单策略回测比是两套东西。

口径（只读，不改结算、不动 preset_formal_opening 本身）：
- era：run.preset_id == preset_formal_opening 且选了多个技能 → ``combo``（组合时代）；单技能 run → ``single``
  （10-14/10-15 的单策略补跑 run 也带着 preset_formal_opening 的 preset_id，只看 preset 会把它们错算成组合）；其它 → ``other``。
- 样本：canonical live_forward + 凌晨补跑的正式 run（catchup_formal，时点有效；10-14 批次就是这类），每行标 origin。
- 已结算样本（success/failed/missed）里：
  - ``confirmed``：盘中买入确认命中过（ever_buy_confirmed）且结算有成交 —— 这才叫「实盘成交」；
  - ``open_simulated``：结算按开盘价模拟成交，但盘中从没确认买入 —— 单独一栏，不算实盘成交；
  - ``confirmed_not_filled``：盘中确认过买入，但结算没成交 —— 确认与结算口径不一致的直接证据，单列；
  - ``not_filled``：没有成交（取消 / 过期 / 买点未触发），且盘中从没确认买入。
- 每笔收益 = 结算 executed_return 扣统一附加成本（同 overview 口径 net_return_after_cost）。
"""

from __future__ import annotations

import json
import statistics
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine.trading_costs import net_return_after_cost
from app.models.recommendation import RecommendationExecutionState
from app.models.tables import Recommendation, RecommendationOutcome, RecommendationRun

COMBO_PRESET_ID = "preset_formal_opening"
SETTLED_LABELS = frozenset({"success", "failed", "missed"})
FILL_CLASSES = ("confirmed", "open_simulated", "confirmed_not_filled", "not_filled")


def _skill_ids(selected_skills_json: str | None) -> tuple[str, ...]:
    try:
        items = json.loads(selected_skills_json or "[]")
    except (TypeError, ValueError):
        return ()
    out = []
    for item in items if isinstance(items, list) else []:
        sid = item.get("skill_id") if isinstance(item, dict) else item
        if sid:
            out.append(str(sid))
    return tuple(out)


def classify_era(preset_id: str | None, selected_skills_json: str | None) -> str:
    n_skills = len(_skill_ids(selected_skills_json))
    if n_skills == 1:
        return "single"
    if str(preset_id or "") == COMBO_PRESET_ID:
        return "combo"
    return "other"


def classify_fill(outcome: Any, state: Any | None) -> str:
    buy_confirmed = state is not None and bool(getattr(state, "ever_buy_confirmed", False))
    if not bool(getattr(outcome, "entry_triggered", False)):
        return "confirmed_not_filled" if buy_confirmed else "not_filled"
    return "confirmed" if buy_confirmed else "open_simulated"


def _stats(returns: list[float]) -> dict[str, Any]:
    if not returns:
        return {"n": 0, "avg_net_return": None, "median_net_return": None, "win_rate": None}
    return {
        "n": len(returns),
        "avg_net_return": round(statistics.fmean(returns), 3),
        "median_net_return": round(statistics.median(returns), 3),
        "win_rate": round(100.0 * sum(1 for r in returns if r > 0) / len(returns), 1),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """rows: {era, strategy_id, fill, net_return_pct}。按 era → 汇总 + 逐策略，三栏成交口径分开。"""
    out: dict[str, Any] = {}
    for era in ("single", "combo", "other"):
        era_rows = [r for r in rows if r["era"] == era]
        if not era_rows:
            continue

        def block(items: list[dict[str, Any]]) -> dict[str, Any]:
            return {
                "settled_count": len(items),
                "confirmed": _stats([r["net_return_pct"] for r in items if r["fill"] == "confirmed"]),
                "open_simulated": _stats([r["net_return_pct"] for r in items if r["fill"] == "open_simulated"]),
                "confirmed_not_filled_count": sum(1 for r in items if r["fill"] == "confirmed_not_filled"),
                "confirmed_not_filled": sorted(
                    f"{r['code']}({r['signal_date']})" for r in items if r["fill"] == "confirmed_not_filled"
                ),
                "not_filled_count": sum(1 for r in items if r["fill"] == "not_filled"),
            }

        strategies = sorted({r["strategy_id"] for r in era_rows})
        out[era] = {**block(era_rows),
                    "by_strategy": {sid: block([r for r in era_rows if r["strategy_id"] == sid]) for sid in strategies}}
    return out


def live_trade_breakdown(
    db: Session,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    from app.services.evidence.validation import canonical_live_forward_run_ids, catchup_formal_run_ids

    canonical = set(canonical_live_forward_run_ids(db))
    run_ids = tuple(canonical | set(catchup_formal_run_ids(db)))
    if not run_ids:
        return {"basis": _BASIS, "eras": {}}
    stmt = (
        select(Recommendation, RecommendationRun, RecommendationOutcome, RecommendationExecutionState)
        .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
        .join(RecommendationOutcome, RecommendationOutcome.recommendation_id == Recommendation.recommendation_id)
        .outerjoin(RecommendationExecutionState,
                   RecommendationExecutionState.recommendation_id == Recommendation.recommendation_id)
        .where(Recommendation.run_id.in_(run_ids))
    )
    if start_date:
        stmt = stmt.where(Recommendation.trade_date >= start_date)
    if end_date:
        stmt = stmt.where(Recommendation.trade_date <= end_date)
    rows: list[dict[str, Any]] = []
    for rec, run, outcome, state in db.execute(stmt).all():
        if str(getattr(outcome, "outcome_label", "") or "").lower() not in SETTLED_LABELS:
            continue
        fill = classify_fill(outcome, state)
        era = classify_era(run.preset_id, run.selected_skills_json)
        skills = _skill_ids(run.selected_skills_json)
        strategy_id = skills[0] if era == "single" else str(rec.bucket or "unknown")
        rows.append({
            "era": era,
            "origin": "live_forward" if str(rec.run_id) in canonical else "catchup_formal",
            "strategy_id": strategy_id,
            "code": str(rec.code),
            "signal_date": str(rec.trade_date),
            "fill": fill,
            "net_return_pct": (
                net_return_after_cost(float(outcome.executed_return or 0))
                if fill in {"confirmed", "open_simulated"} else None
            ),
        })
    return {"basis": _BASIS, "eras": summarize(rows)}


_BASIS = {
    "era": "combo=preset_formal_opening 且多技能的组合时代（已删除方案的历史成交）；single=单技能 run（不论 preset_id）；other=其它",
    "sample": "canonical live_forward + 凌晨补跑的正式 run（catchup_formal）",
    "confirmed": "盘中买入确认命中且结算成交——实盘成交",
    "open_simulated": "结算按次日开盘价模拟成交，但盘中从未确认买入——单列，不算实盘成交",
    "confirmed_not_filled": "盘中已确认买入、结算却没成交——确认与结算口径不一致，单列待查",
    "net_return_pct": "executed_return 扣统一附加成本",
}

__all__ = ["classify_era", "classify_fill", "live_trade_breakdown", "summarize"]
