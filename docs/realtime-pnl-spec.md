> 2026-10-18：旧网关A/B/C（三个无凭据的第三方 Tushare 网关）及 tushare_official 已从代码中删除，现役 Tushare 兼容源为 promax、quantdata；路由见 backend/app/services/datasource/routing_table.json。下文为历史设计记录。

# 实时浮盈展示（未结算推荐）

日期：2026-07-27 · 状态：**已落地** · 端口：frontend-v2 (5174)

> **2026-07-27 落地实现**：
> - **统一路由**：`TushareProviderRouter` 改为 per-API 链序配置（`DEFAULT_API_ROUTES`），
>   `rt_k` 首选 旧网关B（旧网关A 无权限、旧网关C 504）。新数据源/调链序只改 Router 一处。
> - **后端** `GET /api/recommendations/realtime-pnl`（`realtime_pnl_service.py`）：
>   用 rt_k 当日实时 OHLC 合成当日临时日K，复用 outcome 结算同款 `_resolve_entry` 判入场价，
>   以 rt_k 现价（close）算浮盈。20s TTL 缓存 + `?refresh=true` 强刷。纯展示，不落库。
> - **前端** HistoryPage：筛选栏「实时盈亏」按钮 + 表格「实时盈亏」列
>   （holding 显示浮盈%、watching 显示距买点、已结算显示 —）。
> - 实测：24 号 11 笔 pending 全部 holding，浮盈正常（002498 +10.94% / 605028 +9.57% 领涨）。
>
> **子 agent 评审修正的事实**：rt_k 原本因 `旧网关C_primary_apis` 被路由到 504 的 旧网关C
> （链序与可用性逆序），已通过 per-API 链序配置修正；rt_k 实测支持逗号分隔批量（1.7s 返回）。
> 「实时盈亏」是从零新建（realtime_quote_client 生产零调用方），非迁移。

## 背景与目标

`/history` 推荐历史页里，**未结算（pending）的推荐看不到任何盈亏**——只能看到推荐价，盈亏要等盘后 outcome 结算（依赖 T+N 日K同步 + `label_pending_outcomes`）才出现。这导致盘后到结算前有一段「盲区」，用户无法知道手里 pending 推荐现在浮盈浮亏。

**目标**：对未结算推荐，基于 旧网关C 实时行情（`rt_k` 快照 / `rt_min` 分钟）**实时计算并展示浮盈浮亏**。

**边界（关键约束）：**

- 这是**纯展示**，不是结算。绝不动 `recommendation_outcomes` 表、不改 outcome_label、不参与胜率/盈亏比统计。
- 真实结算仍由现有盘后 `label_pending_outcomes` 负责，口径不变。
- **仅未结算推荐**参与实时计算；已结算（success/failed/missed）的不算。

## 已确认的交互决策

| 决策点 | 结论 |
|---|---|
| 触发方式 | **前端查询时实时计算**，不做后台定时快照 |
| 手动刷新 | **表格上方放一个「刷新实时盈亏」按钮**（非股票维度逐行按钮） |
| 覆盖范围 | **仅未结算（pending）推荐** |
| 历史兼容 | 不考虑，按最优架构；`minute_kbars` 东财残留另行清理 |

## 方案

### 1. 后端：新增实时浮盈计算接口

新增 `GET /api/recommendations/realtime-pnl`，输入当前筛选条件下的 pending 推荐，逐只：

1. **复用现有 entry 解析**（`outcome_simulation._resolve_entry`）判断买点是否已触发、得出 `entry_price`。
   - 未触发买点 → 状态 `watching`，显示「距买点 x%」而非浮盈。
   - 已触发 → 状态 `holding`，用实时价算浮盈。
2. **拉实时价**：`fetch_realtime_quotes(codes)`（tushare `realtime_quote`，走
   `（已删除的 token 报价网关）`，多代码批量一次调用，含现价/最高/最低/昨收）。
   - 批量：pending 列表通常 ≤ 几十只，一次批量调用 + 服务端 TTL 缓存（默认 20s）即可，不扫全市场。
   - 上游（realtime 端点）失败/超时 → 返回 `{}`，该票降级 `unavailable`，不报错。
3. **算浮盈**：`pnl_pct = (realtime_price - entry_price) / entry_price * 100`，并给出日内高/低、是否触及止损止盈的提示。

返回结构（每只）：

```json
{
  "recommendation_id": "...",
  "code": "603893",
  "status": "holding | watching | unavailable",
  "entry_price": 12.34,
  "realtime_price": 12.80,
  "pnl_pct": 3.73,
  "day_high": 12.95,
  "day_low": 12.20,
  "quote_time": "2026-07-27 10:31:00",
  "reason": "…（watching/unavailable 时的说明）"
}
```

- 数据源不可达 / 该票无实时价 → `status=unavailable`，前端降级显示「—」，不报错。
- 入口加 30s 左右短缓存，避免前端频繁刷新打爆 旧网关C 限速。

### 2. 前端：HistoryPage 增加「实时盈亏」列 + 顶部刷新按钮

- 在推荐历史表格上方放「刷新实时盈亏」按钮（loading 态），点击调 `/api/recommendations/realtime-pnl`。
- 表格新增一列「实时盈亏」：
  - `holding` → 显示浮盈%（红涨绿跌，配色与现有盈亏一致）。
  - `watching` → 显示「距买点 x%」。
  - 已结算 → 仍显示真实 `executed_return`（不变）。
  - `unavailable` → 「—」。
- 首次进入页面自动拉一次（对当前筛选结果中的 pending），之后靠手动按钮刷新。

### 3. 不做的事

- 不写库、不加新表、不加定时任务、不接调度器。
- 不扫全市场实时行情。
- 不改 `_simulate_outcome` / `label_pending_outcomes` 真实结算路径。

## 实施步骤（verify）

1. 后端 `realtime_pnl` 服务函数：复用 `_resolve_entry` + `fetch_realtime_k_snapshot` → verify：单测给定 feature_snapshot + 实时价能算出正确 pnl。
2. API 路由 + 短缓存 → verify：`curl` 对含 pending 的筛选返回结构化结果，unavailable 降级正常。
3. HistoryPage 列 + 顶部刷新按钮 → verify：页面出现「实时盈亏」列，holding/watching/unavailable 三种态正确渲染。
4. 端到端：对 24 号那批 pending 推荐点刷新，能看到浮盈或「距买点」。

## 风险

- **旧网关C 限速**：pending 多时逐股 `rt_k` 可能触发限速 → 用短缓存 + 限制单次最多 N 只（如 50）缓解。
- **entry 解析依赖日K**：`_resolve_entry` 的候选窗口吃 `future_bars`（日K）。若 T+1 日K未同步，买点触发判断可能滞后 → 实时浮盈对「今日刚推荐、明日才入场」的票会显示 `watching`，属预期。
