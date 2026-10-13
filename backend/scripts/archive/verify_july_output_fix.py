"""轻量验证：7 月 REPLAY run 的 score_line 候选在新降线逻辑下的产出。

不重跑管线（全量重跑内存 9G+ 不可行），直接从已落库 filter_audit_json 提取
score_line 阶段被拦候选的 final_score 分布，按新逻辑模拟：
1. 弱势 regime 线封顶 60（原 72/74）
2. 软降线 base_floor 跟随封顶（52）
3. 弱势×chg20<15 惩罚仍拦截

输出每天"旧逻辑产出/新逻辑可产出"对照。
"""
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


def _candidates(run) -> list[dict]:
    """从 filter_audit_json 提取 score_line 阶段候选（code + final_score）。"""
    try:
        audit = json.loads(run.filter_audit_json or "{}")
    except (json.JSONDecodeError, TypeError):
        return []
    rows = audit.get("rows") or []
    out = []
    for row in rows:
        stage = row.get("final_stage")
        if stage != "score_line":
            continue
        try:
            score = float(row.get("final_score") or row.get("score") or 0)
        except (TypeError, ValueError):
            continue
        out.append({"code": row.get("code"), "score": score, "reason": str(row.get("final_reason") or "")})
    return out


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
        print(f"7 月 formal run 数: {len(runs)}")
        stats = Counter()
        for run in runs:
            cands = _candidates(run)
            if not cands:
                stats["no_candidates"] += 1
                continue
            top = max(c["score"] for c in cands)
            # 旧逻辑：有效线 74（72 地板 + 弱势 +2）且软降线 base_floor=64
            old_pass = sum(1 for c in cands if c["score"] >= 74 or (64 <= top - 1 and c["score"] >= max(top - 1, 64)))
            # 新逻辑：线封顶 60，软降线 base_floor=52 → 放行线 = min(60, 新) → top-1 或 52
            new_line = min(60.0, max(top - 1.0, 52.0))
            new_pass = sum(1 for c in cands if c["score"] >= new_line)
            stats["days"] += 1
            if top >= 55:
                stats["days_top_ge_55"] += 1
            print(f"{run.trade_date}: 候选={len(cands)} top={top:.1f} 旧线产出≈{old_pass} 新线({new_line:.1f})产出={new_pass}")
        print("汇总:", dict(stats))
    finally:
        db.close()


if __name__ == "__main__":
    main()
