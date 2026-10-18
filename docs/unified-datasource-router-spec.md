> 2026-10-18：旧网关A/B/C（三个无凭据的第三方 Tushare 网关）及 tushare_official 已从代码中删除，现役 Tushare 兼容源为 promax、quantdata；路由见 backend/app/services/datasource/routing_table.json。下文为历史设计记录。

# 统一数据源路由器（Unified Data Source Gateway）

日期：2026-07-27 · 状态：待子 agent 评审 · 目标：所有数据源访问收敛到唯一 Router

## 背景与动机

当前数据源访问是「多点直连」的乱象，每加一个数据源都要在多处接线：

- **推荐流程 / 数据落库**：走 `get_pro()` → `TushareProviderRouter`（pro_http：旧网关A/旧网关B/旧网关C）。
- **盘中实时模式**（`market_pool/core.py:get_market_universe_from_pool_with_realtime`、
  `market_data.py:_fetch_tushare_realtime_frame`）：直连 `ts.realtime_quote` 爬虫，**绕过 Router**。
- **实时盈亏**（`realtime_quote_client.py`）：又一条独立的 realtime_quote 爬虫线，与 market_data.py 重复。
- **新闻**（`market_auxiliary.py`）：直连 eastmoney httpx。

后果：realtime_quote 爬虫被复制了 2 份；新源接入要改 N 处；熔断/fallback/启停只在 pro_http 侧有，爬虫侧没有。

**目标架构**：Router 是系统唯一的数据源网关。所有实时数据源访问（含落库 MySQL 的同步）都经它。
**新增数据源只与 Router 对接，与业务代码完全解耦。**

## 真实数据流（必须先认清，避免方案写歪）

### 全市场扫描走 MySQL 本地池（推荐主路径）

- 推荐流程默认 `data_source = local_pool`（`DEFAULT_RECOMMENDATION_DATA_SOURCE`，scheduler 同）。
- `get_market_universe_from_pool(db)` → `_load_quotes_from_pool(db)` 从本地
  `stock_quotes` / `daily_kbars` 读，**就是走 MySQL**。MySQL 的数据由盘后同步任务从 pro 协议灌入。
- **realtime_quote 爬虫不是全市场**：只在 `data_source = local_pool_realtime`（盘中实时模式）时，
  对**召回后的候选股**（数百只，非 5500）分批（50/次）拉实时快照，拼到当日K线。

### 实时价的两类消费方

| 场景 | 标的规模 | 最合适数据源 |
|---|---|---|
| 盘中实时模式候选股补当日快照 | 数百只 | realtime_quote 爬虫（批量 50/次） |
| **/history pending 推荐实时盈亏** | **几十只特定股票** | **`rt_k`（pro 协议，走 Router，旧网关B 支持）** |

**关键结论**：实时盈亏是「几十只特定股票」，用 `rt_k`（pro 协议）走现有 Router 即可，
**不需要 realtime_quote 爬虫**。爬虫只服务于盘中实时模式那一路。

### 各能力实测可用性（2026-07-27）

| 能力 | 可用源 | 说明 |
|---|---|---|
| `rt_k` 单股/小批量实时价 | 旧网关B（pro）✅ / 旧网关A 无权限 / 旧网关C 504 | 需指定 ts_code，不支持全市场 |
| `rt_min` 实时分钟 | 旧网关B ✅ / 旧网关C 504 | 需 freq 参数 |
| `stk_mins` 历史分钟K | 旧网关C（504 中）/ 旧网关B ✅ | 已配 旧网关C 优先 |
| realtime_quote 爬虫（批量实时快照） | （已删除的 token 报价网关） ✅ | 独立爬虫协议，绕开 Router |
| `daily`/`stk_factor`/`cyq_perf` 等 | 旧网关A 主 / 旧网关B / 旧网关C 备 | 现状 |

## 架构：能力路由层（Capability Gateway）

把现有 `TushareProviderRouter`（pro API 名 → provider 选路）升级/包装为
「**数据能力 → 数据源选路**」的统一网关。业务只声明「我要什么能力 + 参数」，网关决定「走哪个源、怎么兜底」。

### 能力（capability）与数据源链

