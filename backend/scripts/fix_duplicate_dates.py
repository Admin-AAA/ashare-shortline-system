"""Fix duplicate date format rows and normalize remaining."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from app.db.session import SessionLocal


def fix_duplicates_then_normalize(db, table, unique_cols, date_col="trade_date"):
    """Delete YYYYMMDD rows that duplicate YYYY-MM-DD rows, then update remaining."""
    join_conds = " AND ".join(f"d1.{c} = d2.{c}" for c in unique_cols)
    fmt_parts = f"CONCAT(LEFT(d1.{date_col}, 4), '-', MID(d1.{date_col}, 5, 2), '-', RIGHT(d1.{date_col}, 2))"

    # Step 1: Delete YYYYMMDD rows where a YYYY-MM-DD version of same key exists
    del_sql = text(f"""
        DELETE d1 FROM `{table}` d1
        INNER JOIN `{table}` d2 ON {join_conds}
        WHERE d1.`{date_col}` NOT LIKE '%-%-%'
        AND d2.`{date_col}` LIKE '%-%-%'
        AND {fmt_parts} = d2.`{date_col}`
    """)
    try:
        r = db.execute(del_sql)
        db.commit()
        print(f"  {table}: deleted {r.rowcount} duplicate rows")
    except Exception as e:
        db.rollback()
        print(f"  {table}: delete error - {e}")
        return

    # Step 2: Update remaining YYYYMMDD to YYYY-MM-DD
    upd_sql = text(f"""
        UPDATE `{table}` SET `{date_col}` =
        CONCAT(LEFT(`{date_col}`, 4), '-', MID(`{date_col}`, 5, 2), '-', RIGHT(`{date_col}`, 2))
        WHERE `{date_col}` NOT LIKE '%-%-%'
    """)
    try:
        r = db.execute(upd_sql)
        db.commit()
        print(f"  {table}: updated {r.rowcount} rows to YYYY-MM-DD")
    except Exception as e:
        db.rollback()
        print(f"  {table}: update error - {e}")


def main():
    db = SessionLocal()
    try:
        fix_duplicates_then_normalize(db, "index_daily", ["ts_code"])
        fix_duplicates_then_normalize(db, "concept_sector_stock", ["sector_code", "code"], "trade_date")

        # Now fix remaining tables without duplicate issue
        for table, col in [
            ("minute_kbars", "trade_date"),
            ("daily_kbars", "trade_date"),
            ("repurchases", "end_date"),
            ("share_floats", "float_date"),
        ]:
            # For large tables, do in batches by date range
            try:
                # First count
                from sqlalchemy import func, select
                count = db.scalar(
                    text(f"SELECT COUNT(*) FROM `{table}` WHERE `{col}` NOT LIKE '%%-%%'")
                )
                if count == 0:
                    print(f"  {table}.{col}: all clean")
                    continue
                print(f"  {table}.{col}: {count} rows to fix...")

                # Batch update in chunks of 100k
                batch_size = 50000
                total_fixed = 0
                while True:
                    upd = text(
                        f"UPDATE `{table}` SET `{col}` = "
                        f"CONCAT(LEFT(`{col}`, 4), '-', MID(`{col}`, 5, 2), '-', RIGHT(`{col}`, 2)) "
                        f"WHERE `{col}` NOT LIKE '%%-%%' LIMIT {batch_size}"
                    )
                    r = db.execute(upd)
                    db.commit()
                    n = r.rowcount
                    total_fixed += n
                    if n < batch_size:
                        break
                    print(f"    ... {total_fixed}/{count}")
                print(f"  {table}.{col}: {total_fixed} fixed")
            except Exception as e:
                db.rollback()
                print(f"  {table}.{col}: error - {e}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
