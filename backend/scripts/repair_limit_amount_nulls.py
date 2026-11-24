import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.db.session import SessionLocal

# 上游 limit_list_d 按涨跌停类型分别给字段：U 只有 fd_amount（封单金额），
# D 只有 limit_amount，Z（炸板）两者皆无。缺失被写成 0 时，
# 「没有这个口径」看起来像「金额为零」。
CASES = (
    ("limit_amount_yi", ("U", "Z")),
    ("fd_amount_yi", ("D", "Z")),
)


def main() -> int:
    ap = argparse.ArgumentParser(description="涨停板表结构性缺失字段由 0 改回 NULL")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    db = SessionLocal()
    try:
        for column, types in CASES:
            placeholders = ", ".join(f"'{t}'" for t in types)
            where = f"limit_type IN ({placeholders}) AND `{column}` = 0"
            n = db.execute(text(f"SELECT COUNT(*) FROM limit_up_board WHERE {where}")).scalar()
            print(f"{column} {types} 待改行数={n}")
            if not args.apply or not n:
                continue
            db.execute(text(f"UPDATE limit_up_board SET `{column}` = NULL WHERE {where}"))
            db.commit()
            left = db.execute(text(f"SELECT COUNT(*) FROM limit_up_board WHERE {where}")).scalar()
            print(f"   已改写，剩余={left}")
        rows = db.execute(text(
            "SELECT limit_type, COUNT(*) n, SUM(limit_amount_yi IS NULL) null_limit, "
            "SUM(fd_amount_yi IS NULL) null_fd FROM limit_up_board GROUP BY limit_type"
        )).all()
        for r in rows:
            print(tuple(r))
        return 0
    finally:
        db.close()


sys.exit(main())
