#!/bin/bash
# §278 总控：严格串行（一次只跑一个单进程）。顺序：新口径全部 -> 旧口径对照（lt_research25 族）
R="/Users/wangao/Desktop/ashare-shortline-system/backend/scripts/midterm/run_s278.sh"
for j in "lt_research25.py:r25" "lt_research16.py:r16" "lt_research19.py:r19" "lt_research88.py:r88" "lt_research48.py:r48" "lt_research94.py:r94" "lt_research98.py:r98" "lt_research102.py:r102"; do
  "$R" new "${j%%:*}" "${j##*:}"
done
"$R" old lt_research25.py r25
echo "ALL_DONE $(date)"
