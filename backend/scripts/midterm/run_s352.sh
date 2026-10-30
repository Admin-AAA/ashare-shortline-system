#!/bin/bash
ROOT="/Users/wangao/Desktop/ashare-shortline-system/backend"
PY="$ROOT/.venv/bin/python"
OUTD="$ROOT/output/research/s352"; LOG="$OUTD/run.log"
mkdir -p "$OUTD"; echo "START_S352 $(date)" | tee "$LOG"
cd "$ROOT"
busy() { pgrep -f "lt_research137|run_s353|research_s352|lt_research13[4-6]|cap_trades|research_s282" >/dev/null; }
while busy; do echo "wait $(date +%H:%M:%S)" | tee -a "$LOG"; sleep 20; done
free_mb=$(sysctl -n vm.swapusage | awk -F'free = ' '{print $2}' | awk '{print int($1)}')
while [ "${free_mb:-0}" -lt 400 ]; do echo "wait swap=$free_mb" | tee -a "$LOG"; sleep 30; free_mb=$(sysctl -n vm.swapusage | awk -F'free = ' '{print $2}' | awk '{print int($1)}'); done
echo "run research_s352 swap_free=$free_mb $(date)" | tee -a "$LOG"
/usr/bin/time -l env PYTHONPATH=. "$PY" scripts/research_s352_oversold_improve.py >>"$LOG" 2>&1
echo "exit=$?" | tee -a "$LOG"
"$PY" scripts/midterm/s352_oversold_eval.py | tee -a "$LOG" | tee "$OUTD/eval.txt"
echo "DONE_S352 $(date)" | tee -a "$LOG"
