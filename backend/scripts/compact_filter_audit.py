"""把历史过滤审计快照转成压缩存储（默认 dry-run）。

2026-11-25 之前的快照以原始 JSON 文本存在 ``recommendation_run_filter_audits.payload_json``，
1133 行合计 3.71GB。改为 zlib 压缩后同内容只占 0.24GB（6.4%）。迁移
``0033_filter_audit_compressed`` 已完成列切换，本脚本用于在迁移前后核对与补做压缩。

Usage (from backend/):
  .venv/bin/python scripts/compact_filter_audit.py            # 看能省多少（dry-run）
  .venv/bin/python scripts/compact_filter_audit.py --apply    # 真写
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import text

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db.session import SessionLocal  # noqa: E402
from app.services.runs.audit_payload import encode_audit_payload  # noqa: E402

BATCH = 20  # 单行最大约 9.6MB，小批量提交，避免撑爆 lock buffer


def _candidates(db, *, older_than_days: int, min_bytes: int, limit: int | None):
    sql = """
        SELECT r.run_id, r.trade_date, LENGTH(a.payload_z) AS n
        FROM recommendation_runs r
        JOIN recommendation_run_filter_audits a ON a.run_id = r.run_id
        WHERE LENGTH(a.payload_z) > :min_bytes
          AND r.trade_date <= DATE_SUB(CURDATE(), INTERVAL :days DAY)
        ORDER BY r.trade_date ASC, r.run_id ASC
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return db.execute(text(sql), {"min_bytes": min_bytes, "days": older_than_days}).all()


def main() -> int:
    ap = argparse.ArgumentParser(description="压缩 recommendation_run_filter_audits.payload_z")
    ap.add_argument("--days", type=int, default=7, help="只处理 trade_date 早于 N 天前的 run（默认 7）")
    ap.add_argument("--min-bytes", type=int, default=100_000, help="只处理压缩后仍大于该字节数的行（默认 100KB）")
    ap.add_argument("--limit", type=int, default=0, help="最多处理多少行（0=全部）")
    ap.add_argument("--apply", action="store_true", help="真的写库（默认 dry-run）")
    ap.add_argument("--optimize", action="store_true", help="结束后 OPTIMIZE TABLE 回收磁盘")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        rows = _candidates(db, older_than_days=args.days, min_bytes=args.min_bytes,
                           limit=args.limit or None)
        if not rows:
            print("没有需要处理的行。")
            return 0
        total_before = sum(int(r.n or 0) for r in rows)
        print(f"候选 {len(rows)} 行，压缩后合计 {total_before / 1048576:.1f}MB")

        saved = 0
        for i, row in enumerate(rows, 1):
            blob = db.scalar(
                text("SELECT payload_z FROM recommendation_run_filter_audits WHERE run_id = :rid"),
                {"rid": row.run_id},
            )
            if not blob:
                continue
            recompressed = encode_audit_payload(json.loads(_decompress(blob)))
            saved += max(0, len(blob) - len(recompressed))
            if args.apply:
                db.execute(
                    text("UPDATE recommendation_run_filter_audits SET payload_z = :payload WHERE run_id = :rid"),
                    {"payload": recompressed, "rid": row.run_id},
                )
                if i % BATCH == 0:
                    db.commit()
            if i % 50 == 0:
                print(f"  ... {i}/{len(rows)}  已省 {saved / 1048576:.1f}MB")
        if args.apply:
            db.commit()

        print(f"{'已写入' if args.apply else 'DRY-RUN 预计'}：省 {saved / 1048576:.1f}MB"
              f"（{saved / max(1, total_before):.1%}）")
        if args.apply and args.optimize:
            print("OPTIMIZE TABLE recommendation_run_filter_audits ...")
            db.execute(text("OPTIMIZE TABLE recommendation_run_filter_audits"))
            db.commit()
            print("完成（磁盘空间已回收）")
        elif not args.apply:
            print("确认无误后加 --apply 执行。")
        return 0
    finally:
        db.close()


def _decompress(blob: bytes) -> str:
    import zlib

    return zlib.decompress(blob).decode("utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
