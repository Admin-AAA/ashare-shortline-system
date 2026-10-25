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
