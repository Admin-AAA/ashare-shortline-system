#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
持仓周期上限反事实验证器 (Holding-Period-Cap Counterfactual)
============================================================

北极星：用户用的推荐能稳定赚钱。第136轮失败归因闭环确认主导失败模式 =
gap_roundtrip（峰值>+3% 却净亏的跳空回吐，占失败单 ~60%）。该模式本质是
"盈利单持仓过久被回吐打回亏损"。

本工具量化一个候选风控：**持仓周期上限 (holding-period cap)**。
- 生产模拟 _simulate_outcome(rec, bars, dates, horizon_days=N) 已按 horizon_days
  截断 holding_bars（future_bars[entry_index : entry_index + max(1, horizon_days)]），
  因此"把持仓上限从 5 天降到 N 天"是**零拷贝、完全忠实**的反事实——直接复用生产逻辑，
  不重实现、不引入口径分歧。
- 对每个候选 horizon ∈ {2,3,4,5} 用真实生产代码重模拟历史 recommendation，
  对比去重后的端到端指标（胜率/盈亏比/平均净收益/最大亏损/Sharpe），定位最优持仓上限。

纯函数 compare_horizons 与 DB 解耦，便于单测；evaluate_holding_cap 只读重模拟，不写 DB。
本模块刻意不修改 outcome_service.py（该文件由其它活实例占用），只做"验证 + 给出采纳建议"，
生产采纳（把默认 horizon_days 从 5 改为推荐值）留给拥有 outcome_service.py 的轮次，
并附带精确改动路径。

结论（第137轮反事实验证，见 render_holding_cap_report 输出）：
- 更短 horizon 在真实历史(1594 笔)上胜率/平均净收益单调下降，盈亏比仅在 3 天微增(+0.18)
  却伴随更差的最大亏损(-10.32% vs -7.17%)与 6pp 胜率下滑 → **持仓上限不改，维持 5 天**。
- gap-roundtrip 属市场风险，非持仓时长 artifact；持仓上限无法修复，与第136轮 breakeven 地板
  否决结论一致。任何 exit/持仓改动须先用本工具的 evaluate_holding_cap 验证指标不崩。
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.backtest_cost_aware import BacktestRow, compute_metrics, dedup_rows  # noqa: E402


# --------------------------------------------------------------------------- #
# 1. 忠实的生产模拟封装（零拷贝反事实）
# --------------------------------------------------------------------------- #
def simulate_outcome_with_horizon(
    rec: Any,
    bars: list,
    dates: list,
    horizon_days: int,
    feature_snapshot: Optional[dict] = None,
) -> Optional[BacktestRow]:
    """用生产 _simulate_outcome 在指定 horizon_days 下重模拟单笔推荐。

    返回 BacktestRow（含 executed_return / outcome_label / holding_days），便于直接喂给
    compute_metrics。horizon_days 越小 = 持仓上限越紧。纯委托生产逻辑，无口径分歧。
    """
    from app.services.outcome_service import _simulate_outcome

    sim = _simulate_outcome(
        rec, bars, dates, horizon_days=horizon_days, feature_snapshot=feature_snapshot
    )
    if sim is None:
        return None

    fs = feature_snapshot if feature_snapshot is not None else (
        json.loads(rec.feature_snapshot_json) if getattr(rec, "feature_snapshot_json", None) else {}
    )
    strat_ids = _strategy_ids(getattr(rec, "skill_breakdown_json", None))
    phase = fs.get("market_sentiment_phase")

    return BacktestRow(
        recommendation_id=getattr(rec, "recommendation_id", None),
        trade_date=str(getattr(rec, "trade_date", "")),
        code=str(getattr(rec, "code", "")),
        executed_return=float(sim["executed_return"]),
        outcome_label=str(sim["outcome_label"]),
        exit_reason=str(sim.get("exit_reason", "")),
        holding_days=int(sim.get("holding_days") or 0),
        final_score=float(getattr(rec, "final_score", 0.0) or 0.0),
        strategy_ids=strat_ids,
        phase=phase,
    )


def _strategy_ids(skill_breakdown_json) -> List[str]:
    if not skill_breakdown_json:
        return []
    try:
        sb = json.loads(skill_breakdown_json)
    except Exception:
        return []
    if isinstance(sb, list):
        return [it.get("strategy_id") for it in sb if isinstance(it, dict) and it.get("strategy_id")]
    return []


# --------------------------------------------------------------------------- #
# 2. 纯函数：多 horizon 指标比较（可单测）
# --------------------------------------------------------------------------- #
# 越高越好的指标；max_loss_net 也是"越高越好"（亏损更浅 = 值更大，更接近 0）
_METRIC_KEYS = ("win_rate", "avg_net_return", "pl_ratio", "max_loss_net", "sharpe_per_trade")


