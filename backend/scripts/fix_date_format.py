"""修复 capital_flow 表中不合规的日期格式 (8位数字 → YYYY-MM-DD)。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from app.db.session import SessionLocal

db = SessionLocal()
try:
    rows = db.execute(text("""
        SELECT trade_date, COUNT(*) as cnt
        FROM capital_flow
        WHERE trade_date NOT LIKE '____-__-__'
        GROUP BY trade_date
        ORDER BY trade_date DESC
        LIMIT 5
    """)).all()
    print("待修复的日期:", [(r[0], r[1]) for r in rows])

    fixed = 0
    deleted = 0
    for row in rows:
        old = row[0]
        digits = "".join(c for c in old if c.isdigit())
        if len(digits) >= 8:
            new = f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
            # 获取旧格式中的 code 列表
            old_codes = [
                r[0] for r in db.execute(
                    text(f"SELECT code FROM capital_flow WHERE trade_date = '{old}'")
                ).all()
            ]
            # 检查哪些 code 在新格式中已存在
            if old_codes:
                existing = {
                    r[0] for r in db.execute(
                        text(f"SELECT code FROM capital_flow WHERE trade_date = '{new}' AND code IN ({','.join(f\"'{c}'\" for c in old_codes)})")
                    ).all()
                }
                non_existing = [c for c in old_codes if c not in existing]
                # 删除冲突的旧格式行
                if existing:
                    codes_str = ",".join(f"'{c}'" for c in existing)
                    db.execute(text(f"DELETE FROM capital_flow WHERE trade_date = '{old}' AND code IN ({codes_str})"))
                    deleted += len(existing)
                # 更新无冲突的旧格式行
                if non_existing:
                    codes_str = ",".join(f"'{c}'" for c in non_existing)
                    db.execute(text(f"UPDATE capital_flow SET trade_date = '{new}' WHERE trade_date = '{old}' AND code IN ({codes_str})"))
                    fixed += len(non_existing)
    db.commit()
    print(f"修复了 {fixed} 行格式, 删除了 {deleted} 行重复")

    remaining = db.execute(text("SELECT COUNT(*) FROM capital_flow WHERE trade_date NOT LIKE '____-__-__'")).scalar()
    print(f"剩余未修复: {remaining}")

    dates = db.execute(text("""
        SELECT trade_date, COUNT(*) as cnt
        FROM capital_flow
        GROUP BY trade_date
        ORDER BY trade_date DESC
        LIMIT 10
    """)).all()
    print("修复后最新日期:")
    for d in dates:
        print(f"  {d[0]}: {d[1]}")
finally:
    db.close()
