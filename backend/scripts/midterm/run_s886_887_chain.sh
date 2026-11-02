#!/bin/bash
set -uo pipefail
ROOT="/Users/wangao/Desktop/ashare-shortline-system/backend/scripts/midterm"
LOG=/tmp/run_s886_887_chain.log
echo "CHAIN_START $(date)" | tee "$LOG"
free_mb=$(sysctl -n vm.swapusage | awk -F'free = ' '{print $2}' | awk '{print int($1)}')
echo "swap_free_mb=$free_mb" | tee -a "$LOG"
if [ "$free_mb" -lt 800 ]; then echo "ABORT_LOW_SWAP free_mb=$free_mb" | tee -a "$LOG"; exit 2; fi
bash "$ROOT/run_s886.sh" 2>&1 | tee -a "$LOG"
echo "S886_EXIT=${PIPESTATUS[0]} $(date)" | tee -a "$LOG"
free_mb=$(sysctl -n vm.swapusage | awk -F'free = ' '{print $2}' | awk '{print int($1)}')
while [ "$free_mb" -lt 800 ]; do
  hm=$(date '+%H%M')
  if [ "$hm" -ge 0825 ] && [ "$hm" -lt 1000 ]; then echo "MORNING_YIELD_BEFORE_S887 $(date)" | tee -a "$LOG"; exit 0; fi
  echo "wait swap before s887 free_mb=$free_mb $(date +%H:%M:%S)" | tee -a "$LOG"; sleep 30
  free_mb=$(sysctl -n vm.swapusage | awk -F'free = ' '{print $2}' | awk '{print int($1)}')
done
bash "$ROOT/run_s887.sh" 2>&1 | tee -a "$LOG"
echo "S887_EXIT=${PIPESTATUS[0]} $(date)" | tee -a "$LOG"
echo "CHAIN_DONE $(date)" | tee -a "$LOG"
