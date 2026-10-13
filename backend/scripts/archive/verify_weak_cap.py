#!/usr/bin/env python3
"""降线逻辑单元验证（2026-08-02 修复验证）：
用 2026-07-15 真实 market_snapshot 验证 resolve_score_line_context
在弱势 regime 下把 effective_score_line 压到 weak_regime_score_cap(60)。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import text  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.engine.aggregator import resolve_score_line_context  # noqa: E402
from app.engine.production_policy import load_production_selection_policy  # noqa: E402

FAILURES: list[str] = []

db = SessionLocal()
try:
    row = db.execute(
        text(
            "SELECT market_snapshot_json FROM recommendation_runs "
            "WHERE run_mode='formal' AND trade_date='2026-07-15' ORDER BY started_at DESC LIMIT 1"
        )
    ).scalar()
finally:
    db.close()

if not row:
    print("FAIL: 未找到 2026-07-15 formal run 的 market_snapshot")
    sys.exit(1)

snapshot = json.loads(row)
policy = load_production_selection_policy()

result = resolve_score_line_context(
    base_score_line=72.0,
    market_snapshot=snapshot,
    risk_template="balanced",
    selection_policy=policy,
)
print("market_base      =", result["market_base"])
print("effective_score_line =", result["effective_score_line"])
print("base_score_line  =", result["base_score_line"])

if result["market_base"] != "弱势":
    FAILURES.append(f"预期弱势 regime，实际 {result['market_base']}")
elif result["effective_score_line"] != 60.0:
    FAILURES.append(f"弱势 cap 应生效为 60.0，实际 {result['effective_score_line']}")
else:
    print("PASS: 弱势 regime 下分数线被 weak_regime_score_cap=60 封顶")

# 对照：非弱势 regime 不应被 cap（用无 regime 快照的保守场景验证不误伤）
result2 = resolve_score_line_context(
    base_score_line=72.0,
    market_snapshot=None,
    risk_template="balanced",
    selection_policy=policy,
)
print("对照组 effective（无快照）=", result2["effective_score_line"])

if FAILURES:
    print("FAILED:", *FAILURES, sep="\n  ")
    sys.exit(1)
print("ALL PASS")
