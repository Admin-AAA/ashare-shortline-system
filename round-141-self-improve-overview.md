# 第141轮 Self-Improve 概览 — T7 落地页优先展示 formal 推荐

**实例**: si525969 · **时间**: 2026-07-08 23:51 · **模式**: 独立 worktree → merge main

## 北极星自检（A/B/C/D）
- **A 项**：formal run 持续产出（07-07/07-08 多个 completed 带推荐）；残留 interrupted 均为 T140 修复前的旧 run。✅ 闭合
- **B 项**：推荐详情卡已含 交易参数/买点原因/市场阶段/评分分解/LLM审查/同策略历史胜率/入场失效规则/风险标签。✅ 已闭合
- **C 项**：`StrategiesPage` 已展示每策略 胜率/样本/均收益/盈亏比/最近命中日 + 多周期下钻；后端 `list_strategies` 返回 `profit_loss_ratio`+`last_recommendation_date`。✅ 已闭合
- **D 项**：LIVE 胜率 59.4%、最大亏损 -9.18%。59.4% 低于 62% 线但仅 161 笔样本（95%CI≈±7.5pp），属噪声，**不追涨**（遵循第133轮教训）。✅ 守底线

## 本轮改动
**根因**：`backend/app/api/dashboard.py::_select_dashboard_recommendation_run` 仅按 `finished_at` 排序、不区分 `run_mode`。当某交易日 experiment run 完成更晚时，落地页（候选池/驾驶舱）会展示 experiment 推荐而非官方 formal 推送 —— 直接违反北极星 A「每天打开看到的是官方正式推送」。

**实测触发**：07-07（experiment 02:42:47 > formal 02:35:44）、07-03 均选 experiment；07-08 全 formal 故碰巧选 formal，属潜伏 bug。

**修复**：改为 **formal 优先** —— 同交易日优先选 `run_mode='formal'` 的 completed run；仅当无 formal 完成时才回退到最新 completed run。

## 交付物
- `backend/app/api/dashboard.py`：`_select_dashboard_recommendation_run` formal 优先 + 回退
- `backend/tests/test_dashboard_formal_priority.py`：3 用例（formal 优先 / 无 formal 回退 / 无 completed 返回 None）

## 验证（端到端，真实数据）
- `pytest` 40 passed（3 new + 37 现有 dashboard 测试）
- worktree E2E：钉最新交易日=07-07，旧逻辑选 experiment、修复后选 formal run `run_20260708_023242_04ba96` ✅
- 重启后端 + `curl /api/dashboard/overview`：首条推荐 `run_20260708_150626_902ef0` 经 DB 核验 `mode=formal` ✅

## Git
commit `c642b5b` → merge `1ed8da2` (main)；worktree 已清理，心跳已删。

## 下一步
next_focus = T7/T1（用户可见）。A/B/C 已闭合，D 不追涨。本轮闭合「落地页随机选择 mode」隐患，北极星 A 稳定成立。
