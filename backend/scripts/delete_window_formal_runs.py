#!/usr/bin/env python3
"""删除窗口内所有 formal run 及其关联数据（清空重放目标区）。

复用 delete_run 的完整级联（推荐/outcome/execution_states/review_notes/watch_items/
agent_decision_logs），只删窗口内 trade_date 的 formal run。
不碰 experiment / backtest run、不碰窗口外的正式 run。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import select  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.tables import RecommendationRun  # noqa: E402
from app.services.workbench_service import delete_run  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2026-05-22")
    parser.add_argument("--end", default="2026-07-29")
    parser.add_argument("--dry-run", action="store_true", help="只列出不删除")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        run_ids = db.scalars(
            select(RecommendationRun.run_id).where(
                RecommendationRun.trade_date.between(args.start, args.end),
                RecommendationRun.run_mode == "formal",
            )
        ).all()
        print(f"窗口 {args.start}~{args.end} 内 formal run: {len(run_ids)} 个")
        if args.dry_run:
            return 0

        deleted = 0
        for rid in run_ids:
            ok = delete_run(db, str(rid))
            if ok:
                deleted += 1
            else:
                print(f"  ! 删除失败: {rid}")
        print(f"已删除 {deleted} 个 run")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
