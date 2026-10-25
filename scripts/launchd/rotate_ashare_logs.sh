#!/bin/bash
# launchd 直接写文件的日志（~/Library/Logs/ashare-*.log、backend/logs/*.log 里不由 RotatingFileHandler 管的）无法 rename 轮转，
# 这里用 copy-truncate：超过阈值就压缩一份存档再原地清空，保留最近 KEEP 份。每周由
# com.wangao.ashare-log-rotate.plist 调用；也可手动运行。
set -u
MAX_MB="${ROTATE_MAX_MB:-20}"
KEEP="${ROTATE_KEEP:-3}"
DIRS=("$HOME/Library/Logs" "$(cd "$(dirname "$0")/../.." && pwd)/backend/logs")
for d in "${DIRS[@]}"; do
  [ -d "$d" ] || continue
  for f in "$d"/ashare-*.log "$d"/datasource_probe.log; do
    [ -f "$f" ] || continue
    size_mb=$(( $(stat -f%z "$f") / 1048576 ))
    [ "$size_mb" -ge "$MAX_MB" ] || continue
    stamp=$(date +%Y%m%d_%H%M%S)
    gzip -c "$f" > "$f.$stamp.gz" && : > "$f"
    echo "$(date '+%F %T') rotated $f (${size_mb}MB) -> $f.$stamp.gz"
    ls -1t "$f".*.gz 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do rm -f "$old"; done
  done
done
