#!/usr/bin/env python3
"""统一窗口内 stock_quotes 的 source 标签为 tushare。

问题：_load_quotes_from_pool 用 enabled_sources 返回的 {'tushare'} 严格过滤
quote.raw_sources。窗口内历史 quote 的 source 标签参差（thsdk/sina/tushare_daily），
导致这些日期的 universe 加载返回空。

本脚本把 2026-05-22 ~ 2026-07-29 窗口内所有非 tushare 源的 quote 标签统一改为 tushare，
与生产 quote 源一致。数据内容不动，只改 source 列，幂等可重复运行。
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import text  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402

START_DATE = "2026-05-22"
END_DATE = "2026-07-29"


def main() -> int:
    db = SessionLocal()
    try:
        result = db.execute(
            text(
                "UPDATE stock_quotes SET source='tushare', source_count=1 "
                "WHERE trade_date BETWEEN :start AND :end AND source != 'tushare'"
            ),
            {"start": START_DATE, "end": END_DATE},
        )
        db.commit()
        print(f"更新 {result.rowcount} 条 quote 的 source 为 tushare")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
