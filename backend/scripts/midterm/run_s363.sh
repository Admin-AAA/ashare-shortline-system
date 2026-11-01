#!/bin/bash
ROOT="/Users/wangao/Desktop/ashare-shortline-system/backend"
CD="$ROOT/scripts/midterm"; PY="$ROOT/.venv/bin/python"
OUTD="$ROOT/output/research/s363"; OUT="$OUTD/res.json"; LOG="$OUTD/run.log"
mkdir -p "$OUTD"; echo "START_S363 $(date)" | tee "$LOG"; rm -f "$OUT" "$OUTD"/trades_*.csv
cd "$CD"
busy() { pgrep -fl 'lt_research14[2-9]|fdu_s362|run_s36[23]|research_s36' | grep -v "$$" >/dev/null; }
run_ph() {
  local name="$1" s="$2" lend="$3"
  while busy; do echo "wait other $(date +%H:%M:%S)" >>"$LOG"; sleep 15; done
  free_mb=$(sysctl -n vm.swapusage | awk -F'free = ' '{print $2}' | awk '{print int($1)}')
  memfree() { memory_pressure 2>/dev/null | awk -F': ' '/free percentage/{print int($2)}'; }
  while [ "${free_mb:-0}" -lt 400 ] && [ "$(memfree)" -lt 25 ]; do echo "wait swap=$free_mb" >>"$LOG"; sleep 25; free_mb=$(sysctl -n vm.swapusage | awk -F'free = ' '{print $2}' | awk '{print int($1)}'); done
  echo "== $name $(date +%H:%M:%S) swap_free_mb=$free_mb" | tee -a "$LOG"
  LT_START="$s" LT_END="$lend" /usr/bin/time -l env PYTHONPATH=../..:. "$PY" lt_research142.py "$name" "$OUT" >>"$LOG" 2>&1
  echo "phase_exit=$?" | tee -a "$LOG"
}
for p in "P19 2019:2019-01-01:2020-04-29" "P20 2020:2020-01-01:2021-04-30" "P21 2021:2021-01-01:2022-04-30" "W0 2022-04~2023-03:2022-04-01:2023-07-29" "W00 2023-04~12:2023-04-01:2024-04-29" "W1 2024:2024-01-01:2025-04-30" "W2 2025-01~09:2025-01-01:2026-01-28" "R12 2025-10~2026-10:2025-10-01:2026-10-16" "N3 2024-01~2026-10:2024-01-01:2026-10-16"; do IFS=: read -r n a b <<< "$p"; run_ph "$n" "$a" "$b"; done
echo "DONE_S363 $(date)" | tee -a "$LOG"
"$PY" s286_287_eval.py "$OUT" FA_base 25 | tee "$OUTD/eval_base.txt"
"$PY" s286_287_eval.py "$OUT" FA_combo 25 | tee "$OUTD/eval_combo.txt"
# relative B gate dump
"$PY" - <<'PY' | tee -a "$OUTD/eval_combo.txt"
import json
r=json.load(open("/Users/wangao/Desktop/ashare-shortline-system/backend/output/research/s363/res.json"))
P8=["P19 2019","P20 2020","P21 2021","W0 2022-04~2023-03","W00 2023-04~12","W1 2024","W2 2025-01~09","R12 2025-10~2026-10"]
R12,N3="R12 2025-10~2026-10","N3 2024-01~2026-10"
b,c=r["FA_base"],r["FA_combo"]
def avg(d,p): return d.get(p,{}).get("avg",-99)
ok_r12=avg(c,R12)>=avg(b,R12); ok_n3=avg(c,N3)>=avg(b,N3)
ok_mdd=c[N3].get("mdd",-99)>=b[N3].get("mdd",-99)
ge6=sum(avg(c,p)>=avg(b,p) for p in P8); ok_w0=avg(c,"W0 2022-04~2023-03")>=avg(b,"W0 2022-04~2023-03")
print(f"B相对: R12 {avg(c,R12):+.2f} vs base {avg(b,R12):+.2f} {'✓' if ok_r12 else '✗'}; N3 {avg(c,N3):+.2f} vs {avg(b,N3):+.2f} {'✓' if ok_n3 else '✗'}; N3 mdd {c[N3].get('mdd')} vs {b[N3].get('mdd')} {'✓' if ok_mdd else '✗'}; 段≥base {ge6}/8 W0 {'✓' if ok_w0 else '✗'}")
print("B判定", "通过" if (ok_r12 and ok_n3 and ok_mdd and ge6>=6 and ok_w0) else "否决")
PY
