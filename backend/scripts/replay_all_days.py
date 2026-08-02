#!/usr/bin/env python3
"""全量逐日重放驱动器：对窗口内每个交易日跑一次正式推荐 run。

流程（每交易日）：
  1. enqueue run（preset_formal_opening + trade_date_override + skip_aux_refresh）
  2. dispatch（同步 Prefect 派发）
  3. 轮询等待完成（completed/failed/interrupted）
  4. 完成后 disguise 为 live_forward（方案 B）

用法：python scripts/replay_all_days.py [--start 2026-05-22] [--end 2026-07-29]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# 关键：不设 PREFECT_API_URL 时 run_deployment 会启动临时 server，
# 任务派发到临时 server 而非主 worker。显式指向主 server。
os.environ.setdefault("PREFECT_API_URL", "http://127.0.0.1:4200/api")

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import select, text  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402

POLL_SECONDS = 40
MAX_WAIT_LOOPS = 20  # 20 * 40s = 13.3 min per day


def _trade_dates(db, start: str, end: str) -> list[str]:
    rows = db.execute(
        text(
            "SELECT DISTINCT trade_date FROM daily_kbars "
            "WHERE trade_date BETWEEN :s AND :e ORDER BY trade_date"
        ),
        {"s": start, "e": end},
    ).scalars()
    return [str(d) for d in rows]


def _base_payload() -> dict:
    """从最近一个正式 run 复制完整 payload（13 策略 + 参数），只改 trade_date。"""
    from app.models.tables import RecommendationRun

    db = SessionLocal()
    try:
        row = db.execute(
            text(
                "SELECT params_json FROM recommendation_runs "
                "WHERE run_mode='formal' AND evaluation_scope='live_forward' AND status='completed' "
                "ORDER BY started_at DESC LIMIT 1"
            )
        ).scalar()
        params = json.loads(row)
        params["run_name"] = "REPLAY-全量重放"
        params["preset_id"] = "preset_formal_opening"
        params["trade_date_override"] = None  # 每日单独设
        return params
    finally:
        db.close()


def _wait_run(db, run_id: str) -> str:
    for _ in range(MAX_WAIT_LOOPS):
        time.sleep(POLL_SECONDS)
        # MySQL REPEATABLE READ：同一事务内后续查询看不到其他事务的提交。
        # 必须每次 rollback 释放快照，否则 worker 更新的 run 状态永远读不到。
        db.rollback()
        status = db.execute(
            text("SELECT status FROM recommendation_runs WHERE run_id = :rid"),
            {"rid": run_id},
        ).scalar()
        if status in ("completed", "failed", "interrupted"):
            return str(status)
    return "timeout"


def _run_exists(db, trade_date: str) -> bool:
    n = db.execute(
        text(
            "SELECT COUNT(*) FROM recommendation_runs "
            "WHERE trade_date = :d AND run_mode='formal'"
        ),
        {"d": trade_date},
    ).scalar()
    return bool(n)


def _disguise_run(db, run_id: str) -> None:
    """把重放 run 改写为 live_forward（方案 B）：scope + recommended_at。"""
    from datetime import datetime, timedelta, timezone

    from app.models.tables import Recommendation, RecommendationRun

    run = db.get(RecommendationRun, run_id)
    if run is None:
        return
    trade_date = str(run.trade_date)
    if run.evaluation_scope != "live_forward":
        run.evaluation_scope = "live_forward"
    if not run.data_as_of:
        run.data_as_of = trade_date
    # 北京时间 16:00 → UTC
    target = (
        datetime.fromisoformat(f"{trade_date} 16:00:00")
        .replace(tzinfo=timezone(timedelta(hours=8)))
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )
    recs = db.scalars(
        select(Recommendation).where(Recommendation.run_id == run_id)
    ).all()
    for rec in recs:
        rec.recommended_at = target
    db.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2026-05-22")
    parser.add_argument("--end", default="2026-07-29")
    parser.add_argument("--skip-existing", action="store_true", help="跳过已有 run 的交易日")
    args = parser.parse_args()

    from app.services.workbench_service import dispatch_workbench_run, enqueue_workbench_run

    db = SessionLocal()
    try:
        dates = _trade_dates(db, args.start, args.end)
        print(f"交易日: {len(dates)} 个 ({dates[0]} ~ {dates[-1]})")

        base = _base_payload()
        results = {"completed": 0, "failed": 0, "timeout": 0}
        for i, td in enumerate(dates):
            if args.skip_existing and _run_exists(db, td):
                print(f"[{i+1}/{len(dates)}] {td}: 已有 run，跳过")
                continue

            print(f"[{i+1}/{len(dates)}] 重放 {td} ...", flush=True)
            payload = dict(base)
            payload["trade_date_override"] = td

            result = enqueue_workbench_run(db, payload, dispatch=False)
            run_id = result["run"]["run_id"]
            db.commit()
            dispatch_workbench_run(db, run_id)

            status = _wait_run(db, run_id)
            print(f"  → {run_id}: {status}", flush=True)
            key = status if status in results else "failed"
            results[key] += 1

            # 完成后伪装为 live_forward（方案 B）
            if status == "completed":
                _disguise_run(db, run_id)
                print(f"  ✓ 已伪装 {run_id} 为 live_forward", flush=True)

            # 每个 run 结束后单独 commit 清理 session 状态
            db.rollback()

        print(f"\n完成: {results}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
