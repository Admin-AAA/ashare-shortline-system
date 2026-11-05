#!/bin/bash
# §996–§1000 串行运行：一次一个进程、一个阶段；swap 空闲 <400MB 则等待。代码取自本脚本所在目录，数据只读主工作树。
HERE="$(cd "$(dirname "$0")/.." && pwd)"
MAIN="/Users/wangao/Desktop/ashare-shortline-system/backend"
PY="$MAIN/.venv/bin/python"
OUTD="$MAIN/output/research/s996"; OUT="$OUTD/res.json"; LOG="$OUTD/run.log"
mkdir -p "$OUTD"; echo "START_S996 $(date) code=$HERE" | tee "$LOG"; rm -f "$OUT"
cd "$HERE/midterm"
run_ph() {
  local name="$1" s="$2" lend="$3"
  free_mb=$(sysctl -n vm.swapusage | awk -F'free = ' '{print $2}' | awk '{print int($1)}')
  while [ "${free_mb:-0}" -lt 400 ]; do echo "wait swap free=$free_mb" >>"$LOG"; sleep 30; free_mb=$(sysctl -n vm.swapusage | awk -F'free = ' '{print $2}' | awk '{print int($1)}'); done
  echo "== $name $(date +%H:%M:%S) swap_free_mb=$free_mb" | tee -a "$LOG"
  LT_START="$s" LT_END="$lend" /usr/bin/time -l env PYTHONPATH="$MAIN:.:../shortline_research" "$PY" ../shortline_research/evdip_s996.py "$name" "$OUT" >>"$LOG" 2>&1
  echo "phase_exit=$?" | tee -a "$LOG"
}
for p in "R12 2025-10~2026-10:2025-10-01:2026-10-30" "N3 2024-01~2026-10:2024-01-01:2026-10-30" "EXT 2024-01~2025-09:2024-01-01:2025-10-31" "W0 2022-04~2023-03:2022-04-01:2023-04-30" "W00 2023-04~12:2023-04-01:2024-01-31" "W1 2024:2024-01-01:2025-01-31" "W2 2025-01~09:2025-01-01:2025-10-31" "P19 2019:2019-01-01:2020-01-31" "P20 2020:2020-01-01:2021-01-31" "P21 2021:2021-01-01:2022-01-31"; do IFS=: read -r n a b <<< "$p"; run_ph "$n" "$a" "$b"; done
echo "DONE_S996 $(date)" | tee -a "$LOG"
"$PY" ../shortline_research/saf_s987_eval.py "$OUT" FA_DIP_h5 DIVP_DIP_h5 BADF_DIP_h5 MIX_DIP_h5 EA_DIP_h5 | tee "$OUTD/eval.txt"
