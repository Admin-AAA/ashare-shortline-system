"""查看 exposure_sample_counts 当前实际值（诊断测试失败）。"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.db.session import SessionLocal  # noqa: E402
from app.services.outcome_service import exposure_sample_counts  # noqa: E402


def main() -> None:
    db = SessionLocal()
    try:
        counts = exposure_sample_counts(db, window_days=30, run_mode=("formal", "experiment"))
        for sid, n in sorted(counts.items(), key=lambda x: x[1]):
            print(f"  {sid}: {n}")
        print("DSA:", counts.get("daily-stock-analysis-skill"))
    finally:
        db.close()


if __name__ == "__main__":
    main()
