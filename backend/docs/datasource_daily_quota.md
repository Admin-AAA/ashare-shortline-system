# Tushare 上游每日额度反推（2026-10-19）

## 方法
1. 数据：`data/runtime/runtime_metrics.sqlite` 的 `process_metrics`（每个进程累计的
   `provider_requests{provider,outcome}` 计数，含 success / invalid_request / timeout 等所有到达上游的尝试），
   以及 `logs/app.log`、`/tmp/chip_backfill_2026.log` 中的 `42901: token daily limit exceeded`。
   从今天起 `provider_quota` 表 `provider_daily_usage` 会按北京时间自然日直接累计，以后可直接读。
2. 定位当天第一次 42901：2026-10-19 09:32（北京时间）`backfill_chip_dates.py`（pid 85174）调用 cyq_perf。
3. 把该时刻之前、本额度日内所有进程对该 provider 的请求累加。额度日的重置时刻未知，分两种口径：
   - 若按北京时间 00:00 重置：10-19 00:54 起（快照保留的最早时刻）至 09:32，quantdata 累计 ≈ **6,396** 次
     （success 6,048 + invalid_request 347 + timeout 1）。00:00–00:54 无快照，故为下界。
   - 若按 UTC 00:00（北京 08:00）重置：08:14 之后完整落在窗口内的进程合计 **1,156** 次；
     加上跨越 08:00 的两个进程（fetch_limit_list_cache 880、prefect-worker 36）上限 ≈ **2,075** 次。
4. promax：快照期内从未出现日限错误（只有 `upstream_pool_exhausted` 的 http_rate_limit，属于并发池瞬时耗尽，
   不是日额度）；当天已成功 ≥ 3,801 次仍未触顶，只能给出下界，无法反推上限。

## 结果与设置
| provider | 反推额度 | 设置 `ASHARE_TUSHARE_<P>_DAILY_QUOTA` |
|---|---|---|
| quantdata | 1,156（保守口径，UTC 重置）~ 6,396（北京时间重置） | **920**（保守口径 1,156 × 80%） |
| promax | 未触顶，≥ 3,801 | **不设置**（按下界 80% 设置会让补数闸永远处于关闭状态） |

补数闸（`provider_quota.backfill_budget_check`）在用量 ≥ 60% × 额度（quantdata ≈ 552 次）或当天已报 42901 时停止补数。
额度只影响补数闸，不影响正式运行。

## 待校准
- 明天观察 quantdata 何时恢复（08:00 前恢复说明按北京时间 00:00 重置 → 额度可上调到 ≈ 5,100）。
- 以后每天第一次 42901 时，读 `SELECT * FROM provider_daily_usage WHERE day=<当天>` 即为当天额度，按 80% 更新。
