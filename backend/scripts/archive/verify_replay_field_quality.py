"""验证 2025-07/09 回放样本的字段合成情况（Phase 2 根因确认）。

背景：诊断报告指出回放样本 40% 字段 SYNTHETIC（dragon_tiger/capital_flow/news
等 confidence 0.18 fallback）。本脚本统计：
1. 2025-07/09 vs 2026-05/06 回放样本的 field_quality fallback 占比；
2. 有多少样本的 bucket 策略依赖 fallback 字段命中（如 dragon-tiger-smart-money
   的 universe 条件 dragon_tiger_is_on_list）。
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.tables import Recommendation, RecommendationRun  # noqa: E402


def _quality_stats(snapshot: dict) -> dict:
    fq = snapshot.get("data_quality", {}).get("field_quality", {})
    fallback = sum(1 for v in fq.values() if isinstance(v, dict) and v.get("status") == "fallback")
    observed = sum(1 for v in fq.values() if isinstance(v, dict) and v.get("status") == "observed")
    total = len(fq)
    sb = snapshot.get("data_quality", {}).get("source_breakdown", {})
    return {
        "fields_total": total,
        "fallback": fallback,
        "fallback_pct": round(fallback / total * 100, 1) if total else 0,
        "synthetic_pct": sb.get("SYNTHETIC"),
        "observed_pct": sb.get("OBSERVED"),
    }


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
            if not recs:
                print(f"{ym}: 无样本")
                continue
            fb_pcts, synth_pcts = [], []
            fb_buckets = Counter()
            for rec in recs:
                try:
                    snap = json.loads(rec.feature_snapshot_json or "{}")
                except (json.JSONDecodeError, TypeError):
                    continue
                q = _quality_stats(snap)
                fb_pcts.append(q["fallback_pct"])
                if q["synthetic_pct"] is not None:
                    synth_pcts.append(q["synthetic_pct"])
                if q["fallback"] > 0:
                    fb_buckets[rec.bucket] += 1
            n = len(fb_pcts)
            print(f"{ym}: n={n} 平均fallback字段占比={sum(fb_pcts)/n:.1f}% "
                  f"平均SYNTHETIC占比={sum(synth_pcts)/len(synth_pcts):.1f}%")
            print(f"   含fallback字段的策略桶分布: {dict(fb_buckets.most_common(6))}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