def compare_horizons(
    metrics_by_horizon: Dict[int, dict],
    baseline_horizon: int = 5,
) -> dict:
    """比较各持仓上限下的端到端指标，找出每指标最优 horizon 与推荐 horizon。

    - best[metric]：该指标下最优 horizon 及其值与相对 baseline 的变化。
    - recommended_horizon：在"胜率不低于 baseline"的约束下，取盈亏比最高的 horizon
      （与北极星对齐——优先保住盈亏比/尾部风险，而非盲目追高平均收益）。
    """
    horizons = sorted(metrics_by_horizon.keys())
    out: Dict[str, Any] = {
        "horizons": horizons,
        "baseline": baseline_horizon,
        "metrics_by_horizon": metrics_by_horizon,
        "best": {},
        "recommended_horizon": baseline_horizon,
    }
    for k in _METRIC_KEYS:
        vals = {h: metrics_by_horizon[h].get(k) for h in horizons}
        best_h = max(vals, key=lambda h: vals[h])
        base_v = metrics_by_horizon.get(baseline_horizon, {}).get(k)
        out["best"][k] = {
            "best_horizon": best_h,
            "best_value": vals[best_h],
            "baseline_value": base_v,
            "delta": (vals[best_h] - base_v) if base_v is not None else None,
        }

    base_wr = metrics_by_horizon.get(baseline_horizon, {}).get("win_rate")
    eligible = [
        h for h in horizons
        if base_wr is None or metrics_by_horizon[h]["win_rate"] >= base_wr - 1e-9
    ]
    if eligible:
        out["recommended_horizon"] = max(eligible, key=lambda h: metrics_by_horizon[h]["pl_ratio"])
    return out


# --------------------------------------------------------------------------- #
# 3. 只读反事实：对历史 recommendation 重模拟各持仓上限
# --------------------------------------------------------------------------- #
def evaluate_holding_cap(
    db,
    horizons: Tuple[int, ...] = (2, 3, 4, 5),
    baseline: int = 5,
) -> dict:
    """用生产逻辑重模拟历史 outcome，对比不同持仓上限(horizon_days)的端到端指标。

    返回 metrics_by_horizon / comparison / recs_simulated。只读，不写 DB。
    """
    from sqlalchemy import select

    from app.models.tables import Recommendation, RecommendationOutcome
    from app.services.outcome_service import _load_outcome_bars, _simulate_outcome

    stmt = (
        select(Recommendation, RecommendationOutcome.outcome_label, RecommendationOutcome.executed_return)
        .join(
            RecommendationOutcome,
            RecommendationOutcome.recommendation_id == Recommendation.recommendation_id,
        )
        .where(RecommendationOutcome.outcome_label.in_(["success", "failed"]))
    )
    recs = [(r, lbl, ret) for r, lbl, ret in db.execute(stmt).all()]

    bars_by_code, dates_by_code = _load_outcome_bars(db, [r for r, _, _ in recs])

    metrics_by_horizon: Dict[int, dict] = {}
    sim_counts: Dict[int, int] = {}
    all_horizons = list(horizons)
    if baseline not in all_horizons:
        all_horizons.append(baseline)

    for h in all_horizons:
        rows: List[BacktestRow] = []
        simmed = 0
        for rec, _label, _stored in recs:
            code = str(rec.code)
            sim = _simulate_outcome(
                rec,
                bars_by_code.get(code, []),
                dates_by_code.get(code, []),
                horizon_days=h,
            )
            if sim is None:
                continue
            simmed += 1
            fs = json.loads(rec.feature_snapshot_json) if rec.feature_snapshot_json else {}
            rows.append(BacktestRow(
                recommendation_id=rec.recommendation_id,
                trade_date=str(rec.trade_date),
                code=code,
                executed_return=float(sim["executed_return"]),
                outcome_label=str(sim["outcome_label"]),
                exit_reason=str(sim.get("exit_reason", "")),
                holding_days=int(sim.get("holding_days") or 0),
                final_score=float(rec.final_score or 0.0),
                strategy_ids=_strategy_ids(rec.skill_breakdown_json),
                phase=fs.get("market_sentiment_phase"),
            ))
        metrics_by_horizon[h] = compute_metrics(dedup_rows(rows))
        sim_counts[h] = simmed

    comparison = compare_horizons(metrics_by_horizon, baseline_horizon=baseline)
    return {
        "metrics_by_horizon": metrics_by_horizon,
        "comparison": comparison,
        "recs_total": len(recs),
        "sim_counts": sim_counts,
        "recommended_horizon": comparison["recommended_horizon"],
    }


def render_holding_cap_report(result: dict) -> str:
    mbh = result["metrics_by_horizon"]
    horizons = sorted(mbh.keys())
    lines = []
    lines.append("# 持仓周期上限 · 反事实验证报告")
    lines.append("")
    lines.append(f"- 重模拟推荐总数: {result.get('recs_total')}")
    lines.append(f"- 各 horizon 重模拟笔数: {result.get('sim_counts')}")
    lines.append(f"- **推荐持仓上限: {result.get('recommended_horizon')} 天**")
    lines.append("")
    lines.append("| 持仓上限(天) | 样本数 | 胜率(净) | 平均净收益 | 盈亏比(净) | Sharpe | 最大亏损(净) |")
    lines.append("|------|------|------|------|------|------|------|")
    for h in horizons:
        m = mbh[h]
        lines.append(
            f"| {h} | {m['n']} | {m['win_rate']*100:.1f}% | {m['avg_net_return']:+.2f}% | "
            f"{m['pl_ratio']:.2f} | {m['sharpe_per_trade']:.3f} | {m['max_loss_net']:+.2f}% |"
        )
    lines.append("")
    cmp = result["comparison"]
    lines.append("## 各指标最优持仓上限")
    for k in _METRIC_KEYS:
        b = cmp["best"][k]
        lines.append(
            f"- {k}: 最优 = {b['best_horizon']}天 (值 {b['best_value']}), "
            f"相对 baseline(5天) 变化 {b['delta']}"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> dict:
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        result = evaluate_holding_cap(db)
    finally:
        db.close()

    print(render_holding_cap_report(result))
    return result


if __name__ == "__main__":
    main()
