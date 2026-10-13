"""验证辅助数据表日期连续性 + 推荐龙虎榜填充率。

T5 一致性：数据缺口导致推荐特征缺失（dragon_tiger 缺口→龙虎榜因子失效）。
本脚本检测：
1. 所有辅助数据表的日期连续性（识别缺口）
2. 推荐中 dragon_tiger_is_on_list=true 的比例

用法: python scripts/verify_dragon_tiger_continuity.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal  # noqa: E402
from sqlalchemy import text  # noqa: E402

# 检查的辅助数据表（推荐管线依赖）
AUX_TABLES = [
    "limit_up_board",
    "dragon_tiger",
    "concept_sector",
    "capital_flow",
]


def check_table_continuity(db, table: str) -> list[tuple[date, date]]:
    """检查表的日期连续性，返回缺口列表。"""
    rows = db.execute(
        text(f"SELECT trade_date FROM {table} GROUP BY trade_date ORDER BY trade_date")
    ).all()
    if not rows:
        return []
    dates = [date.fromisoformat(str(r[0])) for r in rows]
    gaps: list[tuple[date, date]] = []
    for i in range(1, len(dates)):
        prev, cur = dates[i - 1], dates[i]
        if (cur - prev).days > 3:  # >3 天说明有缺口（含周末）
            gaps.append((prev, cur))
    return gaps


def main() -> int:
    db = SessionLocal()
    all_gaps: dict[str, list] = {}
    for table in AUX_TABLES:
        try:
            rows = db.execute(
                text(f"SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM {table}")
            ).fetchone()
            gaps = check_table_continuity(db, table)
            all_gaps[table] = gaps
            print(f"{table:25s} {rows[0]:6d} 行  {rows[1]} ~ {rows[2]}", end="")
            if gaps:
                print(f"  ⚠ 缺口 {len(gaps)} 处")
                for prev, cur in gaps[:5]:
                    print(f"    {prev} → {cur} (gap {(cur - prev).days} 天)")
            else:
                print("  ✅ 连续")
        except Exception as e:
            print(f"{table}: ERROR {e}")

    # 推荐龙虎榜填充率
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

    has_gaps = any(gaps for gaps in all_gaps.values())
    return 0 if not has_gaps and rate >= 5 else 1


if __name__ == "__main__":
    sys.exit(main())
