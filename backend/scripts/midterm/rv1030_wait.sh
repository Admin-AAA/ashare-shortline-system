#!/bin/bash
# 串行护栏：等其他研究任务和 swap；用法 rv1030_wait.sh <cmd...>
busy() { pgrep -f "run_s3[0-9][0-9]\.sh|lt_research1[0-9][0-9]\.py|research_s3|cap_trades|research_s282|rso_s2|xbx_" >/dev/null; }
while busy; do echo "wait other research job $(date +%H:%M:%S)"; sleep 20; done
memfree() { memory_pressure 2>/dev/null | awk -F': ' '/free percentage/{print int($2)}'; }
while [ "$(memfree)" -lt 25 ]; do echo "wait mem $(memfree)%"; sleep 30; done
echo "RUN $(date) $*"; /usr/bin/time -l "$@"; echo "EXIT=$? $(date)"
