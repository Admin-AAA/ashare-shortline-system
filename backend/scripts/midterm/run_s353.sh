#!/bin/bash
ROOT="/Users/wangao/Desktop/ashare-shortline-system/backend"
CD="$ROOT/scripts/midterm"; PY="$ROOT/.venv/bin/python"
OUTD="$ROOT/output/research/s353"; OUT="$OUTD/res.json"; LOG="$OUTD/run.log"
mkdir -p "$OUTD"; echo "START_S353 $(date)" | tee "$LOG"; rm -f "$OUT"
cd "$CD"
busy() { pgrep -f "research_s352|lt_research137\.py|run_s353\.sh|lt_research13[4-6]|research_s3|cap_trades|research_s282" >/dev/null; }
run_ph() {
  local name="$1" s="$2" lend="$3"
  while busy; do echo "wait other research job $(date +%H:%M:%S)" >>"$LOG"; sleep 20; done
  free_mb=$(sysctl -n vm.swapusage | awk -F'free = ' '{print $2}' | awk '{print int($1)}')
  memfree() { memory_pressure 2>/dev/null | awk -F': ' '/free percentage/{print int($2)}'; }
  while [ "${free_mb:-0}" -lt 400 ] && [ "$(memfree)" -lt 25 ]; do echo "wait swap free=$free_mb" >>"$LOG"; sleep 30; free_mb=$(sysctl -n vm.swapusage | awk -F'free = ' '{print $2}' | awk '{print int($1)}'); done
  echo "== $name $(date +%H:%M:%S) swap_free_mb=$free_mb" | tee -a "$LOG"
  LT_START="$s" LT_END="$lend" /usr/bin/time -l env PYTHONPATH=../..:. "$PY" lt_research137.py "$name" "$OUT" >>"$LOG" 2>&1
  echo "phase_exit=$?" | tee -a "$LOG"
}
for p in "P19 2019:2019-01-01:2020-04-29" "P20 2020:2020-01-01:2021-04-30" "P21 2021:2021-01-01:2022-04-30" "W0 2022-04~2023-03:2022-04-01:2023-07-29" "W00 2023-04~12:2023-04-01:2024-04-29" "W1 2024:2024-01-01:2025-04-30" "W2 2025-01~09:2025-01-01:2026-01-28" "R12 2025-10~2026-10:2025-10-01:2026-10-16" "N3 2024-01~2026-10:2024-01-01:2026-10-16"; do IFS=: read -r n a b <<< "$p"; run_ph "$n" "$a" "$b"; done
echo "DONE_S353 $(date)" | tee -a "$LOG"
: > "$OUTD/eval.txt"
for n in RP_base RP_combo; do "$PY" s286_287_eval.py "$OUT" "$n" 25 | tee -a "$OUTD/eval.txt"; done
"$PY" s353_eval.py "$OUT" | tee -a "$OUTD/eval.txt"
