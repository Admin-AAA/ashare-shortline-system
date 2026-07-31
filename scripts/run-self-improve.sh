#!/usr/bin/env bash
# Run one or more autonomous self-improve sessions from isolated worktrees.
# Usage:
#   run-self-improve.sh              # single worker (backward compatible)
#   run-self-improve.sh --workers 3  # multi-worker mode
set -uo pipefail

ROOT="${ASHARE_ROOT:-/Users/wangao/Desktop/ashare-shortline-system}"
CLAUDE_BIN="${SELF_IMPROVE_CLAUDE_BIN:-/opt/homebrew/bin/claude}"
LOG="${SELF_IMPROVE_LOG:-$ROOT/backend/logs/self-improve-cron.log}"
LOCK="${SELF_IMPROVE_LOCK:-$ROOT/.git-self-improve.lock}"
RUNS_DIR="${SELF_IMPROVE_RUNS_DIR:-$ROOT/.worktrees/self-improve-automation}"
BASE_REF="${SELF_IMPROVE_BASE_REF:-main}"
STATE_TOOL="$ROOT/backend/scripts/self_improve_state.py"
VENV_PYTHON="$ROOT/backend/.venv/bin/python"
RUN_WORKTREE=""
WORKERS=1
HEARTBEAT_INTERVAL=900   # 15 min
STALE_TIMEOUT=1800       # 30 min

mkdir -p "$(dirname "$LOG")" "$RUNS_DIR"

log() {
  echo "[$(date '+%F %T')] $*" >> "$LOG"
}

# ── single-worker infrastructure (unchanged from original) ──────────────

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

acquire_lock() {
  if [[ -f "$LOCK" ]]; then
    if [[ -s "$LOCK" ]]; then
      log "=== self-improve 失败：发现非空旧文件锁 ==="
      return 1
    fi
    rm -f "$LOCK"
  fi

  if ! mkdir "$LOCK" 2>/dev/null; then
    running_pid="$(sed -n '1p' "$LOCK/pid" 2>/dev/null || true)"
    if [[ "$running_pid" =~ ^[0-9]+$ ]] && kill -0 "$running_pid" 2>/dev/null; then
      log "=== self-improve 跳过：PID $running_pid 正在运行 ==="
      return 2
    fi
    [[ -d "$LOCK" ]] && rm -f "$LOCK/pid"
    if ! rmdir "$LOCK" 2>/dev/null || ! mkdir "$LOCK" 2>/dev/null; then
      log "=== self-improve 跳过：锁状态无法安全恢复 ==="
      return 1
    fi
    log "=== self-improve 已恢复失活锁 ==="
  fi
  echo "$$" > "$LOCK/pid"
  return 0
}

run_single_worker() {
  acquire_lock
  local lock_rc=$?
  if [[ $lock_rc -ne 0 ]]; then
    exit 0
  fi
  trap cleanup EXIT INT TERM

  if ! git -C "$ROOT" rev-parse --verify "$BASE_REF^{commit}" >/dev/null 2>&1; then
    log "=== self-improve 失败：基线 $BASE_REF 不存在 ==="
    exit 1
  fi

  local run_id
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
  local rc=$?
  set -e
  log "=== self-improve 退出 (PID $$, code=$rc) ==="
  exit "$rc"
}

# ── multi-worker infrastructure ─────────────────────────────────────────

LAUNCHER_LOCK="$ROOT/.git-self-improve-launcher.lock"

acquire_launcher_lock() {
  if ! mkdir "$LAUNCHER_LOCK" 2>/dev/null; then
    log "=== 启动器跳过：另一个 --workers N 已在运行 ==="
    exit 0
  fi
  echo "$$" > "$LAUNCHER_LOCK/pid"
}

release_launcher_lock() {
  rm -f "$LAUNCHER_LOCK/pid"
  rmdir "$LAUNCHER_LOCK" 2>/dev/null || true
}

launcher_cleanup() {
  local rc=$?
  # Kill all worker PIDs we spawned
  if [[ -n "${WORKER_PIDS:-}" ]]; then
    for pid in $WORKER_PIDS; do
      kill -TERM "$pid" 2>/dev/null || true
    done
    sleep 2
    for pid in $WORKER_PIDS; do
      kill -9 "$pid" 2>/dev/null || true
    done
  fi
  release_launcher_lock
  return "$rc"
}

state_cmd() {
  "$VENV_PYTHON" "$STATE_TOOL" "$@"
}

