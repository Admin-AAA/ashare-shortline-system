#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
失败归因闭环分析器 (Failure-Attribution Closed Loop)
====================================================

北极星：用户用的推荐能稳定赚钱。失败不是终点，而是改进的原料。

本工具把"失败 → 归因 → 规则调整 → 验证"闭环变成可复用、可量化的工程：

1. 失败归因 (classify_failure_mode)：把每笔 failed 推荐映射到具体失败模式
   （跳空回吐 / 追高 / 弱势股 / 弱形态 / 尾部跳空 / 其他），定位主导模式。
2. 反事实验证 (run_counterfactual)：用当前模块的真实模拟逻辑（outcome_service._simulate_outcome）
   对历史 recommendation 重新模拟，对比「改动前(库内 outcome)」与「改动后(新逻辑重模拟)」
   的端到端指标（胜率/盈亏比/最大亏损/出场分布）。**只读，不写 DB**——改动效果用真实生产
   代码在历史数据上证明，而非只跑通测试。

纯函数 classify_failure_mode 与指标比较函数与 DB 解耦，便于单元测试。

结论（第136轮反事实验证）：
- 主导失败模式 = gap_roundtrip（峰值>+3% 却净亏的跳空回吐），占失败单 ~60%。
- 候选修复「trailing 激活后加保本地板」(max(trigger,地板价)) 经反事实重模拟被否决：
  flat +0.5% 地板 → 盈亏比 1.98→0.88、棘轮地板 → 1.98→1.09（均崩），且最大亏损不变(-7.17%)。
  根因：trigger_px=min(open,触发价)≤地板价恒成立，max 等效把「所有 trailing 退出」锁在地板价，
  即 max() 高估（代码既有先例：86 笔虚假 100% 胜率）。gap-roundtrip 属市场风险，不靠 exit 地板修复。
- 因此本模块只做归因+反事实验证，不引入 exit 地板；未来任何 exit 改动须先用 run_counterfactual 验证 P/L 不崩。
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.backtest_cost_aware import BacktestRow, compute_metrics, dedup_rows  # noqa: E402

# 历史回测中净胜率显著偏低的 setup（样本仍小，记录待积累；不作为硬剔除依据）
WEAK_SETUPS = {
    "分歧回封接力",
    "放量突破",
    "阶段高点蓄势观察",
}


# --------------------------------------------------------------------------- #
# 1. 失败归因（纯函数，可单测）
# --------------------------------------------------------------------------- #
def classify_failure_mode(
    *,
    exit_reason: str,
    executed_return: Optional[float],
    max_return: Optional[float],
    rsi_12: Optional[float],
    chg20: Optional[float],
    pos60: Optional[float],
    setup_type: Optional[str],
) -> str:
    """把一笔 failed 推荐映射到主导失败模式。

    优先级（从最可操作的尾部风险到最模糊的）：
      1. tail_gap      —— 跳空止损（开盘远低于止损价，极端尾部）
      2. gap_roundtrip —— trailing 激活后峰值>+3% 却净亏：盈利单被跳空回吐打回亏损（本轮主因）
      3. bad_setup     —— 历史净胜率显著偏低的形态
      4. weak_stock    —— 弱势股（RSI 极低）反而失败更多（反直觉，数据驱动）
      5. extension_chase —— 追高（chg20/pos60 极高）
      6. other         —— 其余
    """
    if exit_reason == "stop_loss_gap":
        return "tail_gap"
    if (
        exit_reason in ("trailing_stop_hit", "stop_loss_hit")
        and (max_return is not None and max_return > 3.0)
        and (executed_return is not None and executed_return < 0.0)
    ):
        return "gap_roundtrip"
    if setup_type in WEAK_SETUPS:
        return "bad_setup"
    if rsi_12 is not None and rsi_12 < 45:
        return "weak_stock"
    if (chg20 is not None and chg20 > 50) or (pos60 is not None and pos60 > 90):
        return "extension_chase"
    return "other"


@dataclass
class AttributionResult:
    mode_counts: Dict[str, int]
    mode_failed_return: Dict[str, float] = field(default_factory=dict)
    total_failed: int = 0

    def dominant_mode(self) -> Optional[str]:
        if not self.mode_counts:
            return None
        return max(self.mode_counts, key=lambda m: self.mode_counts[m])


