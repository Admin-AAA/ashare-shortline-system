"""Compact `recommendation_run_filter_audits.payload_json` (default dry-run).

一个 run 的审计 JSON 平均 4.5MB / 最大 8MB，全表 3.5GB：99% 体积在 `rows`（全市场每只票
一份诊断对象），其中 `prefilter`（约 0.4KB/票）与 `trace_summary` 前端从不读取——
读接口已经剥掉它们。本脚本做两级瘦身：

  1. 字段级（默认，作用于早于 --days 的 run）：删 rows[].prefilter / rows[].trace_summary
     （单 run 省约 24%）。distributions 已在写库时算好并单独存，不受影响。
  2. 整块 rows（可选 --drop-rows-before-days M，作用于更早的 run）：只保留
     stage_summary / distributions / reason_counts / empty_reason，漏斗汇总仍可读，
     逐票明细不再可查。适合已经归档的历史 run。

写库路径已在 2026-11-23 改为不再写入这两个字段，本脚本只处理历史行。

Usage (from backend/):
  .venv/bin/python scripts/compact_filter_audit.py                 # 看能省多少（dry-run）
  .venv/bin/python scripts/compact_filter_audit.py --apply          # 真写
  .venv/bin/python scripts/compact_filter_audit.py --apply --drop-rows-before-days 120
  .venv/bin/python scripts/compact_filter_audit.py --apply --optimize
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
from app.services.runs.filter_audit import (  # noqa: E402
    _FILTER_AUDIT_STORAGE_DROP_ROW_FIELDS,
    compact_filter_audit_for_storage,
)

KEEP_KEYS_WHEN_DROPPING_ROWS = ("stage_summary", "distributions", "reason_counts", "empty_reason")
BATCH = 20  # 单行最大约 8MB，小批量提交，避免撑爆 lock buffer


def _candidates(db, *, older_than_days: int, min_bytes: int, limit: int | None):
    sql = """
        SELECT r.run_id, r.trade_date, CHAR_LENGTH(a.payload_json) AS n
        FROM recommendation_runs r
        JOIN recommendation_run_filter_audits a ON a.run_id = r.run_id
        WHERE CHAR_LENGTH(a.payload_json) > :min_bytes
          AND r.trade_date <= DATE_SUB(CURDATE(), INTERVAL :days DAY)
        ORDER BY r.trade_date ASC, r.run_id ASC
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return db.execute(text(sql), {"min_bytes": min_bytes, "days": older_than_days}).all()


def main() -> int:
    ap = argparse.ArgumentParser(description="Compact recommendation_run_filter_audits.payload_json")
    ap.add_argument("--days", type=int, default=7, help="只处理 trade_date 早于 N 天前的 run（默认 7）")
    ap.add_argument("--drop-rows-before-days", type=int, default=0,
                    help="早于 N 天的 run 直接丢 rows，只留汇总（0=不丢）")
    ap.add_argument("--min-bytes", type=int, default=100_000, help="只处理大于该字节数的行（默认 100KB）")
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
        print(f"候选 {len(rows)} 行，合计 {total_before / 1048576:.1f}MB")

        saved = 0
        dropped_rows_runs = 0
        for i, row in enumerate(rows, 1):
            payload = db.scalar(
                text("SELECT payload_json FROM recommendation_run_filter_audits WHERE run_id = :rid"),
                {"rid": row.run_id},
            )
            if not payload:
                continue
            try:
                audit = json.loads(payload)
            except Exception as exc:  # noqa: BLE001 - 坏 JSON 不猜，跳过留人工
                print(f"  跳过 {row.run_id}：JSON 解析失败 {exc}")
                continue
            drop_rows = bool(args.drop_rows_before_days) and str(row.trade_date) <= _cutoff(args.drop_rows_before_days)
            if drop_rows:
                compacted = {k: v for k, v in audit.items() if k in KEEP_KEYS_WHEN_DROPPING_ROWS}
                dropped_rows_runs += 1
            else:
                compacted = compact_filter_audit_for_storage(audit)
            new_payload = json.dumps(compacted, ensure_ascii=False)
            saved += max(0, len(payload.encode()) - len(new_payload.encode()))
            if args.apply:
                db.execute(
                    text("UPDATE recommendation_run_filter_audits SET payload_json = :payload WHERE run_id = :rid"),
                    {"payload": new_payload, "rid": row.run_id},
                )
                if i % BATCH == 0:
                    db.commit()
            if i % 50 == 0:
                print(f"  ... {i}/{len(rows)}  已省 {saved / 1048576:.1f}MB")
        if args.apply:
            db.commit()

        print(f"{'已写入' if args.apply else 'DRY-RUN 预计'}：省 {saved / 1048576:.1f}MB"
              f"（{saved / max(1, total_before):.1%}），其中整块丢 rows 的 run {dropped_rows_runs} 个")
        print(f"字段裁剪：{', '.join(_FILTER_AUDIT_STORAGE_DROP_ROW_FIELDS)}")
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


def _cutoff(days: int) -> str:
    import datetime as _dt

    return (_dt.date.today() - _dt.timedelta(days=days)).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