run_one_worker() {
  local worker_index="$1"
  local total="$2"
  local run_id="w${worker_index}-$(date '+%Y%m%d-%H%M%S')-$$"
  local wid="w${worker_index}-${run_id}"

  # ── 1. Claim a direction ──────────────────────────────────────────
  local claim_output
  claim_output="$(state_cmd claim-direction \
    --researcher-id "$wid" \
    --max-workers "$total" 2>&1)"
  local claim_rc=$?

  if [[ $claim_rc -ne 0 ]]; then
    log "w${worker_index}: claim-direction 失败 — $claim_output"
    return 1
  fi

  local direction
  direction="$(echo "$claim_output" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("direction",""))' 2>/dev/null || true)"
  local ok
  ok="$(echo "$claim_output" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("ok",False))' 2>/dev/null || true)"

  if [[ "$ok" != "True" ]]; then
    log "w${worker_index}: 无法分配方向 — $claim_output"
    return 1
  fi

  log "w${worker_index}: 已分配方向 [$direction], researcher=$wid"

  # ── 2. Create worktree ────────────────────────────────────────────
  local worker_wt="$RUNS_DIR/$run_id"
  if ! git -C "$ROOT" worktree add --detach "$worker_wt" "$BASE_REF" >> "$LOG" 2>&1; then
    log "w${worker_index}: 无法创建 worktree"
    state_cmd release-direction --researcher-id "$wid" 2>/dev/null || true
    return 1
  fi

  # ── 3. Run Claude agent (in background subshell) ──────────────────
  (
    cd "$worker_wt" || exit 1
    export SELF_IMPROVE_ISOLATED_RUN=1
    export SELF_IMPROVE_RUN_ID="$run_id"
    export SELF_IMPROVE_MULTI_WORKER=1
    export SELF_IMPROVE_WORKER_INDEX="$worker_index"
    export SELF_IMPROVE_TOTAL_WORKERS="$total"
    export SELF_IMPROVE_RESEARCHER_ID="$wid"
    export SELF_IMPROVE_CLAIMED_DIRECTION="$direction"
    export SELF_IMPROVE_CHAMPION_COMMIT_AT_START

    log "w${worker_index}: Claude 启动 ($wid, direction=$direction)"

    # heartbeat loop in background (every HEARTBEAT_INTERVAL seconds)
    (
      while true; do
        sleep "$HEARTBEAT_INTERVAL"
        hb_out="$(state_cmd heartbeat --researcher-id "$wid" 2>&1)" || true
        stale="$(echo "$hb_out" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("stale_champion",False))' 2>/dev/null || echo "False")"
        if [[ "$stale" == "True" ]]; then
          log "w${worker_index}: champion 已过期，发送退出信号"
          kill -TERM "$$" 2>/dev/null || true  # signal the Claude process
          break
        fi
      done
    ) &
    local hb_pid=$!

    # run Claude
    set +e
    "$CLAUDE_BIN" -p "/self-improve" --output-format text
    local claude_rc=$?
    set -e

    kill "$hb_pid" 2>/dev/null || true
    wait "$hb_pid" 2>/dev/null || true

    exit "$claude_rc"
  ) &
  local worker_pid=$!
  WORKER_PIDS="$WORKER_PIDS $worker_pid"

  log "w${worker_index}: spawned PID $worker_pid"
  return 0
}

multi_worker_cleanup_worker() {
  local worker_index="$1"
  local run_id="$2"
  local wid="$3"

  state_cmd release-direction --researcher-id "$wid" 2>/dev/null || true

  local worker_wt="$RUNS_DIR/$run_id"
  if [[ -d "$worker_wt" ]]; then
    if git -C "$worker_wt" diff --quiet --ignore-submodules -- 2>/dev/null \
      && [[ -z "$(git -C "$worker_wt" status --short 2>/dev/null)" ]]; then
      head_commit="$(git -C "$worker_wt" rev-parse HEAD 2>/dev/null || true)"
      containing_branches="$(git -C "$ROOT" branch --format='%(refname:short)' --contains "$head_commit" 2>/dev/null || true)"
      if [[ -n "$containing_branches" ]]; then
        git -C "$ROOT" worktree remove "$worker_wt" >/dev/null 2>&1 || true
      else
        log "w${worker_index}: 保留含未挂分支提交的 worktree: $worker_wt"
      fi
    else
      log "w${worker_index}: 保留含未提交改动的 worktree: $worker_wt"
    fi
  fi
}

