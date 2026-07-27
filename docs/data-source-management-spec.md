# 数据源管理（配置页 Tab）

日期：2026-07-27 · 状态：待实施 · 端口：frontend-v2 (5174)

## 目标

系统已接入多个 tushare 系数据源（fastapic / ai-tool / realtime_quote / xiaodefa），
后续还会加。在配置页新增「数据源管理」Tab，对**已接入**的数据源提供三个操作：

- **启用 / 禁用**（运行时生效，不用改 config 重启）
- **测试**（探针验证该源当前是否可用）

**明确不做**：页面动态新增数据源。接入新数据源仍走开发（注册进代码/config），
因为本来就需要开发才能接——管理面只管「启停 + 测试 + 看状态」。

## 现状与缺口

| 数据源 | 协议 | 现在 enabled 来自 | 运行时启停 | 测试 |
|---|---|---|---|---|
| fastapic | pro_http | config 写死 | ❌ 无 | ❌ 无 |
| ai_tool | pro_http | config 写死 | ❌ 无 | ❌ 无 |
| realtime_quote | 爬虫(realtime.stockai888) | config 写死 | ❌ 无 | ❌ 无 |
| xiaodefa（新） | pro_http | 待接入 | ❌ 无 | ❌ 无 |

- 数据域（quote/kline/news…）已有 `set_data_source_enabled`，管的是「某域用哪些源」，
  **不管 provider 级启停**。
- `TushareProviderRouter` 的 provider 列表与优先级写死（`_build_provider_router` /
  `_ordered_providers`），无运行时启停、无测试探针。

## 方案

### 1. 接入 xiaodefa（pro_http 协议，复用现有 provider 基建）

`_build_provider_router()` 里追加第三个 `TushareHttpProvider`：

- name=`xiaodefa`，base_url=`https://t.xiaodefa.top/`，auth_mode=JSON_TOKEN（pro 标准协议）。
- token 走 config（`tushare_xiaodefa_token`），不进 Git。
- 加入 Router 候选池，参与 pro API 的 fallback（priority 排在 fastapic 之后）。

> 注：Router 的 `_ordered_providers` 当前写死 `("fastapic","ai_tool")` 两源序。
> 接入 xiaodefa 需把优先级从「写死二元」改为「按 provider priority 排序 + enabled 过滤」。

### 2. provider 运行时启停（DB 覆盖 config）

- `data_source_config` 表复用：为每个 tushare provider 落一行
  （source=provider 名，source_type=`tushare_provider`）。
- `sync_data_source_configs` 的 desired 增加 provider 条目，使启停可持久化；
  孤儿清理对 provider 条目豁免（现状已是：provider 走进程内遥测不入可编辑注册表）。
- Router 构建与 `_ordered_providers` 读取 DB 的 enabled 覆盖：
  某 provider enabled=false → 不进候选、不参与 fallback。
  **进程内缓存覆盖 + 失效机制**，启停操作后立即生效（清 provider 缓存，下次请求重建）。

### 3. provider 测试探针

- 新增 `POST /api/data-sources/{provider}/test`：
  - pro_http 源（fastapic/ai_tool/xiaodefa）→ 调一个轻量真实 API（如 `stock_basic limit=1`）
    测连通 + 鉴权，返回 `{ok, latency_ms, error}`。
  - realtime_quote → 调 `fetch_realtime_quotes(["000001"])` 测爬虫端点。
- 测试结果写入 `data_source_health`（success/failure/last_error/latency），面板直接读。

### 4. 配置页「数据源管理」Tab（frontend-v2）

- 列出全部 provider：名称、协议、base_url、状态（healthy/degraded/untested/disabled）、
  最近成功/失败时间、last_error、启用开关、「测试」按钮。
- 操作反馈用按钮 loading + 状态条（遵循项目「普通操作不弹窗」约定）。
- 数据来自现有 `/api/admin/data-health` 的 `data_sources` + provider 条目，
  启停/测试调新增的启停/测试接口后刷新。

## 实施步骤（verify）

1. xiaodefa 接入 Router + config token → verify：直接调 `pro.daily` 经 Router 能走通 xiaodefa。
2. Router 优先级改「priority 排序 + enabled 过滤」→ verify：禁用 fastapic 后请求自动落到下一源。
3. provider 启停 DB 覆盖 + 缓存失效 → verify：API 禁用 xiaodefa，下一次 pro 调用不再走它。
4. 测试探针接口 → verify：`curl POST .../xiaodefa/test` 返回 ok+latency；禁用时返回不可用。
5. 配置页 Tab → verify：页面看到 4 个 provider，可启停、可测试，状态实时刷新。

## 风险

- **Router 优先级改造**影响日常同步链路 → 需保证默认（全启用）时行为与现状一致
  （fastapic 主、ai_tool 备），xiaodefa 仅作新增 fallback，不改变既有调用顺序假设。
- **enabled 覆盖的缓存一致性**：多 worker/多进程下，DB 启停变更要能让各进程及时感知
  （短 TTL 缓存或操作后广播失效）。
