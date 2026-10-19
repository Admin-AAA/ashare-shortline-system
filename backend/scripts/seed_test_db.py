"""Create/refresh the isolated test database ``ashare_test``.

1. CREATE DATABASE ashare_test (if missing)
2. alembic upgrade head against it (same head as production)
3. copy small *reference/config* tables from production (read-only on prod):
   presets/preset_skills/skill registry/strategy versions/configs/trade_calendar/stocks.
   Market data (daily_kbars, minute_kbars, quotes, ...) is NOT copied; tests that need it
   are marked ``@pytest.mark.requires_market_data`` and skip on ashare_test.

Usage (from backend/):  PYTHONPATH=. .venv/bin/python scripts/seed_test_db.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import text

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.db.session import engine  # noqa: E402  (production URL from settings)

PROD = "ashare_workbench"
TEST = "ashare_test"
REFERENCE_TABLES = (
    "trade_calendar", "stocks", "skill_registry", "skill_versions", "strategy_versions",
    "presets", "preset_skills", "calibration_config", "data_source_config", "model_route_config",
    "scheduled_tasks", "portfolio_risk_policies",
)


def main() -> None:
    if engine.url.database != PROD:
        raise SystemExit(f"expected production URL ({PROD}) as copy source, got {engine.url.database}")
    with engine.begin() as conn:
        conn.execute(text(f"CREATE DATABASE IF NOT EXISTS {TEST} CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"))
    test_url = engine.url.set(database=TEST).render_as_string(hide_password=False)
    subprocess.run([str(BACKEND / ".venv/bin/alembic"), "upgrade", "head"], cwd=BACKEND, check=True,
                   env={**os.environ, "ASHARE_DATABASE_URL": test_url})
    with engine.begin() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for table in REFERENCE_TABLES:
            conn.execute(text(f"DELETE FROM {TEST}.{table}"))
            cols = [c for (c,) in conn.execute(text(
                "SELECT column_name FROM information_schema.columns WHERE table_schema=:t AND table_name=:n "
                "AND extra NOT LIKE '%GENERATED%' ORDER BY ordinal_position"), {"t": TEST, "n": table})]
            col_sql = ", ".join(f"`{c}`" for c in cols)
            n = conn.execute(text(f"INSERT INTO {TEST}.{table} ({col_sql}) SELECT {col_sql} FROM {PROD}.{table}")).rowcount
            print(f"{table}: {n}")
        conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))


if __name__ == "__main__":
    main()
