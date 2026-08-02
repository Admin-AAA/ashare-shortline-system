#!/usr/bin/env python3
"""回填窗口内 stock_quotes 缺失的 turnover_rate（原始采集缺陷）。

2026-05-26/27/28 三天 quote 的 turnover_rate 全为 0，导致候选过滤把全部股票
判为 suspended_or_no_trade。从 daily_kbars 回填这些天的 turnover_rate。
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import text  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402

TARGET_DATES = ["2026-05-26", "2026-05-27", "2026-05-28"]


def main() -> int:
    db = SessionLocal()
    total = 0
    try:
        for d in TARGET_DATES:
            # 只回填 turnover_rate=0 且 daily_kbars 有值的行
            rows = db.execute(text(
                """
                SELECT q.id, k.turnover_rate
                FROM stock_quotes q
                JOIN daily_kbars k ON q.code = k.code AND q.trade_date = k.trade_date
                WHERE q.trade_date = :d AND (q.turnover_rate IS NULL OR q.turnover_rate <= 0)
                  AND k.turnover_rate > 0
                """
            ), {"d": d}).all()
            for rid, turn in rows:
                db.execute(text(
                    "UPDATE stock_quotes SET turnover_rate = :t WHERE id = :id"
                ), {"t": float(turn), "id": rid})
            db.commit()
            print(f"{d}: 回填 {len(rows)} 条 turnover_rate")
            total += len(rows)
        print(f"完成，共回填 {total} 条")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
