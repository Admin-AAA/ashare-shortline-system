#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读诊断：量化「止盈价未随实际入场价重算」bug 在历史已结算 outcome 中的影响面。

第186轮修复 (outcome_service._resolve_take_profit_price)：跳空低开后止盈价只裁剪不按比例
重算，把设计 +5% 抬到 +14.5%，固定止盈路径失效 → trailing 兜底、且相对推荐价实为亏损却被标 success。

本脚本只读不写：复现修复后的 _resolve_take_profit_price，对比每条已结算 outcome 的
stored take_profit_price 与「应按比例重算」的 corrected 值，定位被异常抬高的止盈价
（即 bug 特征：stored_tp_pct 远高于设计 +5%）。

用法: python3 scripts/diagnose_take_profit_rebase.py
"""
import json
import sys

sys.path.insert(0, "backend")

from app.db.session import SessionLocal  # noqa: E402
from app.models.tables import Recommendation, RecommendationOutcome  # noqa: E402
from app.services.outcome_service import _resolve_take_profit_price, _as_float  # noqa: E402

ENTRY_SLIPPAGE_BPS = 10.0


def main():
    db = SessionLocal()
    try:
        rows = (
            db.query(
                RecommendationOutcome.recommendation_id,
                Recommendation.code,
                RecommendationOutcome.entry_price,
                RecommendationOutcome.take_profit_price,
                RecommendationOutcome.exit_reason,
                RecommendationOutcome.outcome_label,
                RecommendationOutcome.executed_return,
                Recommendation.recommend_price,
                Recommendation.feature_snapshot_json,
            )
            .join(
                Recommendation,
                Recommendation.recommendation_id == RecommendationOutcome.recommendation_id,
            )
            .filter(RecommendationOutcome.entry_triggered.is_(True))
            .filter(RecommendationOutcome.entry_price.isnot(None))
            .filter(RecommendationOutcome.take_profit_price.isnot(None))
            .all()
        )
    finally:
        db.close()

    total = len(rows)
    inflated = []  # bug 特征：stored_tp 远高于 corrected
    for r in rows:
        rec_id, code, entry, stored_tp, exit_reason, label, exec_ret, rec_price, fs_json = r
        rec_price = _as_float(rec_price)
        if not rec_price or rec_price <= 0:
            continue
        try:
            fs = json.loads(fs_json or "{}")
        except Exception:
            continue
        raw_tp = _as_float(fs.get("take_profit"))
        if raw_tp <= 0:
            continue
        # 还原 raw_entry（stored entry_price 是含滑点的 executed entry）
        raw_entry = entry / (1 + ENTRY_SLIPPAGE_BPS / 10000.0)
        corrected_tp = _resolve_take_profit_price(raw_entry, fs, recommend_price=rec_price)
        stored_pct = (stored_tp / entry - 1) * 100
        corrected_pct = (corrected_tp / entry - 1) * 100
        gap_pp = stored_pct - corrected_pct
        if gap_pp > 3.0:  # 存储的止盈价被异常抬高 >3pp → bug 特征
            inflated.append(
                {
                    "rec_id": rec_id,
                    "code": code,
                    "entry": round(entry, 2),
                    "stored_tp": stored_tp,
                    "corrected_tp": round(corrected_tp, 2),
                    "stored_pct": round(stored_pct, 2),
                    "corrected_pct": round(corrected_pct, 2),
                    "gap_pp": round(gap_pp, 2),
                    "exit_reason": exit_reason,
                    "label": label,
                    "exec_ret": exec_ret,
                }
            )

    print(f"已结算且含止盈价/入场价的 outcome 总数: {total}")
    print(f"止盈价被异常抬高 (>3pp) 的条目数: {len(inflated)}")
    if inflated:
        from collections import Counter

        by_reason = Counter(x["exit_reason"] for x in inflated)
        print("  按 exit_reason 分布:", dict(by_reason))
        by_label = Counter(x["label"] for x in inflated)
        print("  按 outcome_label 分布:", dict(by_label))
        # 被标 success 但止盈价异常抬高的（即错标嫌疑最大）
        mislabeled_success = [x for x in inflated if x["label"] == "success"]
        print(f"  其中被标 success 的条目数: {len(mislabeled_success)}")
        print("\n示例（按 gap_pp 降序，最多 12 条）:")
        for x in sorted(inflated, key=lambda d: -d["gap_pp"])[:12]:
            print(
                f"  {x['rec_id']} {x['code']} | entry={x['entry']} "
                f"stored_tp={x['stored_tp']}({x['stored_pct']:+.1f}%) "
                f"→ corrected_tp={x['corrected_tp']}({x['corrected_pct']:+.1f}%) "
                f"gap={x['gap_pp']:+.1f}pp | {x['exit_reason']} | {x['label']} "
                f"exec_ret={x['exec_ret']}"
            )


if __name__ == "__main__":
    main()
