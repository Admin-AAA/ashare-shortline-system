"""检查回放样本的涨停字段来源质量（is_limit_up 是否 fallback）。

背景（2026-08-02 诊断）：回放引擎（daily_bar_backtest）构建特征时 is_limit_up
可能来自 limit_up_board 表（2025 窗口缺失）或 K 线自算。本脚本统计
2025-07/09 与 2026-05/06 回放样本中 is_limit_up / limit_up_days20 的
field_quality 来源与取值分布，判断涨停数据缺口是否影响回放样本。
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
from app.models.tables import Recommendation, RecommendationRun  # noqa: E402

FIELDS = ("is_limit_up", "limit_up_days20", "is_st", "dragon_tiger_is_on_list")


def main() -> None:
    db = SessionLocal()
    try:
        rows = db.execute(
            select(Recommendation, RecommendationRun.run_mode, Recommendation.trade_date)
            .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
            .where(RecommendationRun.run_mode == "backtest")
        ).all()
        windows = {"2025-07": [], "2025-09": [], "2026-05": [], "2026-06": []}
        for rec, mode, td in rows:
            ym = str(td)[:7]
            if ym in windows:
                windows[ym].append(rec)

        for ym, recs in windows.items():
            print(f"--- {ym} (n={len(recs)}) ---")
            for field in FIELDS:
                src = Counter()
                vals = Counter()
                for rec in recs:
                    try:
                        snap = json.loads(rec.feature_snapshot_json or "{}")
                    except (json.JSONDecodeError, TypeError):
                        continue
                    fq = snap.get("data_quality", {}).get("field_quality", {}).get(field, {})
                    src[fq.get("status", "missing")] += 1
                    v = snap.get(field)
                    vals[str(v)] += 1
                print(f"  {field}: 来源={dict(src)} 取值={dict(vals.most_common(4))}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
