#!/bin/bash
# =============================================================================
# 数据健康巡检定时任务脚本 (cron_data_health.sh)
#
# 用法:
#   ./cron_data_health.sh              # 默认输出到终端
#   ./cron_data_health.sh --json       # JSON 输出
#
# 建议 crontab 配置（每天早上 9:05 执行）:
#   5 9 * * 1-5 /opt/ashare-shortline-system/backend/scripts/cron_data_health.sh >> /var/log/data_health.log 2>&1
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

# ── 日志 ──
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/data_health_$(date +%Y%m%d_%H%M%S).log"

echo "========================================" | tee -a "$LOG_FILE"
echo "数据健康巡检 - $(date '+%Y-%m-%d %H:%M:%S')" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"

# ── 执行检测 ──
if [ $# -ge 1 ] && [ "$1" = "--json" ]; then
    python "$SCRIPT_DIR/check_data_health.py" --json 2>&1 | tee -a "$LOG_FILE"
else
    python "$SCRIPT_DIR/check_data_health.py" 2>&1 | tee -a "$LOG_FILE"
fi

EXIT_CODE=$?

echo "" | tee -a "$LOG_FILE"
echo "退出状态: $EXIT_CODE" | tee -a "$LOG_FILE"
echo "日志已保存: $LOG_FILE" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

exit $EXIT_CODE
