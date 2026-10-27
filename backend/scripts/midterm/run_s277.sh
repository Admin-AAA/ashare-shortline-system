#!/bin/bash
set -e
ROOT="/Users/wangao/Desktop/ashare-shortline-system/backend"
CD="$ROOT/scripts/midterm"
OUT="$ROOT/output/research/s277/res.json"
LOG="$ROOT/output/research/s277/run.log"
PY="$ROOT/.venv/bin/python"
echo "START_S277 $(date)" | tee "$LOG"
rm -f "$OUT"
cd "$CD"
run_ph() {
  local name="$1" s="$2" lend="$3"
  free_mb=$(sysctl -n vm.swapusage | awk -F'free = ' '{print $2}' | awk '{print int($1)}')
  echo "== $name $(date +%H:%M:%S) LT_START=$s LT_END=$lend swap_free_mb=$free_mb" | tee -a "$LOG"
  while [ "${free_mb:-0}" -lt 400 ]; do
    echo "wait swap free=$free_mb <400 $(date +%H:%M:%S)" | tee -a "$LOG"
    sleep 30
    free_mb=$(sysctl -n vm.swapusage | awk -F'free = ' '{print $2}' | awk '{print int($1)}')
  done
  LT_START="$s" LT_END="$lend" /usr/bin/time -l env PYTHONPATH=../..:. "$PY" lt_research104.py "$name" "$OUT" >>"$LOG" 2>&1
  echo "phase_exit=$?" | tee -a "$LOG"
}
run_ph "P19 2019" "2019-01-01" "2020-04-29"
run_ph "P20 2020" "2020-01-01" "2021-04-30"
run_ph "P21 2021" "2021-01-01" "2022-04-30"
run_ph "W0 2022-04~2023-03" "2022-04-01" "2023-07-29"
run_ph "W00 2023-04~12" "2023-04-01" "2024-04-29"
run_ph "W1 2024" "2024-01-01" "2025-04-30"
run_ph "W2 2025-01~09" "2025-01-01" "2026-01-28"
run_ph "R12 2025-10~2026-10" "2025-10-01" "2026-10-16"
run_ph "N3 2024-01~2026-10" "2024-01-01" "2026-10-16"
echo "DONE_S277 $(date)" | tee -a "$LOG"
"$PY" lt_research104_eval.py "$OUT" | tee "$ROOT/output/research/s277/eval.txt"
