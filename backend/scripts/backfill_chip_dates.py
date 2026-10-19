"""Re-pull cyq_perf for given trade dates and overwrite chip_* (writes NULL-free real values only).

  PYTHONPATH=. .venv/bin/python scripts/backfill_chip_dates.py 2026-09-22 2026-09-23 ...
  PYTHONPATH=. .venv/bin/python scripts/backfill_chip_dates.py --zero-days-since 2026-09-01
  # 冻结日（筹码与前一交易日整列相同）：先清空当日 chip_* 再重拉，上游没有当日数据就保持 NULL
  PYTHONPATH=. .venv/bin/python scripts/backfill_chip_dates.py --overwrite 2026-07-31 2026-08-03 ...
  PYTHONPATH=. .venv/bin/python scripts/backfill_chip_dates.py --overwrite --frozen-days-since 2026-04-30
"""
from __future__ import annotations

import argparse

from sqlalchemy import text

from app.core.provider_quota import BACKFILL_BUDGET_FRACTION, backfill_budget_check, flush
from app.db.session import SessionLocal
from app.services.data_gap_remediation_service import _backfill_chip_date
from app.services.tushare_transport import TushareProviderError

# cyq_perf 路由会落到的上游；任一超过当日额度 60% 或已报日限即停（routing 会回落到另一家，继续只会把它也耗光）
CHIP_PROVIDERS = ("promax", "quantdata")


def zero_days(db, since: str) -> list[str]:
    rows = db.execute(text(
        "SELECT trade_date, COUNT(*) n, SUM(chip_cost_50 IS NULL OR chip_cost_50 = 0) z FROM daily_kbars "
        "WHERE trade_date >= :s GROUP BY trade_date HAVING z > 0.05 * n ORDER BY trade_date"), {"s": since}).all()
    return [str(r[0]) for r in rows]


def frozen_days(db, since: str, ratio: float = 0.9) -> list[str]:
    """Trade dates whose chip_* equal the previous trade date's for > ratio of stocks."""
    days = [str(r[0]) for r in db.execute(text(
        "SELECT DISTINCT trade_date FROM daily_kbars WHERE trade_date >= "
        "(SELECT MAX(trade_date) FROM daily_kbars WHERE trade_date < :s) ORDER BY trade_date"), {"s": since})]
    out = []
    for prev, d in zip(days, days[1:]):
        n, same = db.execute(text(
            "SELECT COUNT(*), SUM(a.chip_cost_50 = b.chip_cost_50 AND a.chip_cost_15 <=> b.chip_cost_15 "
            "AND a.chip_cost_85 <=> b.chip_cost_85 AND a.chip_weight_avg <=> b.chip_weight_avg) "
            "FROM daily_kbars a JOIN daily_kbars b ON b.code = a.code AND b.trade_date = :p "
            "WHERE a.trade_date = :d AND a.chip_cost_50 IS NOT NULL"), {"p": prev, "d": d}).one()
        if n and int(same or 0) > ratio * int(n):
            out.append(d)
    return out


_NULL_CHIPS = ("UPDATE daily_kbars SET chip_cost_15=NULL, chip_cost_50=NULL, chip_cost_85=NULL, "
               "chip_weight_avg=NULL, chip_winner_rate=NULL, updated_at=UTC_TIMESTAMP() WHERE trade_date=:d")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dates", nargs="*")
    ap.add_argument("--zero-days-since")
    ap.add_argument("--frozen-days-since", help="自动选出筹码与前一交易日 >90%% 相同的日期")
    ap.add_argument("--overwrite", action="store_true",
                    help="重拉前先把当日 chip_* 全部置 NULL（与重拉同一事务；上游报错则回滚）。冻结日必须带")
    ap.add_argument("--budget-fraction", type=float, default=BACKFILL_BUDGET_FRACTION,
                    help="单个数据源当日额度用到该比例即停（默认 0.6），额度上限读 ASHARE_TUSHARE_<P>_DAILY_QUOTA")
    args = ap.parse_args()
    with SessionLocal() as db:
        dates = list(args.dates) or (zero_days(db, args.zero_days_since) if args.zero_days_since else [])
        if args.frozen_days_since:
            dates = sorted(set(dates) | set(frozen_days(db, args.frozen_days_since)))
        print("dates:", dates, flush=True)
        for i, d in enumerate(dates):
            ok, reason = backfill_budget_check(CHIP_PROVIDERS, args.budget_fraction)
            if not ok:
                print(f"STOP before {d}: {reason}; 剩余 {len(dates) - i} 天未补: {dates[i:]}", flush=True)
                break
            try:
                if args.overwrite:
                    db.execute(text(_NULL_CHIPS), {"d": d})  # committed together with the re-pull below
                out = _backfill_chip_date(db, d)
            except TushareProviderError as exc:
                db.rollback()
                print(f"STOP at {d}: 上游失败 {exc}; 剩余 {len(dates) - i} 天未补: {dates[i:]}", flush=True)
                break
            # whatever cyq_perf did not cover stays missing: 0 -> NULL (never 0-as-missing)
            res = db.execute(text(
                "UPDATE daily_kbars SET chip_cost_15=NULL, chip_cost_50=NULL, chip_cost_85=NULL, "
                "chip_weight_avg=NULL, chip_winner_rate=NULL, updated_at=UTC_TIMESTAMP() "
                "WHERE trade_date=:d AND chip_cost_50 = 0"), {"d": d})
            db.commit()
            print(d, out, "zero->NULL:", res.rowcount, flush=True)
    flush()


if __name__ == "__main__":
    main()
