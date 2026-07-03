"""
数据健康检查 CLI。

脚本与 /api/admin/data-health 共用同一个诊断服务，避免运维脚本和健康页
使用不同参考交易日、日期格式或分钟线口径。

使用方法:
    python scripts/check_data_health.py
    python scripts/check_data_health.py --json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import SessionLocal
from app.services.data_health_service import diagnose_all

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("data_health")


def main() -> dict[str, Any]:
    parser = argparse.ArgumentParser(description="数据健康检查")
    parser.add_argument("--json", action="store_true", help="输出与 /api/admin/data-health 同源的 JSON")
    args = parser.parse_args()

    with SessionLocal() as db:
        result = diagnose_all(db)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_human_report(result)
        _log_summary(result)

    return result


def _print_human_report(result: dict[str, Any]) -> None:
    print(f"数据健康评分: {result.get('score', 0)}")
    print(
        "参考交易日: "
        f"{result.get('reference_trade_date') or '-'} "
        f"({result.get('reference_trade_date_source') or 'unknown'})"
    )
    note = result.get("reference_trade_date_note")
    if note:
        print(f"参考说明: {note}")

    print(f"\n{'表名':<20} {'最新日期':<12} {'滞后':>6} {'总行数':>12} {'状态'}")
    print("-" * 70)
    tables = result.get("tables") or {}
    lag_days = result.get("lag_days") or {}
    recent_tables = ((result.get("recent_trade_day_coverage") or {}).get("tables") or {})
    for key, info in tables.items():
        latest = info.get("max_date") or "-"
        lag = lag_days.get(key, "-")
        total = int(info.get("total_rows") or 0)
        recent = recent_tables.get(key) or {}
        gap_count = int(
            recent.get("effective_gap_count")
            if recent.get("effective_gap_count") is not None
            else len(set((recent.get("missing_dates") or []) + (recent.get("incomplete_dates") or [])))
        )
        status = "正常" if not info.get("error") and gap_count == 0 else "需关注"
        if info.get("error"):
            status = f"诊断失败: {info.get('error')}"
        print(f"{key:<20} {latest:<12} {str(lag):>6} {total:>12,} {status}")

    issues = result.get("issues") or []
    if issues:
        print("\n需关注问题:")
        for issue in issues:
            print(f"- [{issue.get('severity', 'unknown')}] {issue.get('message')}")
    else:
        print("\n需关注问题: 无")


def _log_summary(result: dict[str, Any]) -> None:
    issues = result.get("issues") or []
    high = sum(1 for item in issues if item.get("severity") == "high")
    medium = sum(1 for item in issues if item.get("severity") == "medium")
    logger.info(
        "数据健康: score=%s issues=%d high=%d medium=%d reference_trade_date=%s",
        result.get("score"),
        len(issues),
        high,
        medium,
        result.get("reference_trade_date"),
    )


if __name__ == "__main__":
    main()
