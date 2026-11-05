"""[中长线] 增量刷新本地逐股财务指标库 data/midterm/fina（见 app/services/local_fina_refresh.py）。

用法：
  python -m scripts.midterm.refresh_local_fina                 # 最近 4 个季度末（每期 1~2 次网关调用）
  python -m scripts.midterm.refresh_local_fina --periods 20260930,20260630
  python -m scripts.midterm.refresh_local_fina --dry-run       # 照样拉数、算出要改哪些文件，但不写
每晚 21:00 晚间补数后由 scheduler_service._refresh_local_fina 以子进程调用（research 命名空间）。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services.local_fina_refresh import refresh  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--periods", default="")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    out = refresh(periods=[p for p in a.periods.split(",") if p] or None, dry_run=a.dry_run)
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
