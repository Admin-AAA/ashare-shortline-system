"""检查 production 单日重放 run（2026-07-15）的漏斗与 score_line 拦截原因。"""
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
            select(RecommendationRun).where(RecommendationRun.run_id == "run_20260802_154506_5ef1cf")
        ).first()
        audit = json.loads(run.filter_audit_json or "{}")
        print("stage_summary:")
        for s in audit.get("stage_summary") or []:
            print(f"  {s.get('stage')}: {s.get('input_count')} → {s.get('pass_count')} (filtered={s.get('filtered_count')})")
        rows = audit.get("rows") or []
        stages = Counter(r.get("final_stage") for r in rows)
        print("rows 阶段分布:", dict(stages.most_common(6)))
        reasons = Counter()
        scores = []
        for r in rows:
            if r.get("final_stage") == "score_line":
                reasons[str(r.get("final_reason") or "?")[:70]] += 1
                try:
                    scores.append(float(r.get("final_score") or 0))
                except (TypeError, ValueError):
                    pass
        print("score_line 拦截原因:")
        for reason, n in reasons.most_common(10):
            print(f"  {n:5d}  {reason}")
        if scores:
            scores.sort(reverse=True)
            print("候选分数 top:", scores[:15])
        summary = json.loads(run.result_summary_json or "{}")
        print("opportunity:", summary.get("opportunity"), "| reason:", summary.get("no_opportunity_reason"))
    finally:
        db.close()


if __name__ == "__main__":
    main()
