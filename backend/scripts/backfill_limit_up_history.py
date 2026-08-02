"""回填 limit_up_board（涨停板榜单）历史窗口。

背景（2026-08-02 诊断）：limit_up_board 仅覆盖 2026-03-23 起，2025-07/09
回放窗口的 is_limit_up 等涨停特征因此全部缺失/假——feature_builder 的
is_limit_up 来自 limit_up_map（limit_up_board 表），缺失时一律 False，
导致 limit-up-reseal-relay 等涨停策略在 2025 窗口零命中。

复用 backfill_auxiliary.backfill_limit_up（冻结 descriptor 经
run_auxiliary_application 走限速与幂等 upsert），仅扩展日期窗口。

用法：
    backend/.venv/bin/python backend/scripts/backfill_limit_up_history.py \
        --start 2025-01-01 --end 2026-03-20
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

from app.db.session import SessionLocal  # noqa: E402
from scripts.backfill_auxiliary import backfill_limit_up  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="回填 limit_up_board 历史窗口")
    parser.add_argument("--start", required=True, help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--sleep", type=float, default=2.5, help="每交易日调用间隔秒（限速，防接口限流）")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        result = backfill_limit_up(db, args.start, args.end, sleep_seconds=args.sleep)
        print(f"limit_up_board 回填完成: {result}")
        return 0 if result["errors"] == 0 else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
