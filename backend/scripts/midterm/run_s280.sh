#!/bin/bash
# §280 串行运行：先等其他 bot 的研究批次（run_s27x/run_s278*/run_s279* 及任何 lt_research*.py / rso_*.py 进程）结束，再逐段单进程跑；swap 空闲<400MB 则等待。
ROOT="/Users/wangao/Desktop/ashare-shortline-system/backend"
CD="$ROOT/scripts/midterm"; PY="$ROOT/.venv/bin/python"
OUTD="$ROOT/output/research/s280"; OUT="$OUTD/res.json"; LOG="$OUTD/run.log"
mkdir -p "$OUTD"; echo "START_S280 $(date)" | tee "$LOG"; rm -f "$OUT"
cd "$CD"
busy() { pgrep -f "run_s27[0-9]|run_s278|run_s279|lt_research[0-9]*\.py|rso_s2[0-9]*\.py|xbx_" >/dev/null; }
run_ph() {
  local name="$1" s="$2" lend="$3"
  while busy; do echo "wait other research job $(date +%H:%M:%S)" >>"$LOG"; sleep 30; done
  free_mb=$(sysctl -n vm.swapusage | awk -F'free = ' '{print $2}' | awk '{print int($1)}')
  while [ "${free_mb:-0}" -lt 400 ]; do echo "wait swap free=$free_mb" >>"$LOG"; sleep 30; free_mb=$(sysctl -n vm.swapusage | awk -F'free = ' '{print $2}' | awk '{print int($1)}'); done
  echo "== $name $(date +%H:%M:%S) swap_free_mb=$free_mb" | tee -a "$LOG"
  LT_START="$s" LT_END="$lend" /usr/bin/time -l env PYTHONPATH=../..:. "$PY" ../shortline_research/rso_s280.py "$name" "$OUT" >>"$LOG" 2>&1
  echo "phase_exit=$?" | tee -a "$LOG"
}
for p in "W0 2022-04~2023-03:2022-04-01:2023-07-29" "W00 2023-04~12:2023-04-01:2024-04-29" "W1 2024:2024-01-01:2025-04-30" "W2 2025-01~09:2025-01-01:2026-01-28" "R12 2025-10~2026-10:2025-10-01:2026-10-16" "N3 2024-01~2026-10:2024-01-01:2026-10-16"; do IFS=: read -r n a b <<< "$p"; run_ph "$n" "$a" "$b"; done
echo "DONE_S280 $(date)" | tee -a "$LOG"
"$PY" ../shortline_research/rso_s280_eval.py "$OUT" | tee "$OUTD/eval.txt"
