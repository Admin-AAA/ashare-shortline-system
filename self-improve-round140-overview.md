# AShare 自我优化 第140轮 — T12 formal run 稳定性

**日期**: 2026-07-08 22:36 | **实例**: si521736 | **合并**: `f7749d4` (main)

## 北极星对齐
A 项（每日可见 formal 推荐）的最大断档根因：**scheduled formal run 在后端进程内同步执行，backend 重启/热重载直接杀死正在跑的 run**（error=`服务重启或进程退出`）。本轮闭合该根因。

## 根因
- `scheduler_service._run_recommendation` 原用 `asyncio.run(run_workbench(...))` 在后端进程内**同步**执行推荐流水线。
- 后端一重启（self-improve 合并后重启、热重载、崩溃），在跑的 run 立即死亡 → 标记 `interrupted`，当天该次推荐丢失。
- 07-06 / 07-07 / 07-08 的多起 `interrupted` run（`progress=拉取并校验市场股票池` / `Skill 深审 N/M`）正是这条 in-process 路径。
- API 手动触发路径**早已**用 `enqueue_workbench_run` → 独立子进程（`start_new_session=True`），但 **scheduler 定时路径漏接**——两套路径行为不一致。

## 改动
`backend/app/services/scheduler_service.py` :: `_run_recommendation`
- 改为调用 `enqueue_workbench_run`（与 API 手动触发完全一致），由 `_spawn_run_subprocess(start_new_session=True)` 派发到**独立子进程**执行。
- 子进程与 uvicorn 进程解耦：backend 重启后子进程继续更新心跳，`recover_interrupted_runs` 按心跳超时判定，**不会误杀存活任务**。
- 无 `preset_id` 时退化为原 in-process 执行（enqueue 前置条件），保留历史行为。
- 新增 `backend/tests/test_scheduler_formal_run_detached.py`（3 用例：enqueue 路由 / 无 preset 退化 / 参数透传）。

## 端到端验证（真实重启，非仅单测）
1. 触发 formal run `run_20260708_145009_1e3f8e` → 派发独立子进程 **PID 13350**（与 backend **13179** 不同进程）。
2. 重启 backend（13179 → 13453）→ 子进程**存活**，PPID 变为 **1**（launchd，确认已脱离 backend 会话）。
3. 子进程继续推进至 `Skill 深审 7/20`（旧路径正是在此 stage 被杀死）→ `status=completed`，`recommended_count=1`，无 error。
4. 前端数据源 API `/api/runs/{id}/recommendations` 可见推荐：**000977 浪潮信息，score 83.97**。

## 影响
- 三个 scheduled formal run（`post_sync_formal_recommendation` 17:30、`preclose_overnight_holding_recommendation` 14:30、`formal_run_safety_net` 08:30）今后**不再因 backend 重启而断档**。
- 直接降低 A 项断档率；残余 `interrupted` 均为修复前的历史 run。

## 备注
- `test_run_preset_matrix_defaults_to_auto` 在主树因**未提交本地改动**（`DEFAULT_RECOMMENDATION_DATA_SOURCE=auto`）才通过；提交版默认值为 `local_pool_preferred`，属**既有 test/code drift**，与本改动无关，未改动。
