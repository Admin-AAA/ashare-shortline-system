"""统计 7 月 formal run 的 score_line 阶段拦截原因分布。"""
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
        runs = db.scalars(
            select(RecommendationRun).where(
                RecommendationRun.run_mode == "formal",
                RecommendationRun.trade_date >= "2026-07-01",
                RecommendationRun.trade_date <= "2026-07-31",
            )
        ).all()
        reasons = Counter()
        stage_counts = Counter()
        for run in runs:
            try:
                audit = json.loads(run.filter_audit_json or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            rows = audit.get("rows") or []
            for row in rows:
                stage_counts[row.get("final_stage")] += 1
                if row.get("final_stage") == "score_line":
                    reasons[str(row.get("final_reason") or "?").replace("（", "(")[:60]] += 1
        print("阶段分布:", dict(stage_counts.most_common(8)))
        print("\nscore_line 拦截原因:")
        for reason, n in reasons.most_common(12):
            print(f"  {n:5d}  {reason}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
