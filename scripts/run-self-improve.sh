#!/usr/bin/env bash
# Run one autonomous self-improve session from an isolated snapshot of main.
set -uo pipefail

ROOT="${ASHARE_ROOT:-/Users/wangao/Desktop/ashare-shortline-system}"
CLAUDE_BIN="${SELF_IMPROVE_CLAUDE_BIN:-/opt/homebrew/bin/claude}"
LOG="${SELF_IMPROVE_LOG:-$ROOT/backend/logs/self-improve-cron.log}"
LOCK="${SELF_IMPROVE_LOCK:-$ROOT/.git-self-improve.lock}"
RUNS_DIR="${SELF_IMPROVE_RUNS_DIR:-$ROOT/.worktrees/self-improve-automation}"
BASE_REF="${SELF_IMPROVE_BASE_REF:-main}"
RUN_WORKTREE=""

mkdir -p "$(dirname "$LOG")" "$RUNS_DIR"

log() {
  echo "[$(date '+%F %T')] $*" >> "$LOG"
}

cleanup() {
  local rc=$?
  if [[ -n "$RUN_WORKTREE" && -d "$RUN_WORKTREE" ]]; then
    if git -C "$RUN_WORKTREE" diff --quiet --ignore-submodules -- \
      && [[ -z "$(git -C "$RUN_WORKTREE" status --short)" ]]; then
      head_commit="$(git -C "$RUN_WORKTREE" rev-parse HEAD 2>/dev/null || true)"
      containing_branches="$(git -C "$ROOT" branch --format='%(refname:short)' --contains "$head_commit" 2>/dev/null || true)"
      if [[ -n "$containing_branches" ]]; then
        git -C "$ROOT" worktree remove "$RUN_WORKTREE" >/dev/null 2>&1 || true
      else
        log "=== self-improve 保留含未挂分支提交的隔离工作树: $RUN_WORKTREE ==="
      fi
    else
      log "=== self-improve 保留含未提交改动的隔离工作树: $RUN_WORKTREE ==="
    fi
  fi
  rm -f "$LOCK/pid"
  rmdir "$LOCK" 2>/dev/null || true
  return "$rc"
}

if [[ -f "$LOCK" ]]; then
  if [[ -s "$LOCK" ]]; then
    log "=== self-improve 失败：发现非空旧文件锁 ==="
    exit 1
  fi
  rm -f "$LOCK"
fi

if ! mkdir "$LOCK" 2>/dev/null; then
  running_pid="$(sed -n '1p' "$LOCK/pid" 2>/dev/null || true)"
  if [[ "$running_pid" =~ ^[0-9]+$ ]] && kill -0 "$running_pid" 2>/dev/null; then
    log "=== self-improve 跳过：PID $running_pid 正在运行 ==="
    exit 0
  fi
  [[ -d "$LOCK" ]] && rm -f "$LOCK/pid"
  if ! rmdir "$LOCK" 2>/dev/null || ! mkdir "$LOCK" 2>/dev/null; then
    log "=== self-improve 跳过：锁状态无法安全恢复 ==="
    exit 0
  fi
  log "=== self-improve 已恢复失活锁 ==="
fi
echo "$$" > "$LOCK/pid"
trap cleanup EXIT INT TERM

if ! git -C "$ROOT" rev-parse --verify "$BASE_REF^{commit}" >/dev/null 2>&1; then
  log "=== self-improve 失败：基线 $BASE_REF 不存在 ==="
  exit 1
fi

run_id="$(date '+%Y%m%d-%H%M%S')-$$"
RUN_WORKTREE="$RUNS_DIR/$run_id"
if ! git -C "$ROOT" worktree add --detach "$RUN_WORKTREE" "$BASE_REF" >> "$LOG" 2>&1; then
  log "=== self-improve 失败：无法创建隔离工作树 ==="
  exit 1
fi

log "=== self-improve 启动 (PID $$, base=$BASE_REF, worktree=$RUN_WORKTREE) ==="
set +e
(
  cd "$RUN_WORKTREE" || exit 1
  SELF_IMPROVE_ISOLATED_RUN=1 \
  SELF_IMPROVE_RUN_ID="$run_id" \
  "$CLAUDE_BIN" -p "/self-improve" --output-format text
)
rc=$?
set -e
log "=== self-improve 退出 (PID $$, code=$rc) ==="
exit "$rc"
