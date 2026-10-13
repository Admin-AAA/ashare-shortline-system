"""诊断：run_backtest 单日（2026-07-15）各阶段计数与拦截原因。"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.db.session import SessionLocal  # noqa: E402
from app.services.backtest_service import run_backtest  # noqa: E402
from app.services.factors.registry import bootstrap_default_factor_registry  # noqa: E402


def main() -> None:
    bootstrap_default_factor_registry()
    db = SessionLocal()
    try:
        result = run_backtest(db, "2026-07-15", "2026-07-15", horizon_days=5, score_line=72.0)
        print("status:", result.get("status"))
        for r in result.get("results", []):
            print(json_of(r))
        print("summary:", json_of({k: v for k, v in result.items() if k != "results"}))
    finally:
        db.close()


def json_of(obj) -> str:
    import json

    try:
        return json.dumps(obj, ensure_ascii=False, default=str)[:900]
    except Exception:
        return str(obj)[:900]


if __name__ == "__main__":
    main()
