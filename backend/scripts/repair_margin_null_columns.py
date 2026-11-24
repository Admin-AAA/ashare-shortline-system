import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.margin_detail_fill import detect_column_gaps, fill_margin_day

DAYS = [
    "2026-11-16", "2026-10-28", "2026-10-26", "2026-10-22", "2026-10-21",
    "2026-10-20", "2026-10-19", "2026-10-16", "2026-10-15", "2026-10-14", "2026-10-13",
]


def main() -> int:
    ap = argparse.ArgumentParser(description="按列缺口补 margin_detail 的 NULL 金额列")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--days", nargs="*", default=DAYS)
    args = ap.parse_args()
    db = SessionLocal()
    try:
        for d in args.days:
            before = detect_column_gaps(db, d)
            if not before:
                print(f"{d} 无列缺口")
                continue
            if not args.apply:
                print(f"{d} 列缺口 {before}（未加 --apply，不取数不写库）")
                continue
            out = fill_margin_day(db, d, force=True)
            after = detect_column_gaps(db, d)
            print(f"{d} status={out['status']} reasons={out['reasons']} "
                  f"补列={out.get('null_fields_filled')} 新增行={out.get('eastmoney_inserted')} "
                  f"取数={out.get('eastmoney_rows')} 缺口 {before} -> {after}")
            if out.get("error"):
                print("   error:", out["error"])
            probe = out.get("provider_probe")
            if probe:
                print("   probe:", probe["attempted"], probe["by_source"], probe["errors"][:2])
        left = db.execute(text(
            "SELECT trade_date, SUM(rzche IS NULL) n FROM margin_detail "
            "GROUP BY trade_date HAVING n > 0 ORDER BY trade_date DESC"
        )).all()
        print("仍有 rzche NULL 的交易日:", [(str(r[0]), int(r[1])) for r in left])
        return 0
    finally:
        db.close()


sys.exit(main())