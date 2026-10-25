#!/bin/bash
# §279 总控：等 §278 队列结束后，严格串行跑 5 个近达标被否中线研究的新口径重跑
R="/Users/wangao/Desktop/ashare-shortline-system/backend/scripts/midterm/run_s279.sh"
LOG278="/Users/wangao/Desktop/ashare-shortline-system/backend/output/research/s278/all.log"
while ! grep -q ALL_DONE "$LOG278" 2>/dev/null; do sleep 20; done
for j in "lt_research100.py:r100" "lt_research68.py:r68" "lt_research93.py:r93" "lt_research70.py:r70" "lt_research97.py:r97"; do
  "$R" new "${j%%:*}" "${j##*:}"
done
echo "ALL_DONE $(date)"
