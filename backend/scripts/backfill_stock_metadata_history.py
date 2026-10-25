"""手工刷新 stocks + stock_metadata_history（逻辑已迁到 app/services/stock_identity.py，每日由调度任务
stock_identity_refresh 自动执行；本脚本只是手动入口，便于 dry-run 与一次性补齐）。

历史：2026-08-10 曾用本脚本一次性生成全表（source_version=stocks+namechange/2026-08-10/v1），
之后没人刷新；2026-10-25 审计后改为每日增量（source_version=stocks+namechange/rolling/v2，
只写与库有差异的区间）。

用法：
  python scripts/backfill_stock_metadata_history.py --dry-run   # 只打印差异，不写库
  python scripts/backfill_stock_metadata_history.py             # 刷新并对账
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import json

from app.db.session import SessionLocal
from app.services.stock_identity import reconcile_stock_identity, refresh_stock_identity


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        before = reconcile_stock_identity(db)
        result = refresh_stock_identity(db, dry_run=args.dry_run)
        print(json.dumps({"reconcile_before": before, "refresh": result}, ensure_ascii=False, default=str, indent=1))
        if not args.dry_run:
            print(json.dumps({"reconcile_after": reconcile_stock_identity(db)}, ensure_ascii=False, default=str, indent=1))
    finally:
        db.close()


if __name__ == "__main__":
    main()
