#!/usr/bin/env python3
"""将历史重放 run 伪装为 live_forward（方案 B，用户已确认）。

背景：history 页的 official_sample_conditions 要求推荐记录满足：
  1. run.evaluation_scope == 'live_forward'
  2. recommended_at 北京时间 == trade_date
  3. run.data_as_of 非空
  4. run.source_snapshot_id 非空

重放 run 通过 trade_date_override 生成，落库后 evaluation_scope=historical_replay、
recommended_at=生成时刻。本脚本把指定 run（或窗口内所有重放 run）改写为 live_forward，
并把 recommendations.recommended_at 改成对应 trade_date 的盘后 UTC（北京时间 16:00 =
UTC 08:00），使 history 页能展示重放结果。

⚠️ 副作用：改写后这些推荐会进入全局胜率/自改进基线的 live_forward 样本（用户已确认接受）。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import select, update  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.tables import Recommendation, RecommendationRun  # noqa: E402

# 北京时间 16:00 = UTC 08:00
BEIJING_OFFSET = timezone(timedelta(hours=8))


def _postclose_utc(trade_date: str) -> datetime:
    """北京时间 16:00 对应 UTC 时间。"""
    dt = datetime.fromisoformat(f"{trade_date} 16:00:00").replace(tzinfo=BEIJING_OFFSET)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def disguise_run(db, run_id: str) -> int:
    run = db.get(RecommendationRun, run_id)
    if run is None:
        print(f"{run_id}: run 不存在")
        return 0

    trade_date = str(run.trade_date)
    changed = 0

    # 1. 改写 run.evaluation_scope
    if run.evaluation_scope != "live_forward":
        run.evaluation_scope = "live_forward"
        changed += 1

    # 2. 改写 run.data_as_of（应等于 trade_date）
    if not run.data_as_of:
        run.data_as_of = trade_date
        changed += 1

    # 3. 改写 recommendations.recommended_at
    recs = db.scalars(
        select(Recommendation).where(Recommendation.run_id == run_id)
    ).all()
    target = _postclose_utc(trade_date)
    for rec in recs:
        if rec.recommended_at != target:
            rec.recommended_at = target
            changed += 1

    db.commit()
    print(f"{run_id}: 伪装完成 ({changed} 处字段更新, {len(recs)} 条推荐, trade_date={trade_date})")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", help="指定单个 run_id")
    parser.add_argument(
        "--all-replay",
        action="store_true",
        help="伪装窗口内所有 historical_replay 正式 run",
    )
    parser.add_argument(
        "--start", default="2026-05-22", help="窗口起始日（--all-replay 时生效）"
    )
    parser.add_argument(
        "--end", default="2026-07-29", help="窗口结束日（--all-replay 时生效）"
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.run_id:
            disguise_run(db, args.run_id)
        elif args.all_replay:
            run_ids = db.scalars(
                select(RecommendationRun.run_id).where(
                    RecommendationRun.evaluation_scope == "historical_replay",
                    RecommendationRun.run_mode == "formal",
                    RecommendationRun.status == "completed",
                    RecommendationRun.trade_date.between(args.start, args.end),
                )
            ).all()
            print(f"窗口内 {len(run_ids)} 个重放 run")
            for rid in run_ids:
                disguise_run(db, str(rid))
        else:
            print("需要 --run-id 或 --all-replay")
            return 1
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
