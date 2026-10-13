"""查看 7/15 formal run 的 filter_audit rows 结构（score_line 阶段候选字段）。"""
from __future__ import annotations

import json
import sys
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
            select(RecommendationRun).where(
                RecommendationRun.run_mode == "formal",
                RecommendationRun.trade_date == "2026-07-15",
            )
        ).first()
        audit = json.loads(run.filter_audit_json or "{}")
        rows = audit.get("rows") or []
        print("rows 总数:", len(rows))
        for row in rows[:3]:
            print("keys:", list(row.keys()))
            print(json.dumps(row, ensure_ascii=False)[:500])
            print("---")
        # 找 score_line 阶段的 row
        sl = [r for r in rows if r.get("final_stage") == "score_line"]
        print("score_line rows:", len(sl))
        if sl:
            print(json.dumps(sl[0], ensure_ascii=False)[:800])
    finally:
        db.close()


if __name__ == "__main__":
    main()
