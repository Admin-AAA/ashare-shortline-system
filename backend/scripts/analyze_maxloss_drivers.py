"""T3 诊断：D 项最大亏损的真实驱动因子。

目的：验证「最大亏损由 20cm（创业板/科创板）单票跳空穿透止损导致」的假设，
      对比 20cm 与 10cm 票在正式推荐 outcome 中的亏损分布、胜率、最大亏损贡献。

口径（遵循 self_check D 项规则）：
- 仅统计 run_mode='formal' 的 recommendation（LIVE 总体，排除 experiment/backtest）。
- 按 recommendation_id 去重，区分 LIVE(rec_*) 与 BACKTEST(btrec_*)。
- 20cm 判定：code 以 300/301(创业板) 或 688/689(科创板) 开头。

输出：分组（20cm / 10cm / 全部）的推荐数、胜率、平均收益、盈亏比、最大亏损、
      以及「最差 N 笔亏损」中 20cm 占比。
"""
from __future__ import annotations

import sys
from collections import defaultdict

from app.db.session import SessionLocal
from app.models.tables import Recommendation, RecommendationOutcome, RecommendationRun


def is_20cm(code: str) -> bool:
    return code.startswith(("300", "301", "688", "689"))


def main() -> int:
    db = SessionLocal()
    try:
        # LIVE formal outcomes，按 recommendation_id 去重
        # 仅取已结算标签（success/failed），与 self_check D 项口径一致；
        # executed_return 单位为「百分比」（如 -5.88 即 -5.88%），不再 *100。
        rows = (
            db.query(
                RecommendationOutcome.recommendation_id,
                RecommendationOutcome.executed_return,
                RecommendationOutcome.outcome_label,
                RecommendationOutcome.exit_reason,
                RecommendationOutcome.max_drawdown,
                Recommendation.code,
                Recommendation.trade_date,
            )
            .join(Recommendation, Recommendation.recommendation_id == RecommendationOutcome.recommendation_id)
            .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
            .filter(RecommendationRun.run_mode == "formal")
            .filter(RecommendationOutcome.outcome_label.in_(["success", "failed"]))
            .all()
        )
    finally:
        db.close()

    if not rows:
        print("无 formal outcome 数据")
        return 0

    groups: dict[str, list] = defaultdict(list)
    for r in rows:
        key = "20cm" if is_20cm(r.code) else "10cm"
        groups[key].append(r)

    def summarize(label: str, items: list) -> None:
        n = len(items)
        if n == 0:
            print(f"\n[{label}] 样本=0")
            return
        wins = sum(1 for x in items if x.outcome_label == "success")
        losses = [x.executed_return for x in items if x.executed_return < 0]
        gains = [x.executed_return for x in items if x.executed_return > 0]
        avg = sum(x.executed_return for x in items) / n
        max_loss = min((x.executed_return for x in items), default=0)
        avg_loss = sum(losses) / len(losses) if losses else 0.0
        avg_gain = sum(gains) / len(gains) if gains else 0.0
        pl = (avg_gain / abs(avg_loss)) if avg_loss < 0 else float("inf")
        print(f"\n[{label}] 样本={n}")
        print(f"  胜率={wins / n * 100:.1f}% ({wins}/{n})")
        print(f"  平均收益={avg:.2f}%")
        print(f"  盈亏比(均盈/均亏)={pl:.2f}")
        print(f"  最大亏损={max_loss:.2f}%")
        print(f"  亏损笔数={len(losses)} 平均亏损={avg_loss:.2f}%")

    summarize("全部", rows)
    summarize("20cm(创业板/科创板)", groups["20cm"])
    summarize("10cm(主板/中小板)", groups["10cm"])

    # 最差 N 笔亏损中 20cm 占比
    all_losses = sorted(rows, key=lambda x: x.executed_return)
    worst = all_losses[:15]
    worst_20 = sum(1 for x in worst if is_20cm(x.code))
    print(f"\n[最差 15 笔亏损] 20cm 占比={worst_20}/15 = {worst_20 / 15 * 100:.0f}%")
    print("  明细(code / return% / exit_reason / trade_date):")
    for x in worst:
        print(f"    {x.code} {x.executed_return:+.2f}% {x.exit_reason} {x.trade_date}")

    # 按 exit_reason 统计 20cm vs 10cm 的止损穿透
    stop = [x for x in rows if x.exit_reason == "stop_loss"]
    stop_20 = sum(1 for x in stop if is_20cm(x.code))
    print(f"\n[止损离场] 总={len(stop)} 其中20cm={stop_20} ({stop_20 / max(1, len(stop)) * 100:.0f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
