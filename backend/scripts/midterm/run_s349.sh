#!/bin/bash
# §349 串行：FA → BT → SDY → NCS，每策略 9 段单进程。
ROOT="/Users/wangao/Desktop/ashare-shortline-system/backend"
CD="$ROOT/scripts/midterm"; PY="$ROOT/.venv/bin/python"
cd "$CD"
busy() { pgrep -f "run_s3([0-3][0-9]|4[0-8])\.sh|lt_research1([0-2][0-9]|3[0-4])\.py|research_s3|cap_trades|research_s282|rso_s2|xbx_" >/dev/null; }
for S in ${STRATS:-FA BT SDY NCS}; do
  OUTD="$ROOT/output/research/s349/$S"; OUT="$OUTD/res.json"; LOG="$OUTD/run.log"
  mkdir -p "$OUTD"; rm -f "$OUT"; echo "START_S349 $S $(date)" | tee "$LOG"
  for p in "P19 2019:2019-01-01:2020-04-29" "P20 2020:2020-01-01:2021-04-30" "P21 2021:2021-01-01:2022-04-30" "W0 2022-04~2023-03:2022-04-01:2023-07-29" "W00 2023-04~12:2023-04-01:2024-04-29" "W1 2024:2024-01-01:2025-04-30" "W2 2025-01~09:2025-01-01:2026-01-28" "R12 2025-10~2026-10:2025-10-01:2026-10-16" "N3 2024-01~2026-10:2024-01-01:2026-10-16"; do
    IFS=: read -r n a b <<< "$p"
    while busy; do echo "wait other research job $(date +%H:%M:%S)" >>"$LOG"; sleep 20; done
    free_mb=$(sysctl -n vm.swapusage | awk -F'free = ' '{print $2}' | awk '{print int($1)}')
    while [ "${free_mb:-0}" -lt 400 ] && [ "$(memory_pressure 2>/dev/null | awk -F': ' '/free percentage/{print int($2)}')" -lt 25 ]; do sleep 30; free_mb=$(sysctl -n vm.swapusage | awk -F'free = ' '{print $2}' | awk '{print int($1)}'); done
    echo "== $S $n $(date +%H:%M:%S) swap_free_mb=$free_mb" | tee -a "$LOG"
    STRAT=$S LPS_ST_SRC=db LT_START="$a" LT_END="$b" /usr/bin/time -l env PYTHONPATH=../..:. "$PY" lt_research135.py "$n" "$OUT" >>"$LOG" 2>&1
    echo "phase_exit=$?" | tee -a "$LOG"
  done
  : > "$OUTD/eval.txt"
  for v in base TS DIV; do "$PY" s286_287_eval.py "$OUT" "${S}_$v" 25 | tee -a "$OUTD/eval.txt"; done
  "$PY" s349_eval.py "$OUT" "$S" | tee -a "$OUTD/eval.txt"
  echo "DONE_S349 $S $(date)" | tee -a "$LOG"
done
