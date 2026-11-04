#!/bin/bash
# 用法：run_wave2.sh <WHICH> <sec>
WHICH="$1"; SEC="$2"; NAME="$3"
ROOT="/Users/wangao/Desktop/ashare-shortline-system/backend"
CD="$ROOT/scripts/midterm"; PY="$ROOT/.venv/bin/python"
OUTD="$ROOT/output/research/s$SEC"; OUT="$OUTD/res.json"; LOG="$OUTD/run.log"
mkdir -p "$OUTD"; echo "START_S$SEC $(date)" | tee "$LOG"; rm -f "$OUT" "$OUTD"/trades_*.csv "$OUTD"/eval.txt
cd "$CD"
for p in "P19 2019:2019-01-01:2020-04-29" "P20 2020:2020-01-01:2021-04-30" "P21 2021:2021-01-01:2022-04-30" "W0 2022-04~2023-03:2022-04-01:2023-07-29" "W00 2023-04~12:2023-04-01:2024-04-29" "W1 2024:2024-01-01:2025-04-30" "W2 2025-01~09:2025-01-01:2026-01-28" "R12 2025-10~2026-10:2025-10-01:2026-10-16" "N3 2024-01~2026-10:2024-01-01:2026-10-16"; do
  IFS=: read -r n a b <<< "$p"
  free_mb=$(sysctl -n vm.swapusage | awk -F'free = ' '{print $2}' | awk '{print int($1)}')
  while [ "$free_mb" -lt 500 ]; do sleep 30; free_mb=$(sysctl -n vm.swapusage | awk -F'free = ' '{print $2}' | awk '{print int($1)}'); done
  echo "== $n $(date +%H:%M:%S)" >>"$LOG"
  LT_START="$a" LT_END="$b" env PYTHONPATH=../..:. "$PY" suegvol_w2_s980_981.py "$WHICH" "$n" "$OUT" >>"$LOG" 2>&1
  echo "phase_exit=$?" >>"$LOG"
done
echo "DONE_S$SEC $(date)" >>"$LOG"
"$PY" s286_287_eval.py "$OUT" "$NAME" 25 > "$OUTD/eval.txt" 2>&1
