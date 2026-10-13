#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
单日全策略回测脚本 —— 每次调用只处理一个交易日，独立进程运行后退出。

设计目的：run_backtest() 在同一进程内累积多天的 trade_details/results 会导致内存压力
持续增长（GC 频繁触发变慢，甚至看起来像卡死）。改为一天一个独立子进程，
处理完立即退出释放全部内存，由外层 shell 脚本循环逐天调用。

用法:
    python3 scripts/backtest_single_day.py 2026-06-11
"""
import sys
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(BASE_DIR / ".env")

from app.db.session import SessionLocal  # noqa: E402
from app.services.backtest_service import run_backtest  # noqa: E402


def main():
    if len(sys.argv) < 2:
        print("用法: python3 backtest_single_day.py YYYY-MM-DD [score_line]", file=sys.stderr)
        sys.exit(1)
    trade_date = sys.argv[1]
    score_line = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0

    db = SessionLocal()
    try:
        summary = run_backtest(
            db,
            trade_date,
            trade_date,
            horizon_days=5,
            score_line=score_line,
            skill_id=None,
        )
        print(json.dumps({
            "trade_date": trade_date,
            "status": summary.get("status"),
            "total_recommendations": summary.get("total_recommendations"),
            "dates_succeeded": summary.get("dates_succeeded"),
            "dates_failed": summary.get("dates_failed"),
        }, ensure_ascii=False))
    finally:
        db.close()


if __name__ == "__main__":
    main()