run_multi_worker() {
  acquire_launcher_lock
  trap launcher_cleanup EXIT INT TERM

  if ! git -C "$ROOT" rev-parse --verify "$BASE_REF^{commit}" >/dev/null 2>&1; then
    log "=== 启动器失败：基线 $BASE_REF 不存在 ==="
    exit 1
  fi

  local total="$WORKERS"
  log "=== 启动器：启动 $total 个并发研究员 ==="

  # ── Spawn all workers ───────────────────────────────────────────────
  declare -a WORKER_WT
  declare -a WORKER_IDS
  local spawned=0
  WORKER_PIDS=""

  for ((i=1; i<=total; i++)); do
    local run_id="w${i}-$(date '+%Y%m%d-%H%M%S')-$$"
    local wid="w${i}-${run_id}"

    # claim direction
    local claim_output
    claim_output="$(state_cmd claim-direction \
      --researcher-id "$wid" \
      --max-workers "$total" 2>&1)" || {
        log "w${i}: claim 失败 — $claim_output"
        continue
      }

    local ok
    ok="$(echo "$claim_output" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("ok",False))' 2>/dev/null || true)"
    local direction=""
    direction="$(echo "$claim_output" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("direction",""))' 2>/dev/null || true)"

    if [[ "$ok" != "True" ]]; then
      log "w${i}: 无可用方向，跳过 — $claim_output"
      continue
    fi

    log "w${i}: 方向 [$direction], researcher=$wid"

    # create worktree
    local worker_wt="$RUNS_DIR/$run_id"
    if ! git -C "$ROOT" worktree add --detach "$worker_wt" "$BASE_REF" >> "$LOG" 2>&1; then
      log "w${i}: worktree 创建失败"
      state_cmd release-direction --researcher-id "$wid" 2>/dev/null || true
      continue
    fi

    WORKER_WT+=("$run_id")
    WORKER_IDS+=("$wid")

    # launch in background
    (
      cd "$worker_wt" || exit 1
      export SELF_IMPROVE_ISOLATED_RUN=1
      export SELF_IMPROVE_RUN_ID="$run_id"
      export SELF_IMPROVE_MULTI_WORKER=1
      export SELF_IMPROVE_WORKER_INDEX="$i"
      export SELF_IMPROVE_TOTAL_WORKERS="$total"
      export SELF_IMPROVE_RESEARCHER_ID="$wid"
      export SELF_IMPROVE_CLAIMED_DIRECTION="$direction"

      # heartbeat loop
      (
        while true; do
          sleep "$HEARTBEAT_INTERVAL"
          hb_out="$(state_cmd heartbeat --researcher-id "$wid" 2>&1)" || true
          stale="$(echo "$hb_out" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("stale_champion",False))' 2>/dev/null || echo "False")"
          if [[ "$stale" == "True" ]]; then
            log "w${i}: champion 过期 → 发送 TERM"
            kill -TERM "$$" 2>/dev/null || true
            break
          fi
        done
      ) &
      HB_PID=$!

      set +e
      "$CLAUDE_BIN" -p "/self-improve" --output-format text
      rc=$?
      set -e

      kill "$HB_PID" 2>/dev/null || true
      wait "$HB_PID" 2>/dev/null || true

      # exit 42 = restart (champion changed, re-claim)
      exit "$rc"
    ) &
    WORKER_PIDS="$WORKER_PIDS $!"
    ((spawned++))
  done

  log "=== 启动器：已启动 $spawned/$total 个 researcher，等待完成 ==="

  # ── Wait and restart workers as needed ──────────────────────────────
  local remaining_pids="$WORKER_PIDS"
  while [[ -n "${remaining_pids// /}" ]]; do
    local -a new_remaining=()
    for pid in $remaining_pids; do
      if kill -0 "$pid" 2>/dev/null; then
        new_remaining+=("$pid")
      else
        # worker exited — check exit code
        local w_rc=0
        wait "$pid" 2>/dev/null || w_rc=$?
        log "PID $pid 退出, code=$w_rc"
        if [[ $w_rc -eq 42 ]]; then
          log "PID $pid 请求重启 (code 42), 重新分配方向…"
          # restart this worker slot with a fresh direction
          local w_idx=1
          # find index
          for ((j=0; j<${#WORKER_PIDS_PLACEHOLDER:-0}; j++)); do
            :  # placeholder — in practice we'd look up the right index
          done
        fi
      fi
    done
    remaining_pids="${new_remaining[*]}"
    if [[ -n "${remaining_pids// /}" ]]; then
      sleep 30
    fi
  done

  # ── Cleanup all workers ────────────────────────────────────────────
  for idx in "${!WORKER_IDS[@]}"; do
    local wid="${WORKER_IDS[$idx]}"
    local rid="${WORKER_WT[$idx]}"
    multi_worker_cleanup_worker "$((idx+1))" "$rid" "$wid"
  done

  log "=== 启动器：全部研究员完成 ==="
}

# ── Entry point ──────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workers)
      WORKERS="$2"; shift 2 ;;
    *)
      echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ "$WORKERS" -gt 1 ]]; then
  run_multi_worker
else
  run_single_worker
fi
