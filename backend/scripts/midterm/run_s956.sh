#!/bin/bash
ROOT="/Users/wangao/Desktop/ashare-shortline-system/backend"
CD="$ROOT/scripts/midterm"; PY="$ROOT/.venv/bin/python"
OUTD="$ROOT/output/research/s956"; OUT="$OUTD/res.json"; LOG="$OUTD/run.log"
mkdir -p "$OUTD"; echo "START_S956 $(date)" | tee "$LOG"; rm -f "$OUT" "$OUTD"/trades_*.csv "$OUTD"/eval.txt
cd "$CD"
busy() { pgrep -f '/\.venv/bin/python .*(trpssuegvol_o25_s956|opincsuegvol_s957)\.py' >/dev/null; }
routine_busy() {
  local pid etime mins
  pid=$(pgrep -f 'sync_xiaoshi_min_all\.py' | head -1) || return 1
  etime=$(ps -p "$pid" -o etime= 2>/dev/null | tr -d ' ')
  [ -z "$etime" ] && return 1
  if [[ "$etime" == *-* ]]; then return 0; fi
  if [[ "$etime" =~ ^([0-9]+):([0-9]+):([0-9]+)$ ]]; then
    mins=$((10#${BASH_REMATCH[1]}*60 + 10#${BASH_REMATCH[2]}))
  elif [[ "$etime" =~ ^([0-9]+):([0-9]+)$ ]]; then
    mins=$((10#${BASH_REMATCH[1]}))
  else
    mins=999
  fi
  [ "$mins" -lt 18 ]
}
morning_yield() {
  local hm; hm=$(date '+%H%M')
  [ "$hm" -ge 0825 ] && [ "$hm" -lt 1000 ]
}
run_ph() {
  local name="$1" s="$2" lend="$3"
  if [ "${ALLOW_MORNING:-0}" != "1" ] && morning_yield; then echo "MORNING_YIELD $(date)" | tee -a "$LOG"; return 99; fi
  while busy || routine_busy; do
    if [ "${ALLOW_MORNING:-0}" != "1" ] && morning_yield; then echo "MORNING_YIELD $(date)" | tee -a "$LOG"; return 99; fi
    echo "wait other/routine $(date +%H:%M:%S)" >>"$LOG"; sleep 20
  done
  free_mb=$(sysctl -n vm.swapusage | awk -F'free = ' '{print $2}' | awk '{print int($1)}')
  while [ "$free_mb" -lt 800 ]; do
    if [ "${ALLOW_MORNING:-0}" != "1" ] && morning_yield; then echo "MORNING_YIELD $(date)" | tee -a "$LOG"; return 99; fi
    echo "wait swap_free_mb=$free_mb $(date +%H:%M:%S)" >>"$LOG"; sleep 30
    free_mb=$(sysctl -n vm.swapusage | awk -F'free = ' '{print $2}' | awk '{print int($1)}')
  done
  echo "== $name $(date +%H:%M:%S) swap_free_mb=$free_mb" | tee -a "$LOG"
  LT_START="$s" LT_END="$lend" /usr/bin/time -l env PYTHONPATH=../..:. "$PY" trpssuegvol_o25_s956.py "$name" "$OUT" >>"$LOG" 2>&1
  echo "phase_exit=$?" | tee -a "$LOG"
}
for p in "P19 2019:2019-01-01:2020-04-29" "P20 2020:2020-01-01:2021-04-30" "P21 2021:2021-01-01:2022-04-30" "W0 2022-04~2023-03:2022-04-01:2023-07-29" "W00 2023-04~12:2023-04-01:2024-04-29" "W1 2024:2024-01-01:2025-04-30" "W2 2025-01~09:2025-01-01:2026-01-28" "R12 2025-10~2026-10:2025-10-01:2026-10-16" "N3 2024-01~2026-10:2024-01-01:2026-10-16"; do
  IFS=: read -r n a b <<< "$p"
  run_ph "$n" "$a" "$b" || { ec=$?; [ "$ec" = "99" ] && echo "STOPPED_FOR_MORNING_S956" | tee -a "$LOG"; exit 0; }
done
echo "DONE_S956 $(date)" | tee -a "$LOG"
"$PY" s286_287_eval.py "$OUT" TRPSSUEGVOL_o25_h20 25 | tee "$OUTD/eval.txt"
"$PY" - <<'PY' | tee -a "$OUTD/eval.txt"
import json
r=json.load(open("/Users/wangao/Desktop/ashare-shortline-system/backend/output/research/s956/res.json"))
b,d=r["TRPSSUEGVOL_base"], r["TRPSSUEGVOL_o25_h20"]
R,N="R12 2025-10~2026-10","N3 2024-01~2026-10"
print("B门对照 base:", {k: (b[k].get("avg"), b[k].get("pf"), b[k].get("mdd"), b[k].get("n")) for k in (R,N)})
print("o25:", {k: (d[k].get("avg"), d[k].get("pf"), d[k].get("mdd"), d[k].get("n")) for k in (R,N)})
ok = (d[R]["avg"]>=b[R]["avg"] and d[N]["avg"]>=b[N]["avg"] and abs(d[N]["mdd"])<=abs(b[N]["mdd"])+1e-9)
print("B门", "过" if ok else "否")
PY
