#!/bin/bash
# 一次性任务：2026-10-27 额度恢复后补 4 个筹码空洞日（07-30、08-25、08-31、09-09）。
# 由 com.wangao.ashare-chip-oneshot-20261027 在 2026-10-27 15:10（收盘后、16:35–18:35 重启窗口之前）触发。
# 只在 10-27 的 09:00–16:25 之间启动尝试；失败（额度仍未恢复/限流）每 20 分钟重试，最多 3 次；
# 成功（或 3 次都失败）后自我卸载。回补脚本自带额度预算保护（用到日额度 60% 即停）和 promax 窗口令牌桶，不会挤占实时 rt_k。
set -uo pipefail
BACKEND=/Users/wangao/Desktop/ashare-shortline-system/backend
LOG=/Users/wangao/Library/Logs/ashare-chip-oneshot-20261027.log
LABEL=com.wangao.ashare-chip-oneshot-20261027
PLIST=/Users/wangao/Library/LaunchAgents/$LABEL.plist
DATES=(2026-07-30 2026-08-25 2026-08-31 2026-09-09)
cd "$BACKEND" || exit 0
exec >>"$LOG" 2>&1
today=$(date +%F); hm=$(date +%H%M)
echo "== $(date '+%F %T') start (today=$today)"
if [ "$today" != "2026-10-27" ]; then echo "不是 2026-10-27，退出（一次性任务已过期）"; exit 0; fi
if [ "$hm" -lt 0900 ] || [ "$hm" -gt 1625 ]; then echo "不在 09:00–16:25 窗口（现在 $hm），不运行；需手动触发：PYTHONPATH=. .venv/bin/python scripts/backfill_chip_dates.py ${DATES[*]}"; exit 0; fi
ok=0
for attempt in 1 2 3; do
  hm=$(date +%H%M); [ "$hm" -gt 1625 ] && { echo "已过 16:25，不再尝试"; break; }
  echo "-- attempt $attempt $(date '+%T')"
  out=$(PYTHONPATH=. nice -n 10 .venv/bin/python scripts/backfill_chip_dates.py "${DATES[@]}" 2>&1); rc=$?
  echo "$out"
  if [ $rc -eq 0 ] && ! echo "$out" | grep -q "STOP"; then ok=1; break; fi
  [ $attempt -lt 3 ] && sleep 1200
done
if [ $ok -eq 1 ]; then echo "SUCCESS 4 天已补（请跑数据质量巡检确认 chip_history 变绿）"; else echo "FAILED/部分未补：额度仍不足，请手动再跑上面的命令"; fi
# 先改名再卸载（bootout 会结束本进程，必须放最后）
[ -f "$PLIST" ] && mv "$PLIST" "$PLIST.done"
/bin/launchctl bootout "gui/$(/usr/bin/id -u)/$LABEL" 2>/dev/null
exit 0
