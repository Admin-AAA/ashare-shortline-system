"""修复分钟线「有行但覆盖股票数腰斩」的交易日。

背景：`data_health_service` 过去只判「该日有没有行」，一天只补到 3 只票也算覆盖完成，
自愈永不重补。2026-09~10 实测有 11 个交易日覆盖 3~201 只（目标池 300 只），
日内策略/研究在这些日子被静默削掉样本。判定逻辑已在
`_recent_trade_day_coverage` 里改为「< 240 只即不完整」，自愈会覆盖最近 10 个交易日；
本脚本用于把**更早**的薄覆盖日手工补齐（自愈窗口之外的存量）。

用法::

    ./.venv/bin/python scripts/repair_thin_minute_days.py --days 120 --dry-run
    ./.venv/bin/python scripts/repair_thin_minute_days.py --days 120 --code-limit 300

只走生产补数路径 `_minute_kbar_target_codes` + `_backfill_minute_kbar_date`（并行批次、
结果落库 upsert），不新增取数通道。单日探测失败会跳过该日并继续，不会中断整批。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.data_gap_remediation_service import (
    _backfill_minute_kbar_date,
    _minute_kbar_target_codes,
)
from app.services.data_health_service import _MIN_RECENT_MINUTE_KBAR_CODES


def thin_days(db, lookback_days: int, floor: int) -> list[tuple[str, int]]:
    """最近 lookback_days 个有数据的交易日里，覆盖股票数低于 floor 的 (日期, 已覆盖数)。"""
    rows = db.execute(text(
        "SELECT trade_date, COUNT(DISTINCT code) c FROM minute_kbars "
        "GROUP BY trade_date ORDER BY trade_date DESC LIMIT :n"
    ), {"n": lookback_days}).fetchall()
    out = [(str(r[0])[:10], int(r[1])) for r in rows if int(r[1]) < floor]
    return sorted(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="补齐分钟线薄覆盖交易日")
    ap.add_argument("--days", type=int, default=120, help="回看最近多少个有数据的交易日")
    ap.add_argument("--code-limit", type=int, default=300, help="每日目标股票数（与日更同口径）")
    ap.add_argument("--floor", type=int, default=_MIN_RECENT_MINUTE_KBAR_CODES, help="低于该覆盖数视为腰斩")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        days = thin_days(db, args.days, args.floor)
        print(f"扫描最近 {args.days} 个交易日，薄覆盖日 {len(days)} 个（门槛 {args.floor} 只）：")
        for d, c in days:
            print(f"  {d} 覆盖 {c} 只")
        if args.dry_run or not days:
            return 0

        started = time.monotonic()
        for d, before in days:
            codes = _minute_kbar_target_codes(db, d, limit=args.code_limit)
            if not codes:
                print(f"{d} 无需补（目标股票已满）")
                continue
            t0 = time.monotonic()
            res = _backfill_minute_kbar_date(db, d, codes)
            print(f"{d} 覆盖 {before} -> 目标 {len(codes)} 只，补入 {res.get('row_count', 0)} 行，"
                  f"缺口 {res.get('missing_code_count', 0)} 只，状态 {res.get('status', 'ok')}，"
                  f"{time.monotonic() - t0:.0f}s")
        print(f"完成，用时 {time.monotonic() - started:.0f}s")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
