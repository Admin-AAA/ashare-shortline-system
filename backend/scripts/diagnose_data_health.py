"""数据健康度诊断脚本"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select, text

from app.db.session import SessionLocal
from app.models.tables import (
    DailyKbar,
    RecommendationRun,
    ScheduledTask,
    ScheduledTaskRun,
    StockQuote,
)


def tz_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return "N/A"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def main() -> int:
    db = SessionLocal()
    try:
        print("=" * 80)
        print("系统数据健康度诊断报告")
        now = datetime.now(timezone.utc)
        print(f"生成时间: {fmt_dt(now)}")
        print("=" * 80)

        # 1. 调度任务概览
        print("\n## 1. 调度任务概览")
        tasks = db.scalars(select(ScheduledTask).order_by(ScheduledTask.task_id)).all()
        print(f"共注册 {len(tasks)} 个任务")
        for t in tasks:
            print(
                f"  - {t.task_id}: {t.name} | type={t.task_type} | "
                f"enabled={t.enabled} | is_running={t.is_running} | "
                f"last_run={fmt_dt(t.last_run_at)} | last_status={t.last_status or 'N/A'}"
            )
            if t.last_error:
                print(f"      last_error: {t.last_error[:200]}")

        # 2. 最近任务运行记录
        print("\n## 2. 最近 7 天任务运行记录")
        since = datetime.now(timezone.utc) - timedelta(days=7)
        runs = db.scalars(
            select(ScheduledTaskRun)
            .where(ScheduledTaskRun.started_at >= since)
            .order_by(ScheduledTaskRun.started_at.desc())
        ).all()
        print(f"共 {len(runs)} 条运行记录")

        grouped = defaultdict(list)
        for r in runs:
            grouped[r.task_id].append(r)

        for task_id in sorted(grouped.keys()):
            task_runs = grouped[task_id]
            print(f"\n  [{task_id}] 共 {len(task_runs)} 次运行")
            for r in task_runs[:5]:
                print(
                    f"    - {fmt_dt(r.started_at)} -> {fmt_dt(r.finished_at)} | "
                    f"status={r.status} | summary={r.summary[:80] if r.summary else 'N/A'}"
                )
                if r.error_message:
                    print(f"      error: {r.error_message[:200]}")

        # 3. 数据表健康度
        print("\n## 3. 核心数据表健康度")

        # stock_quotes
        sq_latest_snap = db.scalar(select(func.max(StockQuote.snapped_at)))
        sq_latest_date = db.scalar(select(func.max(StockQuote.trade_date)))
        sq_count = db.scalar(select(func.count(StockQuote.code)))
        sq_fresh_count = db.scalar(
            select(func.count(StockQuote.code)).where(StockQuote.freshness_status == "fresh")
        )
        print(f"\n  stock_quotes:")
        print(f"    总数: {sq_count}")
        print(f"    fresh 数量: {sq_fresh_count}")
        print(f"    最新快照时间: {fmt_dt(sq_latest_snap)}")
        print(f"    最新交易日: {sq_latest_date}")

        # daily_kbars 最新交易日
        dk_latest = db.scalar(select(func.max(DailyKbar.trade_date)))
        dk_count = db.scalar(select(func.count(DailyKbar.id)))
        print(f"\n  daily_kbars:")
        print(f"    总行数: {dk_count}")
        print(f"    最新交易日: {dk_latest}")

        # 最新交易日有数据的股票数
        if dk_latest:
            dk_codes = db.scalar(
                select(func.count(func.distinct(DailyKbar.code))).where(DailyKbar.trade_date == dk_latest)
            )
            print(f"    {dk_latest} 有数据的股票数: {dk_codes}")

        # 各股票最新 bar 日期分布
        print("\n  各表最新记录时间分布:")
        rows = db.execute(
            text(
                """
                SELECT TABLE_NAME, MAX(UPDATE_TIME) as latest
                FROM information_schema.tables
                WHERE table_schema = DATABASE()
                AND table_name IN (
                    'stock_quotes', 'daily_kbars', 'recommendation_runs',
                    'scheduled_task_runs', 'scheduled_tasks'
                )
                GROUP BY TABLE_NAME
                """
            )
        ).all()
        for row in rows:
            print(f"    {row.TABLE_NAME}: {row.latest}")

        # 4. recommendation_runs 健康度
        print("\n## 4. 推荐流水线运行健康度")
        rec_latest = db.scalar(select(func.max(RecommendationRun.trade_date)))
        rec_count_7d = db.scalar(
            select(func.count(RecommendationRun.run_id)).where(RecommendationRun.started_at >= since)
        )
        rec_status = db.execute(
            select(RecommendationRun.status, func.count(RecommendationRun.run_id))
            .where(RecommendationRun.started_at >= since)
            .group_by(RecommendationRun.status)
        ).all()
        print(f"  最新推荐交易日: {rec_latest}")
        print(f"  最近 7 天推荐运行数: {rec_count_7d}")
        print("  状态分布:")
        for status, count in rec_status:
            print(f"    {status}: {count}")

        # 5. 检查是否有长时间 running 的任务
        print("\n## 5. 异常运行状态检查")
        stale = db.scalars(
            select(ScheduledTaskRun)
            .where(ScheduledTaskRun.status == "running")
            .where(ScheduledTaskRun.started_at <= datetime.now(timezone.utc) - timedelta(hours=2))
        ).all()
        if stale:
            print(f"  发现 {len(stale)} 个运行超过 2 小时仍显示 running 的任务:")
            for r in stale:
                print(f"    - {r.task_id} started_at={fmt_dt(r.started_at)}")
        else:
            print("  未发现运行超过 2 小时的 running 任务")

        print("\n" + "=" * 80)
        print("诊断完成")
        return 0

    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
