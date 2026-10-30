#!/bin/bash
# §RV-A~F 引擎口径逐段串行（同 run_s348 窗口）；等其他研究任务、内存。
ROOT="/Users/wangao/Desktop/ashare-shortline-system/backend"
CD="$ROOT/scripts/midterm"; PY="$ROOT/.venv/bin/python"
OUTD="$ROOT/output/research/rv1030/eng"; OUT="$OUTD/res.json"; LOG="$OUTD/run.log"
mkdir -p "$OUTD"; rm -f "$OUT"; echo "START_RV1030 $(date)" | tee "$LOG"
cd "$CD"
busy() { pgrep -f "run_s3[0-9][0-9]\.sh|lt_research1[0-9][0-9]\.py|research_s3|cap_trades|research_s282|rso_s2|xbx_" >/dev/null; }
memfree() { memory_pressure 2>/dev/null | awk -F': ' '/free percentage/{print int($2)}'; }
for p in "P19 2019:2019-01-01:2020-04-29" "P20 2020:2020-01-01:2021-04-30" "P21 2021:2021-01-01:2022-04-30" "W0 2022-04~2023-03:2022-04-01:2023-07-29" "W00 2023-04~12:2023-04-01:2024-04-29" "W1 2024:2024-01-01:2025-04-30" "W2 2025-01~09:2025-01-01:2026-01-28" "R12 2025-10~2026-10:2025-10-01:2026-10-16" "N3 2024-01~2026-10:2024-01-01:2026-10-16"; do
  IFS=: read -r n a b <<< "$p"
  while busy; do echo "wait other research job $(date +%H:%M:%S)" >>"$LOG"; sleep 20; done
  while [ "$(memfree)" -lt 25 ]; do echo "wait mem $(memfree)" >>"$LOG"; sleep 30; done
  echo "== $n $(date +%H:%M:%S) memfree=$(memfree)" | tee -a "$LOG"
  LT_START="$a" LT_END="$b" /usr/bin/time -l env PYTHONPATH=../..:. "$PY" rv1030_cand.py "$n" "$OUT" >>"$LOG" 2>&1
  echo "phase_exit=$?" | tee -a "$LOG"
done
echo "DONE_RV1030 $(date)" | tee -a "$LOG"
: > "$OUTD/eval.txt"
for v in EA_base EA_d0 EA_mv EA_combo EA_first FA_base FA_mv FA_d0; do "$PY" s286_287_eval.py "$OUT" "$v" 25 | tee -a "$OUTD/eval.txt"; done
