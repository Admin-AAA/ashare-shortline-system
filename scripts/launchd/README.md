# Prefect 保活 launchd 配置

盘后同步依赖 Prefect worker/server 在线。此前它们是手动 `nohup` 起的临时进程，
机器重启/显示器休眠/进程崩溃后就掉，导致盘后 17:35 的 `post_sync_formal_recommendation`
漏跑（2026-08-04 即此因）。这两份 plist 让 launchd 接管保活（KeepAlive，掉了自动拉起）。

- `com.wangao.ashare-prefect-server.plist`：Prefect server（4200）
- `com.wangao.ashare-prefect-worker.plist`：Prefect worker（启动时先 `schedule_all_enabled_tasks()`
  重新注册全部启用任务的 cron，再连 server）——重启即自愈调度漂移。

## 安装 / 重装

```bash
cp scripts/launchd/com.wangao.ashare-prefect-*.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/com.wangao.ashare-prefect-server.plist
# 等 server 就绪后再起 worker：
curl -sf http://127.0.0.1:4200/api/health && \
launchctl load -w ~/Library/LaunchAgents/com.wangao.ashare-prefect-worker.plist
```

注意：plist 内的 `.venv` 路径、`PREFECT_API_URL`（4200）与当前后端端口（8765）写死，
换机器/换路径需同步修改。

## 日志轮转（2026-10-25）

launchd 直接写文件的日志（`~/Library/Logs/ashare-*.log` 等）无法 rename 轮转，改用 copy-truncate：
`rotate_ashare_logs.sh`（>=20MB 压缩存档后清空，留 3 份）+ `com.wangao.ashare-log-rotate.plist`（每周日 03:30）。
安装：`cp scripts/launchd/com.wangao.ashare-log-rotate.plist ~/Library/LaunchAgents/ && launchctl load -w ~/Library/LaunchAgents/com.wangao.ashare-log-rotate.plist`

## 小石全市场分钟线每日增量（2026-10-26）

`com.wangao.ashare-xiaoshi-min-sync.plist`：每天 20:40 跑 `backend/scripts/sync_xiaoshi_min_all.py`（nice 低优先级，对小石 ≤6 次/秒，
任何异常 exit 0，不碰 Prefect/17:35 日更/选股）。小石 cn-minute 只保留滚动 3 个月，不每天存就丢。数据落 `backend/data/xiaoshi_min_all/`
（与旧 230 只 `xiaoshi_min1` 互不影响），读取用 `sync_xiaoshi_min_all.load_minutes(codes, start, end)`。日志 `~/Library/Logs/ashare-xiaoshi-min-sync.out.log`、
`backend/data/xiaoshi_min_all/sync.log`。
安装：`cp scripts/launchd/com.wangao.ashare-xiaoshi-min-sync.plist ~/Library/LaunchAgents/ && launchctl load -w ~/Library/LaunchAgents/com.wangao.ashare-xiaoshi-min-sync.plist`

## 一次性：筹码空洞 4 日回补（2026-10-27）
`com.wangao.ashare-chip-oneshot-20261027.plist`：2026-10-27 15:10 触发 `oneshot_chip_backfill_20261027.sh`，补 2026-07-30 / 08-25 / 08-31 / 09-09 的 cyq_perf 筹码
（命令：`cd backend && PYTHONPATH=. .venv/bin/python scripts/backfill_chip_dates.py 2026-07-30 2026-08-25 2026-08-31 2026-09-09`）。
只在 10-27 的 09:00–16:25 启动；额度没恢复就 20 分钟后重试，最多 3 次；跑完自我卸载（plist 改名 .done）。日志 `~/Library/Logs/ashare-chip-oneshot-20261027.log`。
> 2026-10-26 更新：4 个筹码空洞日已于 15:4x 通过 teajoin（cyq_perf 兜底）提前补完，上面的一次性任务已取消（plist 改名 .cancelled）。
