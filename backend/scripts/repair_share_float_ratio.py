import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.xiaoshi_alt_data import XIAOSHI_FLOAT_PREFIX, total_shares_by_code

SELECT = text(
    "SELECT id, code, float_share, float_ratio FROM share_floats WHERE holder_name LIKE :p"
)
UPDATE = text("UPDATE share_floats SET float_ratio = :r WHERE id = :i")


def main() -> int:
    ap = argparse.ArgumentParser(description="把 xiaoshi 解禁行的 float_ratio 归一到占总股本口径")
    ap.add_argument("--apply", action="store_true", help="不加则只报告差异，不写库")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        rows = db.execute(SELECT, {"p": XIAOSHI_FLOAT_PREFIX + "%"}).all()
        totals = total_shares_by_code(db, sorted({str(r.code) for r in rows}))
        changes: list[tuple[int, float | None, float, str]] = []
        unresolved: list[str] = []
        for r in rows:
            total = totals.get(str(r.code))
            shares = float(r.float_share or 0)
            if not total or not shares:
                unresolved.append(str(r.code))
                changes.append((int(r.id), None, float(r.float_ratio or 0), str(r.code)))
                continue
            new = round(shares / total * 100.0, 4)
            if abs(new - float(r.float_ratio or 0)) > 1e-6:
                changes.append((int(r.id), new, float(r.float_ratio or 0), str(r.code)))
        print(f"xiaoshi 行 {len(rows)}，需改 {len(changes)}，总股本不可解析 {len(unresolved)} {sorted(set(unresolved))}")
        for _id, new, old, code in sorted(changes, key=lambda x: -x[2])[:10]:
            print(f"  {code} id={_id} {old:.4f}% -> {new if new is None else format(new, '.4f') + '%'}")
        if not args.apply:
            print("未加 --apply，不写库")
            return 0
        for _id, new, _old, _code in changes:
            db.execute(UPDATE, {"r": new, "i": _id})
        db.commit()
        left = db.execute(text(
            "SELECT COUNT(*) FROM share_floats WHERE holder_name LIKE :p AND float_ratio > 100"
        ), {"p": XIAOSHI_FLOAT_PREFIX + "%"}).scalar()
        print(f"已写库 {len(changes)} 行；剩余 >100% 的行数 = {left}")
        return 0 if left == 0 else 1
    finally:
        db.close()


sys.exit(main())