"""验证 dragon_tiger 表数据连续性 + 推荐龙虎榜填充率。

T5 一致性：dragon_tiger 数据缺口导致推荐 dragon_tiger_is_on_list 全 false。
本脚本检测：
1. dragon_tiger 表日期是否连续（无缺口）
2. 推荐中 dragon_tiger_is_on_list=true 的比例

用法: python scripts/verify_dragon_tiger_continuity.py
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

# 让脚本能 import app
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal  # noqa: E402
from sqlalchemy import text  # noqa: E402


def main() -> int:
    db = SessionLocal()
    # 1. 检查 dragon_tiger 表日期连续性
    rows = db.execute(
        text(
            "SELECT trade_date, COUNT(*) FROM dragon_tiger "
            "GROUP BY trade_date ORDER BY trade_date"
        )
    ).all()
    dates = [date.fromisoformat(str(r[0])) for r in rows]
    print(f"dragon_tiger 表: {len(dates)} 个日期, 范围 {dates[0]} ~ {dates[-1]}")

    gaps: list[tuple[date, date]] = []
    for i in range(1, len(dates)):
        prev, cur = dates[i - 1], dates[i]
        # 跳过周末（周五到周一 gap 3 天正常）
        business_gap = (cur - prev).days
        if business_gap > 3:  # >3 天说明有缺口（含周末）
            gaps.append((prev, cur))
    if gaps:
        print(f"⚠ 日期缺口 {len(gaps)} 处:")
        for prev, cur in gaps:
            print(f"  {prev} → {cur} (gap {(cur - prev).days} 天)")
    else:
        print("✅ 日期连续（无缺口）")

    # 2. 检查推荐龙虎榜填充率
    print()
    total = db.execute(
        text(
            "SELECT COUNT(*) FROM recommendations WHERE feature_snapshot_json IS NOT NULL"
        )
    ).fetchone()[0]
    on_list = db.execute(
        text(
            "SELECT COUNT(*) FROM recommendations WHERE feature_snapshot_json IS NOT NULL "
            "AND JSON_EXTRACT(feature_snapshot_json, '$.dragon_tiger_is_on_list') IN ('true', 1, true)"
        )
    ).fetchone()[0]
    rate = on_list * 100 / total if total else 0
    print(f"推荐龙虎榜填充: {on_list}/{total} 笔 ({rate:.1f}%) on_list=true")
    if rate < 5:
        print("⚠ 填充率 <5%，可能数据缺口或填充逻辑问题")
    else:
        print("✅ 填充率正常")

    return 0 if not gaps and rate >= 5 else 1


if __name__ == "__main__":
    sys.exit(main())
