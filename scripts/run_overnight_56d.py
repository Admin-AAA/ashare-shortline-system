#!/usr/bin/env python3
"""最小化隔夜持股法回测：2026-04-01 ~ 2026-05-26"""
import json, sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.services.overnight_holding_service import run_overnight_holding_backtest

DB_URL = "mysql+pymysql://aowang:1279990918@127.0.0.1:4407/ashare_workbench"

def main():
    engine = create_engine(DB_URL, pool_pre_ping=True)
    with Session(engine) as db:
        result = run_overnight_holding_backtest(db, "20260401", "20260526")
    
    # 精简输出
    status = result.get("status", "unknown")
    total = result.get("total_recommendations", 0)
    metrics = result.get("aggregate_metrics", {})
    
    print(f"Status: {status}")
    print(f"Total recommendations: {total}")
    print(f"Trade count: {metrics.get('trade_count', 0)}")
    print(f"Win rate: {metrics.get('win_rate', 0)}")
    print(f"Avg net return: {metrics.get('average_net_return', 0)}")
    print(f"Max drawdown: {metrics.get('max_drawdown', 0)}")
    print(f"Profit factor: {metrics.get('profit_loss_ratio', 0)}")
    print(f"Sample confidence: {metrics.get('sample_confidence', 'unknown')}")
    
    # Full JSON to file
    out_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'overnight_56d_backtest.json')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(result, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nFull results saved to: {out_path}")

if __name__ == "__main__":
    main()
