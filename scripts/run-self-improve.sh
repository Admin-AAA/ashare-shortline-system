#!/usr/bin/env bash
# self-improve 触发器（极简版）
# 编排责任（worktree 创建 / commit / merge / 解冲突 / 清理）全部交给
# .claude/commands/self-improve.md 提示词内的 Claude Code 自己承担。
# 本脚本只负责：在主工作树起一个新 claude 会话跑 /self-improve。
# 保留并行能力：多进程可同时调用本脚本，编排逻辑靠 worktree 隔离。
set -euo pipefail

ROOT="/Users/wangao/Desktop/ashare-shortline-system"
cd "$ROOT"

exec /opt/homebrew/bin/claude -p "/self-improve" --output-format text
