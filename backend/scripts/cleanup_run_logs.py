"""清理 backend/logs 下的运行日志（run_*.log 与脚本日志），保留最近 N 天。

背景（2026-08-02 诊断）：backend/logs 有 491 个 run_*.log 与大量脚本日志
（共 255MB），仅 app.log/access.log 有轮转，run_*.log 无限增长。

策略：
- run_*.log：保留最近 14 天
- 其他非轮转脚本日志（*_batch*.log / *_server.log 等）：保留最近 30 天
- app.log/access.log（含 .N 轮转文件）：不清理（自带 RotatingFileHandler）

用法：backend/.venv/bin/python backend/scripts/cleanup_run_logs.py [--days 14] [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = BACKEND_ROOT / "logs"

_RUN_LOG_RE = re.compile(r"^run_.*\.log$")
_SCRIPT_LOG_RE = re.compile(r"^[a-z_]+\.log$")


def _file_age_days(path: Path) -> float:
    return (time.time() - path.stat().st_mtime) / 86400.0


def main() -> int:
    parser = argparse.ArgumentParser(description="清理 backend/logs 运行日志")
    parser.add_argument("--days", type=int, default=14, help="run_*.log 保留天数")
    parser.add_argument("--script-days", type=int, default=30, help="脚本日志保留天数")
    parser.add_argument("--dry-run", action="store_true", help="只列出将删除的文件")
    args = parser.parse_args()

    if not LOG_DIR.is_dir():
        print(f"日志目录不存在: {LOG_DIR}")
        return 1

    now = datetime.now()
    removed = 0
    freed = 0
    for path in sorted(LOG_DIR.iterdir()):
        if not path.is_file():
            continue
        name = path.name
        if _RUN_LOG_RE.match(name):
            keep_days = args.days
        elif _SCRIPT_LOG_RE.match(name) and name not in {"app.log", "access.log", "backend-local.log"}:
            keep_days = args.script_days
        else:
            continue
        age_days = _file_age_days(path)
        if age_days <= keep_days:
            continue
        size = path.stat().st_size
        if args.dry_run:
            print(f"[dry-run] 删除 {name} ({age_days:.0f}天, {size/1024:.0f}KB)")
        else:
            os.remove(path)
        removed += 1
        freed += size
    print(f"{'[dry-run] ' if args.dry_run else ''}清理完成：删除 {removed} 个文件，释放 {freed/1024/1024:.1f}MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
