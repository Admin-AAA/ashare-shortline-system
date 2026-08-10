"""回填 daily_basic（换手率/PE/市值，Tushare daily_basic）。

按 trade_date 批量拉全市场（一次 5400+ 只，不限速），写入 daily_basic 表。
total_mv/circ_mv 单位为万元（与 Tushare 一致），free_mv_yi = circ_mv / 1e8。

用法：
  python scripts/backfill_daily_basic.py --start 2025-01-15 --end 2026-01-16
  python scripts/backfill_daily_basic.py --start 2025-01-15 --end 2026-01-16 --dry-run
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse

from sqlalchemy import text

from app.db.session import SessionLocal
from app.models.tables import DailyBasic


def _trade_dates(db, start: str, end: str) -> list[str]:
    """窗口内 daily_kbars 已有的真实交易日（避免对非交易日空调用）。"""
    rows = db.execute(
        text(
            "SELECT DISTINCT trade_date FROM daily_kbars "
            "WHERE trade_date >= :s AND trade_date <= :e ORDER BY trade_date"
        ),
        {"s": start, "e": end},
    ).scalars().all()
    return [str(r) for r in rows]


def backfill(db, pro, trade_date: str) -> int:
    df = pro.daily_basic(
        trade_date=trade_date.replace("-", ""),
        fields="ts_code,trade_date,turnover_rate,pe_ttm,total_mv,circ_mv",
    )
    if df is None or len(df) == 0:
        return 0
    objs = []
    for _, row in df.iterrows():
        code = str(row["ts_code"]).split(".")[0]
        if len(code) != 6 or not code.isdigit():
            continue
        objs.append(
            DailyBasic(
                code=code,
                trade_date=trade_date,
                turnover_rate=(
                    float(row["turnover_rate"]) if row["turnover_rate"] is not None and row["turnover_rate"] == row["turnover_rate"] else None
                ),
                pe_ttm=(float(row["pe_ttm"]) if row["pe_ttm"] is not None and row["pe_ttm"] == row["pe_ttm"] else None),
                total_mv=(float(row["total_mv"]) if row["total_mv"] is not None and row["total_mv"] == row["total_mv"] else None),
                circ_mv=(float(row["circ_mv"]) if row["circ_mv"] is not None and row["circ_mv"] == row["circ_mv"] else None),
            )
        )
    # 幂等：删当日再插
    db.execute(text("DELETE FROM daily_basic WHERE trade_date = :d"), {"d": trade_date})
    db.bulk_save_objects(objs)
    db.commit()
    return len(objs)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from app.services.tushare_client import get_pro

    db = SessionLocal()
    try:
        pro = get_pro()
        dates = _trade_dates(db, args.start, args.end)
        print(f"trade_dates to backfill: {len(dates)} ({dates[0]}..{dates[-1] if dates else '-'})")
        if args.dry_run:
            n = backfill(db, pro, dates[0]) if dates else 0
            db.rollback()
            print(f"dry-run {dates[0]}: would write {n} rows (rolled back)")
            return
        total = 0
        for i, d in enumerate(dates):
            n = backfill(db, pro, d)
            total += n
            if (i + 1) % 20 == 0 or i == len(dates) - 1:
                print(f"  {i+1}/{len(dates)} {d} rows={n} cumulative={total}")
        print(f"done: {total} rows across {len(dates)} trade_dates")
    finally:
        db.close()


if __name__ == "__main__":
    main()
