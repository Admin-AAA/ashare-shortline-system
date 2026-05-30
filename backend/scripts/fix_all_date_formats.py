"""
一次性修复数据库中所有日期格式不一致的问题。

所有 trade_date 字段统一为 YYYY-MM-DD 格式。
遍历所有有日期字段的表，将 YYYYMMDD 格式转为 YYYY-MM-DD。

使用方法:
    python scripts/fix_all_date_formats.py [--dry-run]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db.session import SessionLocal


TABLES = [
    ("daily_kbars", "trade_date"),
    ("minute_kbars", "trade_date"),
    ("index_daily", "trade_date"),
    ("limit_up_board", "trade_date"),
    ("concept_sector_stock", "trade_date"),
    ("stk_limits", "trade_date"),
    ("margin_data", "trade_date"),
    ("hk_holdings", "trade_date"),
    ("holder_numbers", "end_date"),
    ("pledge_stats", "end_date"),
    ("repurchases", "end_date"),
    ("auction_data", "trade_date"),
    ("holder_trades", "ann_date"),
    ("broker_recommends", "month"),
    ("institutional_surveys", "surv_date"),
    ("share_floats", "float_date"),
]


def fix_date_column(db, table: str, column: str, dry_run: bool) -> int:
    """将 YYYYMMDD 格式转为 YYYY-MM-DD，符合规范的不变。"""
    # 查询需要修复的行数
    count_sql = text(
        f"SELECT COUNT(*) FROM `{table}` "
        f"WHERE `{column}` IS NOT NULL "
        f"AND `{column}` != '' "
        f"AND CHAR_LENGTH(REPLACE(`{column}`, '-', '')) = 8 "
        f"AND `{column}` NOT LIKE '%-%-%'"
    )
    result = db.execute(count_sql)
    count = result.scalar() or 0

    if count == 0:
        print(f"  {table}.{column}: 无需修复")
        return 0

    print(f"  {table}.{column}: {count} 行需要修复")

    if dry_run:
        return count

    update_sql = text(
        f"UPDATE `{table}` SET `{column}` = "
        f"CONCAT(LEFT(REPLACE(`{column}`, '-', ''), 4), '-', "
        f"MID(REPLACE(`{column}`, '-', ''), 5, 2), '-', "
        f"RIGHT(REPLACE(`{column}`, '-', ''), 2)) "
        f"WHERE `{column}` IS NOT NULL "
        f"AND `{column}` != '' "
        f"AND CHAR_LENGTH(REPLACE(`{column}`, '-', '')) = 8 "
        f"AND `{column}` NOT LIKE '%-%-%'"
    )
    db.execute(update_sql)
    db.commit()
    return count


def main():
    parser = argparse.ArgumentParser(description="修复数据库日期格式")
    parser.add_argument("--dry-run", action="store_true", help="仅检测，不执行修复")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        total = 0
        for table, column in TABLES:
            try:
                n = fix_date_column(db, table, column, args.dry_run)
                total += n
            except Exception as e:
                print(f"  {table}.{column}: 错误 - {e}")

        if args.dry_run:
            print(f"\n共需修复 {total} 行 (--dry-run 模式，未实际修改)")
        else:
            print(f"\n共修复 {total} 行")
    finally:
        db.close()


if __name__ == "__main__":
    main()
