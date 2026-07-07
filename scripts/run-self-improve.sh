#!/usr/bin/env bash
# self-improve 定时触发器（launchd 每小时调用一次）
# 单实例门禁：mkdir 锁，拿不到锁直接退出，防多实例并发
# 替代旧的 /loop 5 次同会话模式（会撞 1M input limit）
set -euo pipefail

# === 配置 ===
ROOT="/Users/wangao/Desktop/ashare-shortline-system"
LOG_DIR="$ROOT/backend/logs"
LOG_FILE="$LOG_DIR/self-improve-cron.log"
LOCK_DIR="/tmp/ashare-self-improve.lock.d"   # mkdir 当锁（macOS 原生，无需 flock）
MAX_AGE=3600                                   # 锁过期阈值（秒）：超过 1 小时强制接管（防僵尸）
CLAUDE="/opt/homebrew/bin/claude"
MAX_TURNS=80                                    # 单轮最多 80 turn（约 30-50 分钟）

mkdir -p "$LOG_DIR"

# === 单实例门禁 ===
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  age=$(( $(date +%s) - $(stat -f %m "$LOCK_DIR" 2>/dev/null || echo 0) ))
  if [ "$age" -lt "$MAX_AGE" ]; then
    echo "[$(date '+%F %T')] 已有实例在跑（${age}s），跳过本次" >> "$LOG_FILE"
    exit 0
  fi
  echo "[$(date '+%F %T')] 锁过期（${age}s），强制接管" >> "$LOG_FILE"
  rmdir "$LOCK_DIR" 2>/dev/null || rm -rf "$LOCK_DIR"
  mkdir "$LOCK_DIR"
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null' EXIT

# 记录锁获取时间
date +%s > "$LOCK_DIR/started_at"

echo "[$(date '+%F %T')] === self-improve 启动（PID $$）===" >> "$LOG_FILE"

# === 起新 Claude 会话跑 /self-improve ===
# -p 非交互模式；--max-turns 限制轮次防爆走；每次新会话，上下文不累积
cd "$ROOT"
"$CLAUDE" -p "/self-improve" \
  --max-turns "$MAX_TURNS" \
  --output-format text \
  >> "$LOG_FILE" 2>&1 || {
    echo "[$(date '+%F %T')] ❌ claude 退出码 $?" >> "$LOG_FILE"
    exit 1
  }

echo "[$(date '+%F %T')] === self-improve 完成 ===" >> "$LOG_FILE"
