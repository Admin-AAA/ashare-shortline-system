#!/usr/bin/env python3
"""注册 strategy_exposure_run 调度任务（盘前 08:03 低曝光策略补跑）。

每日盘前对近 30 天已兑现样本不足的 enabled 策略单独补跑 single_skill_id，
产出 experiment run 仅供 metrics_by_strategy 统计胜率，不进记录中心/驾驶舱。
目的：打破「低曝光策略没样本 → 永远不被选 → 永远没样本」的死循环。

用法: python3 scripts/register_strategy_exposure.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.session import SessionLocal
from app.services.cost_service import upsert_scheduled_task
from app.services.scheduler_service import TASK_TYPE_STRATEGY_EXPOSURE_RUN


def main() -> None:
    db = SessionLocal()
    try:
        upsert_scheduled_task(
            db,
            task_id="strategy_exposure_run",
            name="盘前低曝光策略补跑",
            task_type=TASK_TYPE_STRATEGY_EXPOSURE_RUN,
            cron_expression="3 8 * * 1-5",
            payload={"window_days": 30, "min_sample_threshold": 8},
            enabled=True,
        )
        print("✅ 已注册 strategy_exposure_run 调度任务 (cron=3 8 * * 1-5, 阈值=8)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
