#!/usr/bin/env python3
"""验证新重放 run 的空仓/无机会语义输出（2026-08-02 修复验收）。"""
from __future__ import annotations

import json
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
            "SELECT run_id, trade_date, recommended_count, result_summary_json "
            "FROM recommendation_runs WHERE run_id IN "
            "('run_20260802_163541_469359','run_20260802_164101_791ad0','run_20260802_164622_c58003','run_20260802_164702_61e7f9',"
            "'run_20260802_155221_a6fe67')"
        )
    ).all()
finally:
    db.close()

fails = []
for run_id, trade_date, rec_count, rs in rows:
    d = json.loads(rs or "{}")
    opp = d.get("opportunity")
    reason = d.get("no_opportunity_reason")
    print(f"{trade_date} {run_id}: recommended={rec_count} opportunity={opp!r} reason={reason!r}")
    if rec_count == 0 and opp != "empty":
        fails.append(f"{trade_date}: 0 推荐但 opportunity={opp!r}")
    if rec_count > 0 and opp != "has_recommendations":
        fails.append(f"{trade_date}: 有推荐但 opportunity={opp!r}")

if fails:
    print("FAILED:", *fails, sep="\n  ")
    sys.exit(1)
print("ALL PASS: 空仓/无机会语义正确")
