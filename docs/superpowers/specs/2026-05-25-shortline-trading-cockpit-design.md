# 短线交易驾驶舱 MVP 强重塑设计

日期：2026-05-25

状态：已确认，等待实现计划

## 目标

把当前 demo 工作台一次性重塑为可上线、可真实运行、可解释推荐、可安全验证、可沉浸式决策的短线交易驾驶舱 MVP。系统不保留旧页面结构、旧样式、旧 view model 或旧数据口径的兼容路径；重塑后的产品默认入口就是驾驶舱。

本设计覆盖前端驾驶舱、主题系统、后端推荐运行编排、数据池接口、任务调度、安全验证和部署验收。实现时允许重构模块边界、重排文件结构和调整内部契约，但不能引入模拟推荐结果作为正式运行兜底。

## 范围边界

### 本次必须完成

- 首页由工作台改为短线交易驾驶舱。
- 前端完全重构视觉系统，支持黑色背景和白色背景一键切换，刷新后保持选择。
- 推荐结果以强度、原因、风险、状态、Skill 共识和数据质量表达，不再只是列表。
- 市场、数据池、任务调度、推荐运行统一成驾驶舱状态模型。
- 后端拆分当前过大的推荐服务模块，形成清晰可扩展的运行、候选、推荐、数据池、验证边界。
- 新增 dashboard projection API，为前端提供稳定读模型，前端不直接绑定内部 JSON blob。
- 安全验证脚本支持生产库安全模式、批次号、可清理测试数据和 dry-run。
- 更新部署验收路径，形成本地验证到云端部署的固定流程。

### 本次明确不做

- 不做多用户权限体系。
- 不做真实交易下单。
- 不为了旧页面入口保留兼容 UI。
- 不修改 Maven `pom.xml` 或引入 Java 侧改造。
- 不把数据库密码、API Key、服务器登录密码等敏感凭据写入 Git 跟踪文件。

## 当前问题摘要

### 前端问题

`frontend/src/App.tsx` 承担鉴权、全局数据、运行配置、机会池、统计、任务、详情弹窗等大量职责，已经变成单体工作台。样式集中在 `frontend/src/styles/app.css`，旧的 `.opportunity-*`、`.single-skill-panel` 等 demo 残留和新卡片样式混杂。主题系统不存在，颜色硬编码分散在 CSS 和组件中。

### 后端问题

`backend/app/services/workbench_service.py` 同时负责预设、Skill 同步、运行编排、队列、心跳、数据刷新、过滤、Agent 审查、聚合、落库、序列化、删除和复盘，模块边界不清。推荐解释数据丰富，但大量存储在 JSON blob 中，缺少稳定 projection 层。

### 数据和验证问题

实际报价池表为 `stock_quotes`，日K为 `daily_kbars`，但部分文档仍使用 `UnifiedQuote` 等旧口径。`rebuild_data_pool.py` 具备强清理能力，不适合作为普通生产验证入口。`acceptance_check.py` 会创建预设、运行推荐、修改 Skill 和数据源状态，但缺少批次清理与安全保护。

## 产品设计

### 驾驶舱首页

首页分成 8 个决策区：

1. **今日市场温度**：展示交易日、涨跌家数、涨跌比、涨停/跌停、市场状态和弱市纪律提示。
2. **主线/题材强度**：展示行业和题材的强弱排序、持续性、热度来源、覆盖股票数量。
3. **龙头候选**：展示最终推荐和高潜候选，突出最终分、分数线差值、bucket、setup、Skill 共识和分歧。
4. **风险雷达**：展示高风险标签、过热、数据缺失、Agent 分歧、弱市仓位纪律。
5. **推荐运行状态**：展示当前 run 阶段、进度、心跳、可恢复状态、最近失败原因。
6. **数据池健康度**：展示报价池、日K、辅助数据、数据源状态、刷新时间、可运行性。
7. **调度状态**：展示任务启停、正在执行任务、最近任务结果、失败摘要。
8. **复盘与反馈**：展示待标注推荐、最近 outcome、命中率、失败原因分布。

### 推荐卡表达

推荐卡必须包含：

