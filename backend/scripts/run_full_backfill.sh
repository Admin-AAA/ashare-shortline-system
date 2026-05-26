#!/bin/bash
# 全量 15min K线回填 — 挂在后台运行，支持断点续跑
# 用法: nohup bash scripts/run_full_backfill.sh > /tmp/backfill_full.log 2>&1 &
# 查看: tail -f /tmp/backfill_full.log
# 停止: kill $(cat /tmp/backfill_full.pid)  # 杀主进程即可，进度已保存
# 续跑: bash scripts/run_full_backfill.sh   # --resume 会自动从断点继续

set -euo pipefail
cd "$(dirname "$0")/.."
echo $$ > /tmp/backfill_full.pid
source .venv/bin/activate
python -u scripts/backfill_15min_expand.py --batch-size 200 --delay 0.15 --resume
rm -f /tmp/backfill_full.pid
