#!/usr/bin/env bash
# self-improve 触发器（极简版）
# 编排责任（worktree 创建 / commit / merge / 解冲突 / 清理）全部交给
# .claude/commands/self-improve.md 提示词内的 Claude Code 自己承担。
# 本脚本只负责：在主工作树起一个新 claude 会话跑 /self-improve，
# 并打最小日志（启动/退出时间 + 退出码）方便判断状态。
# 保留并行能力：多进程可同时调用本脚本，编排逻辑靠 worktree 隔离。
set -uo pipefail

ROOT="/Users/wangao/Desktop/ashare-shortline-system"
LOG="$ROOT/backend/logs/self-improve-cron.log"
mkdir -p "$ROOT/backend/logs"

echo "[$(date '+%F %T')] === self-improve 启动 (PID $$) ===" >> "$LOG"

cd "$ROOT"
# 不用 exec：退出码要透传给日志。临时关 errexit 以捕获 claude 的真实退出码。
set +e
/opt/homebrew/bin/claude -p "/self-improve" --output-format text
RC=$?
set -e

echo "[$(date '+%F %T')] === self-improve 退出 代码=$RC ===" >> "$LOG"
exit $RC
