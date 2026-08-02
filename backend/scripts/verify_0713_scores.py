#!/usr/bin/env python3
"""7/13 与 7/16 新重放 run 的分数分布分析（2026-08-02）。"""
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
            "SELECT run_id, trade_date, filter_audit_json FROM recommendation_runs "
            "WHERE run_id IN ('run_20260802_163541_469359','run_20260802_164702_61e7f9')"
        )
    ).all()
finally:
    db.close()

for run_id, trade_date, fa in rows:
    d = json.loads(fa)
    sl = [r for r in d.get("rows", []) if isinstance(r, dict) and r.get("final_stage") == "score_line"]
    scores = sorted(
        ((r.get("score_line") or {}).get("final_score") for r in sl),
        key=lambda x: x if isinstance(x, (int, float)) else -1,
        reverse=True,
    )
    nums = [s for s in scores if isinstance(s, (int, float))]
    reasons = {}
    for r in sl:
        slc = r.get("score_line") or {}
        reasons.setdefault(str(slc.get("reason") or r.get("final_reason")), 0)
        reasons[str(slc.get("reason") or r.get("final_reason"))] += 1
    print(f"{trade_date} {run_id}: score_line rows={len(sl)} top10={nums[:10]}")
    for reason, n in reasons.items():
        print(f"    {n}x {reason[:70]}")
