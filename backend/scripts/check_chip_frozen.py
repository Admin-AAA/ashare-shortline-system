"""只读：列出筹码与前一交易日 >90% 相同的冻结日（覆盖任意历史区间，默认巡检只看近 30 日）。

  PYTHONPATH=. .venv/bin/python scripts/check_chip_frozen.py --since 2025-01-01
  PYTHONPATH=. .venv/bin/python scripts/check_chip_frozen.py --since 2025-01-01 --until 2025-10-17
"""
from __future__ import annotations

import argparse

from app.db.session import SessionLocal
from app.services.data_freshness import check_chip_frozen_since


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True)
    ap.add_argument("--until")
    args = ap.parse_args()
    with SessionLocal() as db:
        out = check_chip_frozen_since(db, args.since, args.until)
    for msg in out["issues"]:
        print(msg)
    print(f"frozen {len(out['frozen_dates'])} days:", " ".join(out["frozen_dates"]))


if __name__ == "__main__":
    main()
