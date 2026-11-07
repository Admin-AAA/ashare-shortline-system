#!/bin/bash
# §1013–§1017 串行运行：一次一个进程、一个阶段；swap 空闲 <400MB 则等待。
# 只读本地 parquet；输出 backend/output/research/s1013/（gitignore）。
HERE="$(cd "$(dirname "$0")/.." && pwd)"
MAIN="/Users/wangao/Desktop/ashare-shortline-system/backend"
PY="$MAIN/.venv/bin/python"
OUTD="$MAIN/output/research/s1013"; OUT="$OUTD/res.json"; LOG="$OUTD/run.log"
mkdir -p "$OUTD"; echo "START_S1013 $(date) code=$HERE" | tee "$LOG"
cd "$HERE/midterm"
run_ph() {
  local name="$1" s="$2" lend="$3"
  free_mb=$(sysctl -n vm.swapusage | awk -F'free = ' '{print $2}' | awk '{print int($1)}')
  while [ "${free_mb:-0}" -lt 400 ]; do echo "wait swap free=$free_mb" >>"$LOG"; sleep 30; free_mb=$(sysctl -n vm.swapusage | awk -F'free = ' '{print $2}' | awk '{print int($1)}'); done
  echo "== $name $(date +%H:%M:%S) swap_free_mb=$free_mb" | tee -a "$LOG"
  LT_START="$s" LT_END="$lend" /usr/bin/time -l env PYTHONPATH="$MAIN:.:../shortline_research" "$PY" ../shortline_research/mrsi_s1013.py "$name" "$OUT" >>"$LOG" 2>&1
  echo "phase_exit=$?" | tee -a "$LOG"
}
for p in "R12 2025-10~2026-10:2025-10-01:2026-10-30" "N3 2024-01~2026-10:2024-01-01:2026-10-30" "EXT 2024-01~2025-09:2024-01-01:2025-10-31" "W0 2022-04~2023-03:2022-04-01:2023-04-30" "W00 2023-04~12:2023-04-01:2024-01-31" "W1 2024:2024-01-01:2025-01-31" "W2 2025-01~09:2025-01-01:2025-10-31" "P19 2019:2019-01-01:2020-01-31" "P20 2020:2020-01-01:2021-01-31" "P21 2021:2021-01-01:2022-01-31"; do
  IFS=: read -r n a b <<< "$p"; run_ph "$n" "$a" "$b"
done
echo "DONE_S1013 $(date)" | tee -a "$LOG"
"$PY" ../shortline_research/saf_s987_eval.py "$OUT" MRSI2_BROAD_h3 MRSI2_DIP_h3 MRSI2_STRONG_h3 MRSI6_BROAD_h5 MRSI14_BROAD_h5 ALL_BROAD_h3 ALL_DIP_h3 ALL_STRONG_h3 | tee "$OUTD/eval.txt"
