#!/bin/bash
# §278 追加：等 §279 队列结束后跑 lt_research18（RP_r05 与基线，新口径）
LOG279="/Users/wangao/Desktop/ashare-shortline-system/backend/output/research/s279/all.log"
while ! grep -q ALL_DONE "$LOG279" 2>/dev/null; do sleep 20; done
/Users/wangao/Desktop/ashare-shortline-system/backend/scripts/midterm/run_s278.sh new lt_research18.py r18
echo "ALL_DONE $(date)"
