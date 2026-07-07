# 自愈引擎 第139轮 — 失联 run 周期回收看门狗（T12 稳定性收尾）

## 本轮目标
收尾第138轮 T12（LLM 暂不可用→空跑零推荐）的端到端验证，并闭合「孤儿 run 长期滞留 running」这一独立残留根因。

## 关键结论
1. **第138轮 T12 修复已端到端闭环**
   - 验证 run `run_20260707_164125_f8ca14`：`status=completed`、`recommended_count=1`。
   - 推荐 **301321 翰博高新**（final_score 75.34）经 `/api/recommendations/history` 与 `/api/runs/{id}/recommendations` 均可查 → 前端候选池可见。

2. **排除误判的「时区 bug」**
   - 实测 `heartbeat_at` 由子进程以 `datetime.now(timezone.utc).replace(tzinfo=None)`（UTC）写入；`recover_interrupted_runs` 同样以 UTC 比较 → **一致，非 bug**。
   - 卡死 run `run_20260707_163308_abfbd8` 的根因：子进程在 00:39:48 后端重启时被杀，心跳停在 16:39:44 UTC（≈17min 前），处于 1200s 宽限窗内，原回收逻辑**仅启动时或客户端访问 runs 列表时**触发 → 若前端未打开，孤儿 run 长期滞留 `running`，污染北极星 A 自检。

## 交付物（commit 64c8ebc，已在 main）
- `backend/app/services/scheduler_service.py`
  - `start_scheduler` 注册 interval(300s) 周期任务 `periodic_stale_run_recovery`（首跑 start+60s）。
  - `_periodic_recover_stale_runs()` 复用 `recover_interrupted_runs`（UTC 心跳一致性已验证），**即使前端未打开**也会在心跳超时(1200s)后自动标记 `interrupted`。
- `backend/tests/test_scheduler_stale_run_recovery.py`：2 用例（2h 陈旧→interrupted；10s 新鲜→保留）。

## 验证结果
- `pytest` 2 passed。
- 后端重启（新 PID 11334，8765 正常服务）加载新代码；触发 runs 列表走 `_auto_recover_stale_runs`，`abfbd8` 正确变为 `interrupted`。
- **当前 `running` 态 run 数 = 0**，北极星 A 项干净。

## 下一步
`next_focus = T1 / T7`（用户可见）。T12 两处根因（空跑零推荐 + 孤儿 run 滞留）均已闭合。
