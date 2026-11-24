"""通用辅助数据集按交易日历史回填（幂等 upsert，经 S4 run_auxiliary_application）。

复刻 backfill_auxiliary.backfill_limit_up 的限速/幂等/逐日隔离逻辑，但数据集 key
可参数化，用于把仅覆盖单窗的辅助表（dragon_tiger / margin_detail / capital_flow /
hsgt_top10 等）补到另一窗口，使新维度因子能做强制双窗口检验。

用法：
  python scripts/backfill_aux_dataset.py --dataset dragon_tiger --start 2025-01-01 --end 2026-03-20 --sleep 0.3
  python scripts/backfill_aux_dataset.py --dataset margin_detail --start 2025-01-01 --end 2026-03-20 --sleep 0.3
"""
from __future__ import annotations
import argparse, logging, sys, time
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.tables import DailyKbar
from app.services.auxiliary.application import run_auxiliary_application

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill_aux_dataset")


def trade_dates(db, start: str, end: str, weekday: int | None = None) -> list[str]:
    days = [str(d) for d in db.scalars(
        select(DailyKbar.trade_date).where(
            DailyKbar.trade_date >= start, DailyKbar.trade_date <= end
        ).distinct().order_by(DailyKbar.trade_date)).all()]
    if weekday is None:
        return days
    return [d for d in days if date.fromisoformat(d).weekday() == weekday]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--sleep", type=float, default=0.3)
    # 周度披露的数据集（如 pledge_stat 每周五）只需要补披露日那一批，
    # 其余交易日取到的是同一份数据，逐日重复取数只会白耗上游配额。
    ap.add_argument("--weekday", type=int, default=None, help="只补该星期几（0=周一）")
    args = ap.parse_args()
    db = SessionLocal(); stored = errors = refreshed = 0
    try:
        dates = trade_dates(db, args.start, args.end, args.weekday)
        logger.info("dataset=%s days=%d (%s..%s)", args.dataset, len(dates), dates[0] if dates else "-", dates[-1] if dates else "-")
        for i, d in enumerate(dates):
            if args.sleep > 0:
                time.sleep(args.sleep)
            try:
                result = run_auxiliary_application(
                    db, instruments=(), trade_date=d, refresh=True,
                    datasets=(args.dataset,))
                item = result.orchestrated.result_for(args.dataset)
                if item.errors:
                    raise RuntimeError(", ".join(e.message for e in item.errors))
                # 只统计真正写入的行数。coverage.record_count 在判定 FRESH 时是库内已有行数，
                # 拿它当「已补」会把一行未写的补数报成几千万行成功。
                stored += item.persistence.attempted_rows if item.persistence else 0
                refreshed += 1 if item.refresh.required else 0
                db.commit()
                if (i + 1) % 20 == 0 or i == len(dates) - 1:
                    logger.info("%d/%d %s written=%d refreshed_days=%d", i + 1, len(dates), d, stored, refreshed)
            except Exception as exc:  # noqa: BLE001 - isolate per day
                db.rollback(); errors += 1
                logger.warning("%s %s failed: %s", args.dataset, d, str(exc)[:160])
        logger.info("done dataset=%s written=%d refreshed_days=%d errors=%d", args.dataset, stored, refreshed, errors)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
