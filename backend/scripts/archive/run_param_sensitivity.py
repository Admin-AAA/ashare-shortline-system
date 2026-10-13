"""
隔夜持股法参数敏感性分析。

运行 baseline + V1 + V2 三组参数，区间 2026-05-15 ~ 2026-05-24。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import logging
from datetime import date

from app.db.session import SessionLocal
from app.services.overnight_holding_service import (
    run_overnight_param_sensitivity,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("param_sensitivity")


def main():
    start = "2026-05-14"
    end = "2026-05-25"
    logger.info("参数敏感性分析: 11 组参数 (baseline + V1~V10), 区间 %s ~ %s", start, end)

    db = SessionLocal()
    try:
        # 使用服务模块内置的 11 组参数网格（含 baseline + V1~V10）
        result = run_overnight_param_sensitivity(db, start, end)
    finally:
        db.close()

    # ── 输出报告 ──
    print("\n" + "=" * 80)
    print(f"参数敏感性分析报告: 隔夜持股法")
    print(f"区间: {start} ~ {end}")
    print(f"参数组合: {result.get('combination_count', '?')} 组")
    print(f"成功: {result.get('success_count', '?')} 组, 失败: {result.get('error_count', '?')} 组")
    print("=" * 80)

    status = result.get("status", "unknown")
    print(f"\n状态: {status}")

    ranking = result.get("ranking", [])
    print(f"\n排名: {len(ranking)} 个参数组合进入排名\n")
    for entry in ranking:
        print(f"  #{entry['rank']} {entry['label']}")
        wf_acc = entry.get("walk_forward_accuracy")
        if wf_acc is not None:
            print(f"     样本外准确率: {wf_acc:.1%}")
        print(f"     全量准确率:    {entry.get('accuracy', 0):.1%}")
        print(f"     平均收益率:    {entry.get('avg_return', 0):.2%}")
        wf_ret = entry.get("walk_forward_avg_return")
        if wf_ret is not None:
            print(f"     样本外收益率:  {wf_ret:.2%}")
        print(f"     交易笔数:      {entry.get('trade_count', 0)}")
        print(f"     最大回撤:      {entry.get('max_drawdown', 0):.2%}")
        print()

    rec = result.get("recommendation", {})
    print(f"推荐结果:")
    print(f"  最优选择:   {rec.get('best_pick')}")
    print(f"  基线标签:   {rec.get('baseline_label')}")
    print(f"  理由:       {rec.get('adoption_reason')}")
    print(f"  Top 5:      {rec.get('top_5')}")
    print()

    notes = result.get("notes", [])
    for note in notes:
        print(f"  备注: {note}")
    print()

    errors = result.get("errors", [])
    if errors:
        print(f"错误 ({len(errors)} 个):")
        for err in errors:
            print(f"  - {err.get('label')}: {err.get('message', err.get('error', '未知'))}")
        print()

    # 输出所有结果的完整指标
    all_results = result.get("results", [])
    print(f"\n所有 {len(all_results)} 组结果明细:")
    print("-" * 80)
    for r in all_results:
        print(f"  [{r['label']}]")
        print(f"    交易笔数: {r.get('trade_count', 0)}  |  准确率: {r.get('accuracy', 0):.1%}")
        print(f"    平均收益: {r.get('avg_return', 0):.2%}  |  盈亏比: {r.get('profit_loss_ratio', 0):.2f}")
        print(f"    最大回撤: {r.get('max_drawdown', 0):.2%}  |  超额收益: {r.get('excess_return', 0):.2%}")
        print(f"    样本外笔数: {r.get('walk_forward_trade_count', 0)}")
        print(f"    置信度: {r.get('sample_confidence', '?')}")
        wf_acc = r.get("walk_forward_accuracy")
        if wf_acc is not None:
            wf_ret = r.get("walk_forward_avg_return", 0)
            print(f"    样本外准确率: {wf_acc:.1%}  |  样本外收益: {wf_ret:.2%}")
        print()


if __name__ == "__main__":
    main()
