"""检查 7/15 当前代码重跑 run 的 score_line 拦截原因（验证降线修复效果）。"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.tables import RecommendationRun  # noqa: E402


def main() -> None:
    db = SessionLocal()
    try:
        run = db.scalars(
            select(RecommendationRun)
            .where(
                RecommendationRun.run_mode == "backtest",
                RecommendationRun.trade_date == "2026-07-15",
            )
            .order_by(RecommendationRun.started_at.desc())
        ).first()
        print("run:", run.run_id, "started_at:", run.started_at, "recs:", run.recommended_count)
        audit = json.loads(run.filter_audit_json or "{}")
        rows = audit.get("rows") or []
        reasons = Counter()
        stages = Counter()
        score_samples = []
        for row in rows:
            stages[row.get("final_stage")] += 1
            if row.get("final_stage") == "score_line":
                reasons[str(row.get("final_reason") or "?")[:70]] += 1
                score_samples.append(row.get("final_score"))
        print("阶段分布:", dict(stages.most_common(6)))
        print("score_line 原因:")
        for r, n in reasons.most_common(10):
            print(f"  {n:5d}  {r}")
        if score_samples:
            print("score_line 候选分数样例:", score_samples[:10])
        print("result_summary opportunity:", json.loads(run.result_summary_json or "{}").get("opportunity"))
    finally:
        db.close()


if __name__ == "__main__":
    main()