def attribute_failures(fields_list: List[Dict[str, Any]]) -> AttributionResult:
    """对一批 failed 推荐做模式统计。fields_list 每项含 classify_failure_mode 所需字段。"""
    counts: Dict[str, int] = defaultdict(int)
    rets: Dict[str, List[float]] = defaultdict(list)
    for f in fields_list:
        mode = classify_failure_mode(
            exit_reason=f.get("exit_reason", ""),
            executed_return=f.get("executed_return"),
            max_return=f.get("max_return"),
            rsi_12=f.get("rsi_12"),
            chg20=f.get("chg20"),
            pos60=f.get("pos60"),
            setup_type=f.get("setup_type"),
        )
        counts[mode] += 1
        if f.get("executed_return") is not None:
            rets[mode].append(float(f["executed_return"]))
    avg = {m: round(sum(v) / len(v), 2) if v else 0.0 for m, v in rets.items()}
    return AttributionResult(mode_counts=dict(counts), mode_failed_return=avg, total_failed=len(fields_list))


# --------------------------------------------------------------------------- #
# 2. 反事实验证（只读，用真实生产模拟逻辑重模拟历史）
# --------------------------------------------------------------------------- #
def _load_recs_with_outcomes(db) -> List[Tuple[Any, str, float]]:
    """返回 [(Recommendation, outcome_label, stored_executed_return), ...]。"""
    from sqlalchemy import select

    from app.models.tables import Recommendation, RecommendationOutcome

    stmt = (
        select(Recommendation, RecommendationOutcome.outcome_label, RecommendationOutcome.executed_return)
        .join(
            RecommendationOutcome,
            RecommendationOutcome.recommendation_id == Recommendation.recommendation_id,
        )
        .where(RecommendationOutcome.outcome_label.in_(["success", "failed"]))
    )
    return [(r, lbl, ret) for r, lbl, ret in db.execute(stmt).all()]


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


def run_counterfactual(db, horizon_days: int = 5) -> Dict[str, Any]:
    """用当前模块逻辑重模拟历史 outcome，对比改动前(库内)与改动后(新逻辑)的端到端指标。

    返回 before/after 指标、翻转统计、被保本地板拯救的笔数。
    """
    from app.services.outcome_service import _load_outcome_bars, _simulate_outcome

    recs = _load_recs_with_outcomes(db)
    bars_by_code, dates_by_code = _load_outcome_bars(db, [r for r, _, _ in recs])

    before_rows: List[BacktestRow] = []
    after_rows: List[BacktestRow] = []
    flipped: Dict[str, int] = Counter()
    floor_rescued = 0
    floor_rescued_return = 0.0

    for rec, label, stored_ret in recs:
        code = str(rec.code)
        fs = json.loads(rec.feature_snapshot_json) if rec.feature_snapshot_json else {}
        phase = fs.get("market_sentiment_phase")
        strat_ids = _strategy_ids(rec.skill_breakdown_json)
        score = float(rec.final_score or 0.0)
        tdate = str(rec.trade_date)

        before_rows.append(BacktestRow(
            recommendation_id=rec.recommendation_id, trade_date=tdate, code=code,
            executed_return=float(stored_ret or 0.0), outcome_label=label or "",
            exit_reason="", holding_days=0, final_score=score,
            strategy_ids=strat_ids, phase=phase,
        ))

        sim = _simulate_outcome(
            rec,
            bars_by_code.get(code, []),
            dates_by_code.get(code, []),
            horizon_days=horizon_days,
        )
        if sim is None:
            continue
        after_ret = float(sim["executed_return"])
        after_label = str(sim["outcome_label"])
        after_rows.append(BacktestRow(
            recommendation_id=rec.recommendation_id, trade_date=tdate, code=code,
            executed_return=after_ret, outcome_label=after_label,
            exit_reason=str(sim.get("exit_reason", "")), holding_days=int(sim.get("holding_days") or 0),
            final_score=score, strategy_ids=strat_ids, phase=phase,
        ))
        if label != after_label:
            flipped[f"{label}->{after_label}"] += 1
        # 保本地板拯救：改动后由亏转盈（原净亏，新模拟净收益转正）
        if label == "failed" and after_label == "success":
            floor_rescued += 1
            floor_rescued_return += after_ret

    before_metrics = compute_metrics(dedup_rows(before_rows))
    after_metrics = compute_metrics(dedup_rows(after_rows))

    return {
        "before": before_metrics,
        "after": after_metrics,
        "flipped": dict(flipped),
        "floor_rescued": floor_rescued,
        "floor_rescued_return": round(floor_rescued_return, 2),
        "recs_simulated": len(after_rows),
    }


