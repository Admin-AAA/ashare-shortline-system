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
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.tables import DailyKbar
from app.services.auxiliary.application import run_auxiliary_application

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill_aux_dataset")


def trade_dates(db, start: str, end: str) -> list[str]:
    return [str(d) for d in db.scalars(
        select(DailyKbar.trade_date).where(
            DailyKbar.trade_date >= start, DailyKbar.trade_date <= end
        ).distinct().order_by(DailyKbar.trade_date)).all()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--sleep", type=float, default=0.3)
    args = ap.parse_args()
    db = SessionLocal(); stored = errors = 0
    try:
        dates = trade_dates(db, args.start, args.end)
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
                stored += item.coverage.record_count; db.commit()
                if (i + 1) % 20 == 0 or i == len(dates) - 1:
                    logger.info("%d/%d %s cumulative=%d", i + 1, len(dates), d, stored)
            except Exception as exc:  # noqa: BLE001 - isolate per day
                db.rollback(); errors += 1
                logger.warning("%s %s failed: %s", args.dataset, d, str(exc)[:160])
        logger.info("done dataset=%s stored=%d errors=%d", args.dataset, stored, errors)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
