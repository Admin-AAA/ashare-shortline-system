#!/usr/bin/env python3
"""字符集健康复核（2026-08-02）：用 SQLAlchemy（SET NAMES utf8mb4）读取
7 月 formal run 的 JSON 字段，统计非法 UTF-8 字节与中文可读性，判断
此前诊断的"写入乱码"是库内真实损坏还是 mysql CLI 客户端字符集假象。
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import text  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402

db = SessionLocal()
try:
    rows = db.execute(
        text(
            "SELECT trade_date, run_id, run_name, filter_audit_json, result_summary_json "
            "FROM recommendation_runs WHERE run_mode='formal' AND trade_date BETWEEN '2026-07-01' AND '2026-07-31' "
            "ORDER BY trade_date, started_at"
        )
    ).all()
finally:
    db.close()


def _bad_utf8(s: str) -> list[str]:
    """找出字符串中无法 round-trip 的字符（替换字符 U+FFFD 即坏字节痕迹）。"""
    bad = []
    for i, ch in enumerate(s):
        if ch == "\ufffd":
            bad.append(f"pos={i}")
            if len(bad) >= 3:
                break
    return bad


print(f"run 数: {len(rows)}")
total_bad_json = 0
total_bad_name = 0
for trade_date, run_id, run_name, fa, rs in rows:
    bad_json = _bad_utf8(fa or "") + _bad_utf8(rs or "")
    bad_name = _bad_utf8(run_name or "")
    if bad_json:
        total_bad_json += 1
        print(f"  {trade_date} {run_id}: JSON 含 {len(bad_json)} 处坏字节 {bad_json[:2]}")
    if bad_name:
        total_bad_name += 1
        print(f"  {trade_date} {run_id}: run_name 含坏字节: {run_name!r}")
print(f"\nJSON 损坏 run 数: {total_bad_json}/{len(rows)}")
print(f"run_name 损坏数: {total_bad_name}/{len(rows)}")
# 抽查 run_name 正常样例
names = [r[2] for r in rows if r[2] and "\ufffd" not in r[2]]
print("正常 run_name 样例:", names[:3])
