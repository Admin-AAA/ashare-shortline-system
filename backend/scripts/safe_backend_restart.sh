#!/bin/bash
# Drain-safe replacement for the forced launchd backend restart.
# Waits up to MAX_WAIT_MIN for active runs/tasks/flows to finish; skips the restart if still busy.
set -uo pipefail
BACKEND=/Users/wangao/Desktop/ashare-shortline-system/backend
MAX_WAIT_MIN=${MAX_WAIT_MIN:-45}
cd "$BACKEND" || exit 1
for ((i = 0; i <= MAX_WAIT_MIN; i++)); do
  out="$(.venv/bin/python -m scripts.active_work_check 2>/dev/null)"; rc=$?
  out="$(printf '%s' "$out" | tail -1)"
  if [ "$rc" = "0" ]; then
    echo "$(date '+%F %T') idle -> restarting backend"
    /bin/launchctl kickstart -k "gui/$(/usr/bin/id -u)/com.wangao.ashare-backend"
    sleep 20
    /usr/bin/curl -sS --max-time 10 http://127.0.0.1:8765/api/health; echo
    exit 0
  fi
  echo "$(date '+%F %T') not restarting yet (rc=$rc): $out"
  sleep 60
done
echo "$(date '+%F %T') still busy after ${MAX_WAIT_MIN}min -> restart skipped"
exit 0