```
capability            数据源链（按优先级，故障自动降级）          协议
──────────────────────────────────────────────────────────────────────
daily_bar / factor / chip / ...   旧网关A → 旧网关B → 旧网关C   pro_http
realtime_price（单股/小批量实时价）  旧网关B(rt_k) → realtime_quote爬虫  pro + 爬虫兜底
batch_realtime（候选股批量实时快照） realtime_quote爬虫               爬虫（唯一可行）
minute_bar（历史分钟K）             旧网关C(stk_mins) → 旧网关B     pro_http
news（公告）                       tushare(anns_d) → eastmoney爬虫   pro + 爬虫
```

### 核心原则

1. **统一的是「入口 + 治理」，不是「协议」**。pro_http 和爬虫是两类协议，各自封装为
   Source adapter，对网关暴露统一 `fetch(capability, **params)` 接口。
2. **每个 (source, capability) 独立熔断 + fallback + 运行时启停 + 遥测**（复用现有
   `data_source_config` 启停覆盖与熔断器）。
3. **业务代码只调 `gateway.fetch(capability, ...)`**，不直接 import tushare / 直连数据源。

## 迁移路径（分阶段，不一次推翻）

### Phase 1（本次）：统一实时行情入口 + /history 实时盈亏

1. 建 capability + Source adapter 架子（`app/services/datasrc/`）。
2. `realtime_price` 能力：首选 `rt_k`（经 pro Router，旧网关B），爬虫兜底。
3. `batch_realtime` 能力：收敛 `market_data.py` + `realtime_quote_client.py` 两条重复爬虫线为
   一个 `realtime_quote_source` adapter，market_pool 盘中实时模式改调它。
4. **/history 实时盈亏**：复用 `_resolve_entry` + `gateway.fetch("realtime_price", codes)` +
   前端「刷新实时盈亏」按钮 + 实时盈亏列。**本任务主体，走 rt_k，不碰爬虫。**
5. 删除重复爬虫代码（`realtime_quote_client.py` 并入 adapter，market_data.py 改为调 adapter）。

### Phase 2（后续）：数据落库同步统一
- 盘后同步 / `data_gap_remediation` 等写 MySQL 的链路从「直连 get_pro()」改为经 gateway，
  享受统一熔断/启停/遥测。

### Phase 3（后续）：新闻/公告收敛
- `market_auxiliary.eastmoney` 包成 Source adapter，纳入 gateway。

## 本次实施范围（Phase 1）

- [ ] capability + Source adapter 接口（`app/services/datasrc/`）
- [ ] `pro_source` adapter（包装现有 TushareProviderRouter，暴露 rt_k/daily 等）
- [ ] `realtime_quote_source` adapter（合并 market_data.py + realtime_quote_client.py 爬虫）
- [ ] `gateway.fetch` 统一入口 + capability 路由表 + 熔断/fallback/启停
- [ ] **/history 实时盈亏**：`_resolve_entry` + `gateway.fetch("realtime_price")` + 前端按钮/列
- [ ] market_pool 盘中实时模式改调 `gateway.fetch("batch_realtime")`
- [ ] 删除重复爬虫代码
- [ ] 数据源健康面板：按 (source, capability) 展示

## 风险

- **market_pool 盘中实时模式改 gateway 有风险**（盘中推荐核心链路）→ 先做 adapter 平移
  （不改逻辑），再切调用点，全程用现有测试 + 实测验证。
- **两条 realtime_quote 爬虫线字段口径差异**（market_data 的 `_quote` vs realtime_quote_client
  的归一化）→ 以 market_pool 在用的口径为准，实时盈亏不依赖爬虫（走 rt_k）规避此风险。
- **rt_k 批量上限未知** → 实时盈亏标的通常 ≤50，分批即可；超批量时降级单股。

## 待子 agent 评审的开放问题

1. capability 抽象放在 `tushare_transport` 层扩展，还是新建 `datasrc/` 独立包？
2. `realtime_price` 用 rt_k 为主、爬虫为兜底，这个链序是否合理（rt_k 是 pro 更干净但依赖 旧网关B）？
3. Phase 1 一次性切 market_pool 盘中实时模式，还是先只接 /history、market_pool 留到 Phase 2？