def render_counterfactual_report(result: Dict[str, Any]) -> str:
    b, a = result["before"], result["after"]
    lines = []
    lines.append("# 失败归因闭环 · 反事实验证报告")
    lines.append("")
    lines.append(f"- 重模拟推荐数: {result['recs_simulated']}")
    lines.append(f"- 保本地板拯救(亏→盈)笔数: {result['floor_rescued']} (合计净收益 +{result['floor_rescued_return']:.2f}%)")
    lines.append(f"- 标签翻转: {result['flipped']}")
    lines.append("")
    lines.append("| 指标 | 改动前 | 改动后 | 变化 |")
    lines.append("|------|--------|--------|------|")
    lines.append(f"| 去重样本数 | {b['n']} | {a['n']} | — |")
    lines.append(f"| 胜率(净) | {b['win_rate']*100:.1f}% | {a['win_rate']*100:.1f}% | {(a['win_rate']-b['win_rate'])*100:+.1f}pp |")
    lines.append(f"| 平均净收益 | {b['avg_net_return']:+.2f}% | {a['avg_net_return']:+.2f}% | {a['avg_net_return']-b['avg_net_return']:+.2f}pp |")
    lines.append(f"| 盈亏比(净) | {b['pl_ratio']:.2f} | {a['pl_ratio']:.2f} | {a['pl_ratio']-b['pl_ratio']:+.2f} |")
    lines.append(f"| Sharpe(每笔) | {b['sharpe_per_trade']:.3f} | {a['sharpe_per_trade']:.3f} | {a['sharpe_per_trade']-b['sharpe_per_trade']:+.3f} |")
    lines.append(f"| 最大亏损(净) | {b['max_loss_net']:+.2f}% | {a['max_loss_net']:+.2f}% | {a['max_loss_net']-b['max_loss_net']:+.2f}pp |")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 3. 失败模式统计（用于日志/报告，独立于反事实）
# --------------------------------------------------------------------------- #
def collect_failure_fields(db) -> List[Dict[str, Any]]:
    from sqlalchemy import text

    sql = text("""
        SELECT o.exit_reason, o.executed_return, o.max_return,
               r.final_score, r.setup_type, r.feature_snapshot_json
        FROM recommendation_outcomes o
        JOIN recommendations r ON o.recommendation_id = r.recommendation_id
        WHERE o.outcome_label = 'failed'
    """)
    out: List[Dict[str, Any]] = []
    for row in db.execute(sql).fetchall():
        reason, ret, mx, score, setup, fs_json = row
        fs = json.loads(fs_json) if fs_json else {}
        out.append({
            "exit_reason": reason or "",
            "executed_return": float(ret) if ret is not None else None,
            "max_return": float(mx) if mx is not None else None,
            "rsi_12": _to_float(fs.get("rsi_12")),
            "chg20": _to_float(fs.get("chg20")),
            "pos60": _to_float(fs.get("pos60")),
            "setup_type": setup,
        })
    return out


def _to_float(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except Exception:
        return None


def main() -> Dict[str, Any]:
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        # 失败模式归因
        fields = collect_failure_fields(db)
        attr = attribute_failures(fields)
        # 反事实验证
        cf = run_counterfactual(db)
    finally:
        db.close()

    print("## 失败模式分布")
    for mode, cnt in sorted(attr.mode_counts.items(), key=lambda kv: -kv[1]):
        avg = attr.mode_failed_return.get(mode, 0.0)
        print(f"- {mode}: {cnt} 笔 (avgRet={avg:+.2f}%)")
    print(f"\n主导失败模式: {attr.dominant_mode()} (共 {attr.total_failed} 笔失败)")
    print()
    print(render_counterfactual_report(cf))
    return {"attribution": attr.mode_counts, "counterfactual": cf}


if __name__ == "__main__":
    main()
