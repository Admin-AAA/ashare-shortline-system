# 矩阵 0 推荐：前端正确展示 + 观察池接入 + clamp 对齐复现

> 日期：2026-07-06 · 角色：UI Designer（像素君）

## 问题
用户跑矩阵批次（8 个 preset）长期 0 推荐，但前端**完全没展示结果**——`MatrixPage` 只有触发按钮，`RunDetailPage` 在 0 推荐时只写"产出候选：0 只"。用户分不清这是 bug 还是正常行为。

## 结论先行
0 推荐**不是 bug**，是「强牛·高潮」阶段系统刻意拒出手（分数线 +19、禁用降线）。本次把这套逻辑**清晰地展示给用户**，并把观察池接进前端，同时把 repo 与生产不一致的分数线 clamp 对齐、量化复现。

---

## A. 前端展示（UI Designer 本职）

### 后端数据补全（加性、向后兼容）
- `workbench_service.py::run_workbench`：run summary 新增 `score_line_meta`（market_base / market_sentiment / effective_score_line）+ `top_candidate_score`（全量候选最高分）。
- `scheduler_service.py::_run_preset_matrix`：每个 preset row 补 `universe_count / scored_count / effective_score_line / market_base / market_sentiment / top_candidate_score / climax_watchlist`；顶层聚合 `market_regime / aggregated_watchlist（合并去重）/ all_zero`。`finish_task_run` 已把整个返回值写入 `task_run.result`，前端一次 `matrixRunStatus` 即可取完整报告。

### `MatrixPage.tsx` 重写
- 加载即拉最近一次矩阵结果；触发后每 2s 轮询进度至完成。
- **行情过热·系统主动防御状态卡**（核心）：当 `all_zero` 且行情为强牛/高潮时，琥珀色调醒目卡片，明确标注「非 Bug · 设计预期」，列出：行情状态（强牛·高潮）、分数线抬高 +19、候选最高分 vs 有效分数线、回测依据（高潮胜率 38~58%、已禁用降线）。
- 8 preset 明细表：扫描池 / 进入评分 / 推荐 / 有效分数线；推荐=0 标「未过线」。
- **高潮观察池卡片区**：渲染 `aggregated_watchlist`（距线缺口 + 命中策略 tags），标注「轻仓观察 · 非正式推荐」；空时提示「与行情过热、全面防御一致」。

验证：`tsc --noEmit` 通过（exit 0）。

## B. clamp 对齐 + 回测复现

### 改动
- `aggregator.py:248`：`effective_score_line` 上限 `min(95.0)` → `min(120.0)`，复现生产批次的 119 线（原 95 会压低高潮阶段线、与回测/生产不一致）；下限 35 不变。无测试死锁该值。

### 复现（`scripts/repro_clamp_alignment.py`）
代表性强牛·高潮 snapshot，对照 8 preset 的 base 分数线，输出旧 clamp95 vs 对齐120 的 effective：

| preset | base | eff(旧95) | eff(对齐120) | 门槛状态 |
|---|---|---|---|---|
| 趋势龙头组合 | 93.5 | 95.0 | **112.5** | 线>最高分·必0推荐 |
| 陈小群 | 96.0 | 95.0 | **115.0** | 线>最高分·必0推荐 |
| 主线相对强度 | 66.0 | 85.0 | 85.0 | 线≤最高分·需内部分数 |
| DSA | 72.0 | 91.0 | 91.0 | 线>最高分·必0推荐 |
| 涨停回封接力 | 63.0 | 82.0 | 82.0 | 线≤最高分·需内部分数 |
| 隔夜持股 | 72.0 | 91.0 | 91.0 | 线>最高分·必0推荐 |
| 阶段新高突破 | 66.0 | 85.0 | 85.0 | 线≤最高分·需内部分数 |
| 强趋势缩量回踩 | 63.0 | 82.0 | 82.0 | 线≤最高分·需内部分数 |

**结论**：趋势龙头/陈小群/DSA/隔夜持股 的有效分数线（112.5 / 115 / 91 / 91）远高于本次批次全局候选最高分 **86.35**，必然 0 推荐，与 clamp 取值无关；clamp 对齐到 120 后，生产线 119 量级可被回测复现，回测与生产打分一致。

验证：`test_aggregator.py` 47 passed；`test_scheduler_service.py` 13 passed。

---

## C. 验证缺口（诚实标注）
- 本环境无 browser-use / Playwright 二进制，MatrixPage 新 UI **未真机截图**；`tsc` 通过 + 逻辑闭环，置信度高。
- 真实「0 推荐」的最终复现需 `sync_scope=all` 同步 7-06 后跑一次真实矩阵批次（前端已能展示）；复现脚本量化的是分数线公式本身，非真实批次数据。

## 改动文件
- `frontend-v2/src/pages/MatrixPage.tsx`（重写）
- `backend/app/services/workbench_service.py`（`score_line_meta` + `top_candidate_score`）
- `backend/app/services/scheduler_service.py`（rows 补全 + 顶层聚合）
- `backend/app/engine/aggregator.py`（clamp 95→120）
- `backend/scripts/repro_clamp_alignment.py`（新增复现脚本）

⚠️ 以上内容由 AI 基于公开信息整理生成，仅供参考，不构成任何投资建议或个股推荐。投资有风险，决策需谨慎。