- 股票代码、名称、行业、题材标签。
- 最终强度：`final_score`、`score_gap`、`bucket`、`setup_type`。
- 核心理由：`rationale`、关键支撑点、入场条件、失效条件。
- 风险：高/中/信息三类风险，风险原因可解释。
- Skill 审查：每个 Skill 的 stance、score、confidence、supports、risks、action。
- 数据质量：报价来源、日K覆盖、辅助数据覆盖、缺失字段。
- 状态：观察中、忽略、已复盘、结果已标注、重复推荐。

### 个股解释抽屉

点击推荐卡打开解释抽屉，不使用旧详情弹窗结构。抽屉包含：

- 分数拆解：Agent、规则、市场、风险、证据维度。
- Skill 分歧：一致、轻微分歧、显著分歧、多空冲突。
- 风险雷达：风险标签和触发字段。
- Pipeline trace：从候选池到推荐池的关键阶段。
- 交易计划：入场风格、仓位区间、止损、止盈、次日观察。
- 复盘反馈：标注 outcome、复盘备注、观察状态。

## 前端架构

### 目录结构

```text
frontend/src/
  app/
    AppShell.tsx
    AuthGate.tsx
    nav.ts
  api/
    client.ts
    dashboard.ts
  features/
    dashboard/
      DashboardPage.tsx
      components/
      hooks/
      viewModel.ts
    run-desk/
    opportunity-pool/
    run-history/
    data-health/
    automation/
    validation/
    review/
  shared/
    ui/
    theme/
    format.ts
    statusTone.ts
  styles/
    tokens.css
    themes.css
    cockpit.css
```

### Shell 设计

`AppShell` 只负责：

- 鉴权后的全局布局。
- 顶部状态栏。
- 左侧或顶部导航。
- 全局 toast。
- section routing。

业务状态不再堆在 `AppShell`。每个 feature 由自己的 hook 和 view model 管理。

### 主题系统

主题通过 `document.documentElement.dataset.theme` 控制，支持 `dark` 和 `light`。`localStorage` key 为 `ashare-theme`。首次加载优先读取 localStorage，没有值则跟随 `prefers-color-scheme`。

CSS token 分三层：

- `tokens.css`：尺寸、字体、圆角、阴影、语义色名称。
- `themes.css`：黑白主题变量值。
- `cockpit.css`：驾驶舱布局和组件样式。

黑色主题是专业交易终端风格：深背景、高对比数字、克制状态色、信息密度高。白色主题是研究分析风格：清爽底色、表格可读、风险和强度色保持语义一致。两套主题共享语义 token，但不做简单反色。

## 后端架构

### 服务拆分

```text
backend/app/services/
  runs/
    orchestrator.py
    queue.py
    progress.py
    recovery.py
  candidates/
    pipeline.py
    prefilters.py
    skill_gate.py
    trace.py
  recommendations/
    persistence.py
    projection.py
    explanation.py
    review_feedback.py
  data_pool/
    quotes.py
    daily_kbars.py
    auxiliary.py
    readiness.py
    health_projection.py
  presets/
    preset_service.py
    skill_registry_service.py
  validation/
    safety.py
    acceptance.py
    deployment.py
```

### 运行编排

`runs/orchestrator.py` 是推荐运行状态机，阶段固定为：

1. `prepare`
2. `market_data`
3. `scope_filter`
4. `auxiliary_data`
5. `feature_build`
6. `rule_scan`
7. `skill_gate`
8. `skill_review`
9. `aggregate`
10. `persist`

每个阶段必须返回结构化结果和可序列化 trace。进度、心跳、错误、checkpoint 由 `runs/progress.py` 管理。异步队列由 `runs/queue.py` 管理。恢复逻辑由 `runs/recovery.py` 管理，不再散落在主编排函数内。

### 候选管线

`candidates/pipeline.py` 负责市场范围、公共 prefilter、Skill prefilter、规则门和漏斗审计。每只股票要有统一 trace：

```json
{
  "code": "000001",
  "stage": "skill_gate",
  "passed": true,
  "reason": "规则分达到 Skill 审查门槛",
  "metrics": {
    "amount_yi": 8.2,
    "turnover_rate": 4.6,
    "prelim_score": 76.4
  }
}
```

### 推荐投影

