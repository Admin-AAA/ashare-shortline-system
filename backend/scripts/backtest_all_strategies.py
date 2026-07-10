#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全策略历史回测脚本
==================

对全部 enabled 策略在指定历史区间跑一次 run_backtest（每天推荐管线跑一次，
覆盖全部 11 个策略，不是逐策略重复跑），产出 recommendations 带 skill_breakdown_json，
再用 outcome_service.metrics_by_strategy 统计各策略胜率。

直接调用 backend 内部函数（不走 HTTP），避免长耗时请求超时/阻塞 uvicorn worker。

用法:
    cd backend && python3 scripts/backtest_all_strategies.py --start 2026-05-11 --end 2026-07-03
"""
import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(BASE_DIR / ".env")

from app.db.session import SessionLocal  # noqa: E402
from app.services.backtest_service import run_backtest  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--horizon-days", type=int, default=5)
    parser.add_argument("--score-line", type=float, default=60.0)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        print(f"回测区间: {args.start} ~ {args.end} horizon={args.horizon_days} score_line={args.score_line}")
        summary = run_backtest(
            db,
            args.start,
            args.end,
            horizon_days=args.horizon_days,
            score_line=args.score_line,
            skill_id=None,  # None -> 全部 enabled 策略同时跑
        )
        print(json.dumps({
            "status": summary.get("status"),
            "dates_scanned": summary.get("dates_scanned"),
            "dates_succeeded": summary.get("dates_succeeded"),
            "dates_failed": summary.get("dates_failed"),
            "total_recommendations": summary.get("total_recommendations"),
            "labeled_count": summary.get("labeled_count"),
        }, ensure_ascii=False, indent=2))

        # 打印失败/跳过的日期原因，便于诊断
        skipped = [r for r in summary.get("results", []) if r.get("status") != "ok"]
        if skipped:
            print(f"\n跳过/失败 {len(skipped)} 天:")
            for r in skipped[:20]:
                print(f"  {r.get('trade_date')}: {r.get('status')} - {r.get('reason', r.get('error', ''))}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
