"""批量运行最近3个月每个交易日的全策略组合推荐。

使用 preset_formal_opening（全策略组合），backtest 模式，禁用 agent_review。
运行完成后统一调用 label_pending_outcomes 标注结果。
"""
from __future__ import annotations

import asyncio
import json
import time
import traceback
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(".env")

from sqlalchemy import create_engine, text
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.services.workbench_service import run_workbench

START_DATE = "2026-06-17"
END_DATE = "2026-09-17"
PRESET_ID = "preset_formal_opening"
OUTPUT_LOG = Path("output/batch_backtest_log.json")


def get_trading_dates() -> list[str]:
    """从 daily_kbars 获取交易日列表。"""
    settings = get_settings()
    engine = create_engine(settings.database_url)
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT DISTINCT trade_date FROM daily_kbars
                WHERE trade_date >= :start AND trade_date <= :end
                ORDER BY trade_date
            """),
            {"start": START_DATE, "end": END_DATE},
        )
        return [row.trade_date for row in result]


async def run_one_day(db, trade_date: str) -> dict:
    """运行单日推荐。"""
    payload = {
        "preset_id": PRESET_ID,
        "trade_date_override": trade_date,
        "run_mode": "backtest",
        "run_name": f"回测-全策略组合-{trade_date}",
        "disable_agent_review": True,
        "skip_aux_refresh": True,
        "wait_for_completion": True,
    }
    start = time.time()
    try:
        result = await run_workbench(db, payload)
        elapsed = time.time() - start
        # 从数据库查询实际推荐数
        rec_count = db.execute(
            text("""
                SELECT COUNT(*) FROM recommendations r
                JOIN recommendation_runs rr ON r.run_id = rr.run_id
                WHERE rr.trade_date = :td AND rr.preset_id = :pid
                AND rr.run_name LIKE '回测-全策略组合-%'
            """),
            {"td": trade_date, "pid": PRESET_ID},
        ).scalar()
        return {
            "trade_date": trade_date,
            "status": "success",
            "elapsed": round(elapsed, 1),
            "recommendation_count": rec_count or 0,
        }
    except Exception as exc:
        elapsed = time.time() - start
        return {
            "trade_date": trade_date,
            "status": "failed",
            "elapsed": round(elapsed, 1),
            "error": str(exc),
            "traceback": traceback.format_exc()[-500:],
        }


async def main() -> None:
    OUTPUT_LOG.parent.mkdir(parents=True, exist_ok=True)

    dates = get_trading_dates()
    print(f"交易日数: {len(dates)} ({dates[0]} ~ {dates[-1]})")

    results = []
    total_start = time.time()

    for i, trade_date in enumerate(dates, 1):
        db = SessionLocal()
        try:
            print(f"[{i}/{len(dates)}] 运行 {trade_date} ...", end=" ", flush=True)
            result = await run_one_day(db, trade_date)
            results.append(result)
            print(f"{result['status']} | {result['elapsed']}s | 推荐{result.get('recommendation_count', 0)}只"
                  + (f" | 错误: {result['error'][:80]}" if result['status'] == 'failed' else ""))
        finally:
            db.close()

        # 每10天保存一次进度
        if i % 10 == 0:
            with open(OUTPUT_LOG, "w", encoding="utf-8") as f:
                json.dump({"results": results, "updated_at": datetime.now().isoformat()}, f, ensure_ascii=False, indent=2)

    total_elapsed = time.time() - total_start
    success = sum(1 for r in results if r["status"] == "success")
    failed = sum(1 for r in results if r["status"] == "failed")
    total_recs = sum(r.get("recommendation_count", 0) for r in results)

    summary = {
        "start_date": START_DATE,
        "end_date": END_DATE,
        "preset_id": PRESET_ID,
        "total_days": len(dates),
        "success_days": success,
        "failed_days": failed,
        "total_recommendations": total_recs,
        "total_elapsed": round(total_elapsed, 1),
        "results": results,
        "completed_at": datetime.now().isoformat(),
    }

    with open(OUTPUT_LOG, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"批量运行完成！")
    print(f"  总天数: {len(dates)}")
    print(f"  成功: {success}")
    print(f"  失败: {failed}")
    print(f"  总推荐数: {total_recs}")
    print(f"  总耗时: {total_elapsed:.0f}s ({total_elapsed/60:.1f}min)")
    print(f"  日志: {OUTPUT_LOG}")


if __name__ == "__main__":
    asyncio.run(main())
