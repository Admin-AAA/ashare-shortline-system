#!/usr/bin/env python3
"""2026-07 全部 formal run 的 regime / 有效线 / 漏斗批量分析（2026-08-02）。

目的：验证 weak_regime_score_cap 降线修复对 7 月零产出问题的实际覆盖面。
对每个 formal run：读出 market_snapshot（regime/sentiment），用当前生产 policy
重算 effective_score_line，并从 filter_audit_json 读 skill_gate 通过数与
score_line 阶段过滤数。
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

db = SessionLocal()
try:
    rows = db.execute(
        text(
            "SELECT trade_date, market_snapshot_json, filter_audit_json, result_summary_json "
            "FROM recommendation_runs WHERE run_mode='formal' AND trade_date BETWEEN '2026-07-01' AND '2026-07-31' "
            "AND status='completed' ORDER BY trade_date"
        )
    ).all()
finally:
    db.close()

policy = load_production_selection_policy()
print(f"{'日期':<12}{'regime':<8}{'sentiment':<6}{'原线':<6}{'新线':<6}{'gate过':<6}{'score线拦':<8}{'推荐':<4}")
for trade_date, msnap, faudit, rsum in rows:
    try:
        snap = json.loads(msnap or "{}")
        market_base = (snap.get("breadth") or {}).get("regime") or snap.get("market_regime") or "?"
        sentiment = (snap.get("breadth") or {}).get("risk") or "?"
        ctx = resolve_score_line_context(72.0, market_snapshot=snap, risk_template="balanced", selection_policy=policy)
        new_line = ctx["effective_score_line"]
    except Exception as exc:  # noqa: BLE001
        market_base, sentiment, new_line = f"ERR:{exc}", "?", "?"
    gate_pass = score_line_blocked = rec_count = "?"
    try:
        audit = json.loads(faudit or "{}")
        stage = {s.get("stage"): s for s in audit.get("stage_summary", [])}
        gate_pass = stage.get("skill_gate", {}).get("pass_count", "?")
        score_line_blocked = stage.get("score_line", {}).get("filtered_count", "?")
    except Exception:  # noqa: BLE001
        pass
    try:
        summary = json.loads(rsum or "{}")
        rec_count = summary.get("top_codes") if isinstance(summary, dict) else "?"
        rec_count = len(rec_count) if isinstance(rec_count, list) else 0
    except Exception:  # noqa: BLE001
        pass
    print(f"{trade_date:<12}{str(market_base)[:7]:<8}{str(sentiment)[:5]:<6}{'72':<6}{str(new_line):<6}{str(gate_pass):<6}{str(score_line_blocked):<8}{rec_count:<4}")
