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

## 后端进程也必须连常驻 server（2026-11-15）

**后端 `com.wangao.ashare-backend.plist` 的 `EnvironmentVariables` 同样必须有
`PREFECT_API_URL=http://127.0.0.1:4200/api`。** 模板已纳入本目录
（`com.wangao.ashare-backend.plist`，重装时与 prefect-*.plist 一起 `cp`），
此前它只存在本机、没有版本化，worker plist 配了而后端漏配：
Prefect 3 客户端没设 API URL 时会为每个 `get_client()` 临时起一个 server（随机端口，
日志里出现过 8245/8861），它与常驻 server 共写同一个 `~/.prefect/prefect.db`，
造成 `sqlite3.OperationalError: database is locked`（累计 16834 次、78MB 错误日志），
并发起临时 server 还会端口冲突使 `run_deployment` 拿 502、
`POST /api/admin/tasks/{id}/run` 直接 500。

- 代码侧自愈：`backend/app/core/prefect_bootstrap.py` 在 `app/main.py` 最顶部、
  任何 `prefect` 导入之前探测 4200 并补 `PREFECT_API_URL`。**Prefect settings 在首次
  import 时缓存，进程运行后再改环境变量无效**，所以必须放启动前。
- 运行期若检测到配置漂移，`require_resident_prefect_client()` 会发
  `prefect_client_misconfigured` 告警并明确拒绝触发，绝不静默退化成临时 server。
- 排查：`GET /api/admin/orchestration/status` 看 `mode` / `ephemeral_risk`；
  应为 `mode=configured`、`ephemeral_risk=false`。

> **launchd 操作坑**：在 Agent/SSH 这类非 GUI 会话里，`launchctl bootout` 能成功，
> 但 `launchctl bootstrap|load gui/<uid> ...` 会报 `Bootstrap failed: 5: Input/output error`，
> 连全新 label 也一样，服务会一直起不来。可行做法是从 **launchd 自己 spawn 的进程**
> 里执行（例如 `launchctl kickstart -k gui/<uid>/com.wangao.ashare-backend-restart`
> 触发一个已加载 job 的脚本），或在 GUI 登录会话里执行；改完 plist 务必先备好恢复路径。

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
