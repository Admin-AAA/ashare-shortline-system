#!/bin/bash
# §282 逐笔捕获串行队列：每个脚本只跑 R12、N3（其余阶段沿用 §278 已有 JSON）；LPS_W 全 9 阶段。串行、单进程、swap 空闲<400MB 等待。
ROOT="/Users/wangao/Desktop/ashare-shortline-system/backend"
CD="$ROOT/scripts/midterm"; PY="$ROOT/.venv/bin/python"
OUTD="$ROOT/output/research/s282"; mkdir -p "$OUTD/trades"
export CAP_DIR="$OUTD/trades"
unset ST_HIST_PATH
run_ph() {  # tag script phase start end
  local tag="$1" script="$2" name="$3" s="$4" lend="$5"
  free_mb=$(sysctl -n vm.swapusage | awk -F'free = ' '{print $2}' | awk '{print int($1)}')
  echo "== $tag $name $(date +%H:%M:%S) swap_free_mb=$free_mb" | tee -a "$OUTD/all.log"
  while [ "${free_mb:-0}" -lt 400 ]; do sleep 30; free_mb=$(sysctl -n vm.swapusage | awk -F'free = ' '{print $2}' | awk '{print int($1)}'); done
  (cd "$CD" && CAP_TAG="$tag" LT_START="$s" LT_END="$lend" PYTHONPATH=../..:. "$PY" cap_trades.py "$script" "$name" "$OUTD/${tag}_scratch.json" >>"$OUTD/${tag}.log" 2>&1)
  echo "exit=$? $tag $name" | tee -a "$OUTD/all.log"
}
for j in "lt_research25.py:r25" "lt_research16.py:r16" "lt_research19.py:r19" "lt_research88.py:r88" "lt_research48.py:r48" "lt_research94.py:r94" "lt_research98.py:r98" "lt_research102.py:r102"; do
  run_ph "${j##*:}" "${j%%:*}" "R12 2025-10~2026-10" "2025-10-01" "2026-10-16"
  run_ph "${j##*:}" "${j%%:*}" "N3 2024-01~2026-10" "2024-01-01" "2026-10-16"
done
run_ph r104 lt_research104.py "P19 2019" "2019-01-01" "2020-04-29"
run_ph r104 lt_research104.py "P20 2020" "2020-01-01" "2021-04-30"
run_ph r104 lt_research104.py "P21 2021" "2021-01-01" "2022-04-30"
run_ph r104 lt_research104.py "W0 2022-04~2023-03" "2022-04-01" "2023-07-29"
run_ph r104 lt_research104.py "W00 2023-04~12" "2023-04-01" "2024-04-29"
run_ph r104 lt_research104.py "W1 2024" "2024-01-01" "2025-04-30"
run_ph r104 lt_research104.py "W2 2025-01~09" "2025-01-01" "2026-01-28"
run_ph r104 lt_research104.py "R12 2025-10~2026-10" "2025-10-01" "2026-10-16"
run_ph r104 lt_research104.py "N3 2024-01~2026-10" "2024-01-01" "2026-10-16"
echo "ALL_DONE $(date)" | tee -a "$OUTD/all.log"