`recommendations/projection.py` 将现有 `Recommendation`、`RecommendationRun`、`AgentDecisionLog`、`WatchItem`、`ReviewNote`、`RecommendationOutcome` 组合成驾驶舱 DTO。前端不直接解析 `feature_snapshot_json`、`market_context_json`、`skill_breakdown_json` 的内部结构。

MVP 阶段允许继续把原始 JSON 存在数据库中，但必须统一从 projection 层读出稳定字段。后续再决定是否把高频查询字段正规化成表。

### 数据池接口

以当前真实表为准：

- 报价池：`stock_quotes`
- 日K池：`daily_kbars`
- 辅助数据：`limit_up_board`、`concept_sector`、`concept_sector_stock`、`capital_flow`、`dragon_tiger`、`news_announcement`

统一接口：

- `data_pool/readiness.py`：可运行性、缺口、覆盖率、样例。
- `data_pool/health_projection.py`：数据源健康、延迟、错误、任务状态。
- `data_pool/auxiliary.py`：辅助数据覆盖和刷新摘要。

旧 `UnifiedQuote` 文档口径必须更新为 `stock_quotes`。

## API 设计

新增 `backend/app/api/dashboard.py`，注册到 `main.py`。

### 接口列表

- `GET /api/dashboard/overview`
- `GET /api/dashboard/market`
- `GET /api/dashboard/recommendations`
- `GET /api/dashboard/recommendations/{id}/explain`
- `GET /api/dashboard/runs/current`
- `GET /api/dashboard/runs/{run_id}/pipeline`
- `GET /api/dashboard/data-health`
- `GET /api/dashboard/tasks`
- `GET /api/dashboard/validation`

### DTO 结构

核心 DTO：

- `DashboardOverview`
- `MarketTemperature`
- `ThemeStrength`
- `CockpitRecommendation`
- `RecommendationExplanation`
- `RiskRadar`
- `RunProgress`
- `PipelineStageSummary`
- `CandidateTraceRow`
- `DataPoolHealth`
- `TaskHealth`
- `ValidationStatus`

所有请求和响应使用 Pydantic 模型。新的 dashboard API 不接收裸 `dict` payload。

## 安全验证设计

### 安全原则

- 生产库验证允许执行，但必须可识别、可清理、可回滚。
- 任何清库、删表、重建库、批量重置、破坏性迁移必须默认禁止。
- pytest 在 demo 阶段直接使用项目配置的生产 MySQL；测试基础设施不得执行清库、删表、重建库或批量重置。
- 验收脚本默认 `dry-run`，显式 `--execute` 才写入。

### 验收批次

安全验收写入统一批次号：

```text
acceptance-YYYYMMDD-HHMMSS
```

所有测试预设、测试 run、复盘备注、观察项必须包含批次标记。验收脚本结束时输出 created artifacts，并支持 `--cleanup-batch <batch_id>` 清理本批次数据。

### 脚本调整

`backend/scripts/acceptance_check.py` 改造成：

- 支持 `--base-url`，默认 `http://127.0.0.1:8765`。
- 支持 `--dry-run` 和 `--execute`。
- 支持 `--batch-id`。
- 支持 `--cleanup-batch`。
- 默认运行 `experiment`，不污染正式统计。
- 不自动切换生产数据源状态；需要显式参数才允许。

`backend/scripts/rebuild_data_pool.py` 增加危险操作保护：

- 默认禁止清空数据。
- 清空数据必须显式传 `--dangerously-clear-data-pool`。
- 检测生产库时二次确认环境变量 `ASHARE_ALLOW_PRODUCTION_DESTRUCTIVE=1`，否则退出。

## 部署验收流程

本地门禁：

```bash
cd frontend && npm run build
cd backend && ./.venv/bin/python -m pytest -q \
  tests/test_feature_builder.py \
  tests/test_aggregator.py \
  tests/test_hallucination_check.py \
  tests/test_research_context.py \
  tests/test_market_pool.py
```

安全验收：

```bash
cd backend
./.venv/bin/python scripts/acceptance_check.py --base-url http://127.0.0.1:8765 --dry-run
./.venv/bin/python scripts/acceptance_check.py --base-url http://127.0.0.1:8765 --execute --run-mode experiment
```

