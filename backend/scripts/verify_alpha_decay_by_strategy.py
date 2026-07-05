"""验证策略 alpha 衰减：按策略 + 时间段（前期/近期）对比胜率变化。

T2 定期验证：策略 alpha 是否在衰减。
- 前期 vs 近期胜率对比（trade_date 中位数分割）
- 单个因子可能衰减（如 is_main_theme），但策略整体 alpha 可能因 trailing stop + 组合而改善

用法: python scripts/verify_alpha_decay_by_strategy.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal  # noqa: E402
from sqlalchemy import text  # noqa: E402

# 前期/近期分割日期（可按需调整）
SPLIT_DATE = "2026-06-15"
MIN_SAMPLE = 5  # 最小样本量（低于此不报告，避免噪声）


def main() -> int:
    db = SessionLocal()
    rows = db.execute(
        text(
            """
            SELECT r.bucket,
                   CASE WHEN r.trade_date < :split THEN '前期' ELSE '近期' END AS period,
                   COUNT(*) AS c,
                   SUM(CASE WHEN o.outcome_label='success' THEN 1 ELSE 0 END) AS w,
                   ROUND(AVG(o.executed_return), 2) AS avg_ret
            FROM recommendation_outcomes o
            JOIN recommendations r ON o.recommendation_id = r.recommendation_id
            WHERE o.outcome_label IN ('success', 'failed') AND r.bucket != ''
            GROUP BY r.bucket, period
            ORDER BY r.bucket, period
            """
        ),
        {"split": SPLIT_DATE},
    ).all()

    # 按 bucket 聚合前期/近期
    by_bucket: dict[str, dict[str, dict]] = {}
    for bucket, period, c, w, avg_ret in rows:
        by_bucket.setdefault(bucket, {})[period] = {
            "count": c,
            "win_rate": w * 100 / c if c else 0,
            "avg_return": float(avg_ret or 0),
        }

    print(f"策略 alpha 衰减分析（分割日期: {SPLIT_DATE}，最小样本: {MIN_SAMPLE}）\n")
    print(f"{'策略':32s} {'前期WR':>8} {'近期WR':>8} {'变化':>8} {'近期笔数':>8}")
    decaying: list[str] = []
    improving: list[str] = []
    for bucket, periods in sorted(by_bucket.items()):
        early = periods.get("前期", {})
        recent = periods.get("近期", {})
        early_wr = early.get("win_rate", 0)
        recent_wr = recent.get("win_rate", 0)
        recent_count = recent.get("count", 0)
        early_count = early.get("count", 0)
        if early_count < MIN_SAMPLE or recent_count < MIN_SAMPLE:
            print(f"  {bucket:30s} {early_wr:6.1f}%  {recent_wr:6.1f}%  {'样本不足':>8}  {recent_count:6d}")
            continue
        delta = recent_wr - early_wr
        flag = "⚠衰减" if delta < -5 else ("✅改善" if delta > 5 else "稳定")
        print(f"  {bucket:30s} {early_wr:6.1f}%  {recent_wr:6.1f}%  {delta:+6.1f}pp {flag}  {recent_count:6d}")
        if delta < -5:
            decaying.append(bucket)
        elif delta > 5:
            improving.append(bucket)

    print(f"\n衰减策略 ({len(decaying)}): {decaying or '无'}")
    print(f"改善策略 ({len(improving)}): {improving or '无'}")
    print(f"\n关键洞察:")
    print(f"  - 单个因子可能衰减（如 is_main_theme），但策略整体 alpha 可能因 trailing stop + 组合而改善")
    print(f"  - 衰减 ≠ 必须移除：因子在组合中的边际贡献可能独立于单独相关性")
    print(f"  - 移除因子前必须时间分割反事实验证（前期/近期矛盾时不移除）")

    # 衰减策略 > 0 才返回 1（提示关注）
    return 1 if decaying else 0


if __name__ == "__main__":
    sys.exit(main())
