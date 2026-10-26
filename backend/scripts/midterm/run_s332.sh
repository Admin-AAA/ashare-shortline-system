#!/bin/bash
# §332 LVZ_h5 + §333 FDV_h5 同一进程逐段串行（一次只跑一个阶段）。先等其他研究进程结束；
# 内存闸门：swap 空闲 ≥400MB 或 memory_pressure 系统空闲 ≥40% 才开下一段，否则等待。
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CD="$ROOT/scripts/midterm"; PY="$ROOT/.venv/bin/python"
OUTD="$ROOT/output/research/s332"; OUT="$OUTD/res.json"; LOG="$OUTD/run.log"
mkdir -p "$OUTD"; echo "START_S332 $(date) root=$ROOT" | tee "$LOG"; rm -f "$OUT"
cd "$CD"
busy() { pgrep -f "run_s3[0-2][0-9]\.sh|lt_research1[0-2][0-6]\.py|research_s3[0-2][0-9]|cap_trades|research_s282|_s32[0-9]\.py|rso_s2|xbx_" >/dev/null; }
mem_ok() {
  local sw pf
  sw=$(sysctl -n vm.swapusage | awk -F'free = ' '{print $2}' | awk '{print int($1)}')
  pf=$(memory_pressure 2>/dev/null | awk -F': ' '/free percentage/{print int($2)}')
  echo "swap_free_mb=$sw mem_free_pct=$pf"
  [ "${sw:-0}" -ge 400 ] || [ "${pf:-0}" -ge 40 ]
}
run_ph() {
  local name="$1" s="$2" lend="$3"
  while busy; do echo "wait other research job $(date +%H:%M:%S)" >>"$LOG"; sleep 20; done
  until mem_ok >>"$LOG"; do sleep 30; done
  echo "== $name $(date +%H:%M:%S)" | tee -a "$LOG"
  LT_START="$s" LT_END="$lend" /usr/bin/time -l env PYTHONPATH=../..:. "$PY" ../shortline_research/lvz_fdv_s332.py "$name" "$OUT" >>"$LOG" 2>&1
  echo "phase_exit=$?" | tee -a "$LOG"
}
for p in "P19 2019:2019-01-01:2020-04-29" "P20 2020:2020-01-01:2021-04-30" "P21 2021:2021-01-01:2022-04-30" "W0 2022-04~2023-03:2022-04-01:2023-07-29" "W00 2023-04~12:2023-04-01:2024-04-29" "W1 2024:2024-01-01:2025-04-30" "W2 2025-01~09:2025-01-01:2026-01-28" "R12 2025-10~2026-10:2025-10-01:2026-10-26" "N3 2024-01~2026-10:2024-01-01:2026-10-26"; do IFS=: read -r n a b <<< "$p"; run_ph "$n" "$a" "$b"; done
echo "DONE_S332 $(date)" | tee -a "$LOG"
for n in LVZ_h5 FDV_h5; do "$PY" s286_287_eval.py "$OUT" "$n" 35 | tee -a "$OUTD/eval.txt"; done
"$PY" s332_eval.py "$OUT" | tee -a "$OUTD/eval.txt"