云端部署：

- 同步当前工作区到 `/opt/ashare-shortline-system`。
- 重启 `ashare-backend`。
- 重载或重启 nginx。
- 验证 `http://139.196.242.53:18000` 可访问。
- 检查 `/api/health` 和驾驶舱首页。

## 实施阶段

### 阶段 1：后端边界和 dashboard 读模型

- 创建新服务目录。
- 从 `workbench_service.py` 抽出 projection、persistence、progress、queue 的低风险部分。
- 新增 Pydantic dashboard DTO。
- 新增 dashboard API。
- 增加 projection 单元测试。

### 阶段 2：数据池、调度和安全验证

- 建立 `data_pool/readiness.py` 和 `health_projection.py`。
- 重构验收脚本安全模式。
- 给危险数据池重建脚本加保护。
- 更新文档中 `UnifiedQuote` 等旧口径。
- 增加安全验证测试。

### 阶段 3：前端驾驶舱重构

- 创建 `AppShell`、主题系统、shared UI。
- 把首页替换为 `DashboardPage`。
- 把旧工作台能力重组为运行、机会池、数据健康、任务、复盘模块。
- 删除旧 demo 样式和未使用 view model。
- 实现黑白主题持久化。

### 阶段 4：验证和部署

- 运行前后端门禁。
- 本地启动并做驾驶舱冒烟。
- 执行安全验收。
- 如需要推送 GitHub，则同步部署云端并验证公网地址。

## 测试策略

后端新增测试：

- Dashboard overview projection。
- Recommendation explanation projection。
- Data pool readiness projection。
- Run progress projection。
- Acceptance script dry-run。
- Destructive data rebuild protection。

前端验证：

- `npm run build`。
- 主题切换刷新保持。
- 驾驶舱首页无白屏。
- 推荐解释抽屉可打开。
- 后端失败时展示错误状态。

当前前端没有测试框架，本次不新增重型测试栈；如需要 E2E，优先使用外部 Playwright 冒烟脚本，不把复杂测试框架引入 MVP。

## 风险与缓解

### 后端大拆风险

风险：拆 `workbench_service.py` 容易引入推荐链路回归。

缓解：先抽 projection、persistence、progress 等低风险边界，再移动编排逻辑。每个阶段保留现有测试通过。

### 生产验证风险

风险：验收脚本和数据池重建脚本误写或误删生产数据。

缓解：默认 dry-run、批次标记、清理命令、危险操作显式 flag、生产破坏性操作环境变量双保险。

### 前端重构风险

风险：一次性替换 `App.tsx` 造成页面能力缺失。

缓解：按 feature 切分迁移，先让 dashboard API 和 `AppShell` 跑通，再迁移运行、机会池、数据健康和统计能力。

### 数据口径漂移

风险：文档、类型、实际表名不一致继续扩大。

缓解：以 `stock_quotes`、`daily_kbars`、辅助表为唯一当前口径，更新 README、architecture、上线验收标准。

## 验收标准

- 访问首页直接看到短线交易驾驶舱，不再是旧工作台。
- 黑色/白色主题可切换，刷新后保持选择。
- 推荐结果展示强度、原因、风险、状态、Skill 共识和数据质量。
- 市场、数据池、调度、推荐运行有统一状态表达。
- 旧 demo 页面结构和旧样式不作为兼容路径保留。
- `workbench_service.py` 的核心职责被拆出到清晰模块，后端继续可运行。
- Dashboard API 使用 Pydantic DTO，不向前端暴露内部 JSON blob 细节。
- 数据池文档和代码口径统一为 `stock_quotes` / `daily_kbars`。
- 验收脚本支持 dry-run、execute、batch、cleanup，危险脚本默认不可破坏生产数据。
- 前端 `npm run build` 通过。
- 后端核心测试通过。
- 如执行云端部署，公网地址和 `/api/health` 验证通过。

## 自检记录

- 无未决空白项。
- 范围覆盖用户确认的“架构强重塑”路线。
- 明确旧工作台、旧样式、旧 view model 不保留兼容路径。
- 明确生产安全验证边界。
- 明确当前真实数据表口径，避免继续使用旧 `UnifiedQuote` 表述。
