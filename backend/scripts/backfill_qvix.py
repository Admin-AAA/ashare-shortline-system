#!/usr/bin/env python3
"""回填 / 增量更新市场级波动率 QVIX 到 index_daily。

数据源见 app/services/datasource/qvix.py（akshare 无 token，直连 options.qvix 主机，实测稳定）。
写 index_daily，用专属 ts_code（QVIX50.SH / QVIX300.SH / QVIX300IDX.SH），与真实指数唯一键不冲突。

⚠️ 只落数据，不接选股/评分；作为实盘因子需按 §282 预注册 + 生产同构回测达标后另接。
默认不挂常驻定时任务，按需运行本脚本或用 --since 做日更增量。

用法:
    python scripts/backfill_qvix.py                 # 全历史增量补全三条 QVIX
    python scripts/backfill_qvix.py --since 2026-11-01
    python scripts/backfill_qvix.py --codes QVIX300.SH
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    parser = argparse.ArgumentParser(description="回填 QVIX 波动率到 index_daily")
    parser.add_argument("--since", default=None, help="只写该日期(含)之后，YYYY-MM-DD，用于日更增量")
    parser.add_argument("--codes", default=None, help="逗号分隔的 ts_code 子集，默认全部")
    args = parser.parse_args()

    from app.db.session import SessionLocal
    from app.services.datasource.qvix import QVIX_SERIES, ensure_qvix_daily, latest_qvix

    codes = [c.strip() for c in args.codes.split(",")] if args.codes else list(QVIX_SERIES.keys())
    unknown = [c for c in codes if c not in QVIX_SERIES]
    if unknown:
        print(f"未知 ts_code: {unknown}；可选 {sorted(QVIX_SERIES)}")
        return 2

    db = SessionLocal()
    try:
        counts = ensure_qvix_daily(db, codes, since=args.since)
        for code, n in counts.items():
            latest = latest_qvix(db, code)
            tail = f"{latest['trade_date']} close={latest['close']}" if latest else "无数据"
            print(f"{code}: 写入 {n} 行 | 最新 {tail}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
