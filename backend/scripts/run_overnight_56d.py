"""
隔夜持有策略 56 天回测（2026-04-01 ~ 2026-05-26）
用法: cd backend && source .venv/bin/activate && python scripts/run_overnight_56d.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, ".")

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.services.overnight_holding_service import run_overnight_holding_backtest

db_url = os.environ.get(
    "DATABASE_URL",
    "mysql+pymysql://aowang:1279990918@139.196.242.53:4407/ashare_workbench?charset=utf8mb4",
)
engine = create_engine(db_url, connect_args={"charset": "utf8mb4"}, pool_pre_ping=True)

with Session(engine) as db:
    result = run_overnight_holding_backtest(db, "2026-04-01", "2026-05-26")

print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
engine.dispose()
