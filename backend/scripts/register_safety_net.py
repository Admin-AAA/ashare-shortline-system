#!/usr/bin/env python3
"""注册 formal_run_safety_net 调度任务（盘前 08:30 安全网）。

用法: python3 scripts/register_safety_net.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.session import SessionLocal
from app.services.cost_service import upsert_scheduled_task


def main() -> None:
    db = SessionLocal()
    try:
        upsert_scheduled_task(
            db,
            task_id="formal_run_safety_net",
            name="盘前 formal 推荐安全网",
            task_type="formal_safety_net",
            cron_expression="30 8 * * mon-fri",
            payload={},
            enabled=True,
        )
        print("✅ 已注册 formal_run_safety_net 调度任务 (cron=30 8 * * mon-fri)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
