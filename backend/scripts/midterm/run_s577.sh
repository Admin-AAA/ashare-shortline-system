#!/bin/bash
ROOT="/Users/wangao/Desktop/ashare-shortline-system/backend"
CD="$ROOT/scripts/midterm"; PY="$ROOT/.venv/bin/python"
OUTD="$ROOT/output/research/s577"; OUT="$OUTD/res.json"; LOG="$OUTD/run.log"
mkdir -p "$OUTD"; echo "START_S577 $(date)" | tee "$LOG"; rm -f "$OUT" "$OUTD"/trades_*.csv
cd "$CD"
busy() { pgrep -f '/\.venv/bin/python .*(finavol_d055_s576|fcfevol_s577)\.py' >/dev/null; }
routine_busy() {
  # 仅对「新鲜例行」让行：启动未满 18 分钟；卡住/空转的长尾不阻塞挖掘
  local pid etime mins
  pid=$(pgrep -f 'sync_xiaoshi_min_all\.py' | head -1) || return 1
  etime=$(ps -p "$pid" -o etime= 2>/dev/null | tr -d ' ')
  [ -z "$etime" ] && return 1
  if [[ "$etime" == *-* ]]; then return 0; fi  # >1 day → still treat busy rare
  if [[ "$etime" == *:*:* ]]; then mins=$((10#${etime%%:*}*60 + 10#${etime#*:})); mins=${mins%%:*}; else
    # MM:SS
    mins=$((10#${etime%%:*}))
  fi
  # recompute properly
  if [[ "$etime" =~ ^([0-9]+):([0-9]+):([0-9]+)$ ]]; then
    mins=$((10#${BASH_REMATCH[1]}*60 + 10#${BASH_REMATCH[2]}))
  elif [[ "$etime" =~ ^([0-9]+):([0-9]+)$ ]]; then
    mins=$((10#${BASH_REMATCH[1]}))
  else
    mins=999
  fi
  [ "$mins" -lt 18 ]
}
run_ph() {
  local name="$1" s="$2" lend="$3"
  while busy || routine_busy; do echo "wait other/routine $(date +%H:%M:%S)" >>"$LOG"; sleep 20; done
  free_mb=$(sysctl -n vm.swapusage | awk -F'free = ' '{print $2}' | awk '{print int($1)}')
  echo "== $name $(date +%H:%M:%S) swap_free_mb=$free_mb" | tee -a "$LOG"
  LT_START="$s" LT_END="$lend" /usr/bin/time -l env PYTHONPATH=../..:. "$PY" fcfevol_s577.py "$name" "$OUT" >>"$LOG" 2>&1
  echo "phase_exit=$?" | tee -a "$LOG"
}
for p in "P19 2019:2019-01-01:2020-04-29" "P20 2020:2020-01-01:2021-04-30" "P21 2021:2021-01-01:2022-04-30" "W0 2022-04~2023-03:2022-04-01:2023-07-29" "W00 2023-04~12:2023-04-01:2024-04-29" "W1 2024:2024-01-01:2025-04-30" "W2 2025-01~09:2025-01-01:2026-01-28" "R12 2025-10~2026-10:2025-10-01:2026-10-16" "N3 2024-01~2026-10:2024-01-01:2026-10-16"; do IFS=: read -r n a b <<< "$p"; run_ph "$n" "$a" "$b"; done
echo "DONE_S577 $(date)" | tee -a "$LOG"
"$PY" s286_287_eval.py "$OUT" FCFEVOL_h20 25 | tee "$OUTD/eval.txt"
