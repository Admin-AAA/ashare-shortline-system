"""
验证脚本：直接调用 backtest_service.run_backtest 确认产出交易明细。

用法:
  cd backend && python -m scripts.verify_backtest_detail

输出:
  - 回测状态、总推荐数、交易明细笔数
  - 前 5 笔明细摘要
  - 字段完整性检查
"""

import sys
from pathlib import Path

# 确保 backend 目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import SessionLocal
from app.services.backtest_service import run_backtest


EXPECTED_FIELDS = {
    "recommendation_id", "trade_date", "code", "name", "industry",
    "entry_date", "entry_price", "exit_date", "exit_price",
    "net_return_pct", "gross_return_pct", "achievable_return_pct",
    "benchmark_return_pct", "morning_drawdown_pct", "holding_days",
    "setup_type", "entry_rule", "exit_reason", "final_score",
    "accurate", "false_positive_reason", "signal_features", "resolved",
}


def main():
    db = SessionLocal()
    try:
        # 回测近期日期
        result = run_backtest(
            db,
            start_date="2026-05-01",
            end_date="2026-05-20",
            run_mode="experiment",
            horizon_days=5,
            score_line=72.0,
        )

        print(f"回测状态: {result['status']}")
        print(f"扫描日期数: {result['dates_scanned']}")
        print(f"总推荐数: {result['total_recommendations']}")

        trade_details = result.get("trade_details", [])
        print(f"\n交易明细笔数: {len(trade_details)}")
        print(f"trade_detail_count: {result.get('trade_detail_count', 0)}")

        if not trade_details:
            print("\n⚠️ 无交易明细（可能是数据不足或评分线过高）")
            print("尝试放宽评分线...")
            # 重试更低评分线
            result2 = run_backtest(
                db,
                start_date="2026-05-01",
                end_date="2026-05-20",
                run_mode="experiment",
                horizon_days=5,
                score_line=60.0,
            )
            trade_details = result2.get("trade_details", [])
            print(f"放宽后交易明细笔数: {len(trade_details)}")

        if trade_details:
            # 字段完整性检查
            missing_fields = EXPECTED_FIELDS - set(trade_details[0].keys())
            extra_fields = set(trade_details[0].keys()) - EXPECTED_FIELDS
            if missing_fields:
                print(f"\n⚠️ 缺少字段: {missing_fields}")
            else:
                print("\n✓ 字段完整性验证通过")
            if extra_fields:
                print(f"  额外字段: {extra_fields}")

            print(f"\n前 {min(5, len(trade_details))} 笔明细:")
            for i, td in enumerate(trade_details[:5]):
                print(f"  [{i+1}] {td['trade_date']} {td['code']} {td['name']} | "
                      f"入场:{td['entry_price']:.2f} 出场:{td['exit_price']:.2f} | "
                      f"收益:{td['net_return_pct']:.2f}% | "
                      f"买点:{td['setup_type']} | "
                      f"误判:{td['false_positive_reason'] or '-'}")
        else:
            print("\n⚠️ 放宽后仍无交易明细")
    finally:
        db.close()


if __name__ == "__main__":
    main()
