# 数据源 sxdata（tus.sxdata.top）评估报告

探测时间：2026-11-15 17:19–18:23（周日非交易时段，不影响实盘）。原始证据：`docs/sxdata_probe_results.json`。探测脚本：`backend/scripts/probe_sxdata_source.py`（token 只从环境变量读取，未写入任何文件）。对账基准：teajoin（当前主源）。

## 1. 它是什么、怎么调

- **Tushare Pro 协议的第三方代理**：HTTP POST `http://tus.sxdata.top/tu_share/`，JSON `{"api_name","token","params","fields"}`，返回 `data.fields` / `data.items`，与 teajoin / quantdata 同一种方式（本项目 `AuthMode.JSON_TOKEN`）。
- 三种用法实测：**HTTP 可用**；Python SDK（改 `pro._DataApi__http_url`）同 HTTP；**MCP 不可用**——`/tu_share/mcp` 对主 token 和 `backup15000_` 备用 token 都返回 `您的token不存在`（2026-11-15 18:06 实测）。
- 两枚 token：主 token（共享池）+ `backup15000_` 备用 token（专用通道，见 §4.4）。
- 行数上限按接口不同：`report_rc` 5000、`kpl_concept_cons` 3000、`st` 1000；全市场日线 5571 行可一次返回，**没有 5000 行截断**。返回里未观察到 teajoin 那种显式 `has_more` 翻页字段，大表靠单次上限。

## 2. 接口可用性（系统在用的 57 个接口）

首轮 57 个接口：43 个直接成功，其余经重试/换参数后大多可用。结论：

| 类别 | 可用 | 不可用 / 受限 |
|---|---|---|
| 行情与基础 | daily、adj_factor、daily_basic、stk_limit、suspend_d、namechange、trade_cal、stk_auction、stk_mins（1/5/15/30/60min） | **stock_basic 只能按 ts_code 单查**（无参/`list_status`/`exchange` 均 400 invalid_params），无法一次拉全市场股票清单 |
| 资金与两融 | moneyflow、moneyflow_hsgt、hsgt_top10、margin、margin_detail、block_trade | — |
| 涨停 / 龙虎榜 / 情绪 | limit_list_d、top_list、top_inst、kpl_list、limit_list_ths、ths_hot、stk_shock、stk_high_shock、stk_alert、kpl_concept_cons | — |
| 筹码 / 技术 / 指数 | cyq_perf、stk_factor、stk_factor_pro、index_daily | — |
| 财务 / 股东 / 公司 | fina_indicator、share_float、stk_holdernumber、stk_holdertrade、pledge_stat、repurchase、dividend、stk_surv、express | fina_indicator_vip / forecast_vip / express_vip：`unknown_api`（VIP 接口未开放）；fina_audit / cn_schedule：invalid_params |
| 板块 | ths_member、kpl_concept_cons | ths_index：invalid_params（参数形态与官方不同） |
| 文本层（teajoin 扩展） | report_rc、express、eco_cal、stk_managers、idx_anns、st、kpl_list、ths_hot | forecast：持续 503；broker_recommend、anns_d：invalid_params |
| 实时 | rt_k（1.99s）、rt_min（9.3s）可用但慢 | — |

注意事项：
- `stk_alert` 返回的日期是 **2026-11-18～11-26（未来日期）**，文本层个别接口有字段/参数映射异常，不宜直接信任。
- `margin` 必须用 `trade_date` 单日参数（用 start/end 会 invalid_params）；`hk_hold` 同理。
- 首次请求常见的 `503 upstream_pool_exhausted` 是**瞬时容量忙**，8 秒后重试大多成功（stk_factor、pledge_stat、stk_holdertrade、stk_alert、stk_shock、margin、kpl_concept_cons 都是重试后才通）。

## 3. 数据范围（相对现有源的核心价值）

| 数据集 | sxdata 历史起点 | 最新 | 全市场覆盖 | 备注 |
|---|---|---|---|---|
| daily | **1991-04-03**（000001.SZ 首行） | 2026-11-13 | 2005-01-04 起 1345 只；2010 年 1632；2015 年 2347；2024 年 5329；最新 **5571** | 无截断；BaoStock/teajoin 只到 2020（teajoin）或更晚 |
| adj_factor / daily_basic | **2005-01-04 起**（1353 / 1344 行） | 2026-11-13 | ✓ | daily_basic 字段与 teajoin 完全一致（18 列） |
| stk_limit | 已验证 2015-01-05（2591 行） | 最新 5656 行 | ✓ | — |
| limit_list_d | **2020-01-02 起**（2007/2010/2015/2018/2019 均空） | 2026-11-13（91 行，与 teajoin 一致） | ✓ | 历史起点与 teajoin 相同，无额外收益 |
| moneyflow | **2010-01-04 起**（834 行） | 最新 5579 行 | ✓ | — |
| margin / margin_detail | **2010-03-31 起**（汇总 2 行；明细 41 行） | 2026-11-13（明细 2019 行） | ✓ | 明细按 trade_date 当日即可取 |
| top_list / top_inst | **2010-01-04 起**（15 行） | 2026-11-13（69 / 690 行） | — | — |
| hk_hold | **2017-01-03 起**（1509 行） | 2026-11-13（1025 行） | ✓ | — |
| index_daily | **1990-12-19 起**（000001.SH 1990-92 共 519 行） | 2026-11-13 | — | 与 teajoin 数值逐字段一致 |
| stk_factor | **2006-01-04 起**（1179 行） | 最新 5571 行 | ✓ | 全市场当日 56 秒，慢 |
| cyq_perf | **2018-01-02 起**（3042 行） | 最新 5571 行 | ✓ | teajoin 文档只验证到 2026-07+ |
| stk_mins | 已验证 2018-01-02（5min 48 根/日） | 近 30 天窗口 1008 行；1min 近 5 天 1200 行 | ✓ | 未验证 2018 以前 |
| fina_indicator | **1990-03-21 起**（000001.SZ 158 条） | 2026Q3（end_date 20260930） | — | 字段齐全，数值与公告日正常 |
| stk_holdernumber / dividend / share_float | 1996 / 1991 / 2007 | — | — | — |
| suspend_d | 已验证 2015-01-05 窗口（1278 行） | — | — | — |

**结论：历史深度是最有价值的差异点**——日线/复权/每日指标 2005+、资金流/两融/龙虎榜 2010+、技术因子 2006+、筹码 2018+、指数 1990+，可以支撑 2010–2020 区间的回测补数（项目本地 daily_kbars 2021+、stk_limits 2022+）。

## 4. 性能（周日 17:30–18:20 实测，共享池处于偏拥塞状态）

### 4.1 延迟

| 场景 | 结果 |
|---|---|
| 冷启动首次调用 | 6.4s（第二次 2.1s） |
| 串行轻查询（daily 单股 21 行，n=20） | p50 **1.86s**，p95 3.72s，max 5.35s |
| 串行全市场日线（5571 行 / 480KB，n=6） | p50 **19.7s**，p95 35.7s；2018-01-02 单日 93.4s；daily_basic 最新 98.5s；moneyflow 最新 56s |

### 4.2 并发与吞吐

| 测试 | 结果 |
|---|---|
| 4 并发 × 40 | 16.5s，40/40 成功，**2.43 QPS**，p50 1.51s |
| 8 并发 × 48 | 17.7s，48/48 成功，**2.71 QPS**，p50 1.43s |
| 12 并发 × 36 | 62.3s，35/36 成功，**0.56 QPS**（吞吐反降），1 次 504 |
| 全市场日线 2 并发 × 6 | 79.8s，6/6 成功，0.08 QPS，p50 23.2s，max 47.6s |
| 持续 60s（3 路，实际 96.9s） | 77/78 成功，**47.7 请求/分钟**，p50 1.29s，1 次 504 |

**没有任何显式"每分钟 N 次"限流**（主 token 打到 12 路并发也没有 429）；吞吐瓶颈是上游共享池容量和单次延迟。最佳并发 **4–8 路，稳态约 2.5 QPS ≈ 150–160 请求/分钟**，再高只会排队/超时。

### 4.3 失败模式

- `503 upstream_pool_exhausted`（约 10–20% 调用）：**瞬时容量忙**，8s 后重试大多成功；探测中 daily/margin/stk_factor/stk_alert/stk_shock/kpl_concept_cons 都出现过。
- `504 Gateway Time-out`（60s 网关超时）：重查询偶发（daily 2018 单日、daily_basic 最新、kpl_concept_cons），重试后成功。
- 无 429、无 token 日额度耗尽提示（探测量下）。

### 4.4 备用 token（`backup15000_` 专用通道）

- 单次更快：p50 **0.55s**（主 token 1.86s），快约 3.4 倍。
- **但并发上限极低**：2 路并发 12 次请求 9 次 429 `edge_conn_limit`；3 路 11/12 次 429；6 路 5/12 次 429。只能当**单线程快速通道**用，不适合作为自动 provider 并发调用。

## 5. 与现有源对比（同参数）

| 场景 | teajoin（主源） | sxdata | 结论 |
|---|---|---|---|
| daily 单股 21 行 | p50 0.97s | p50 1.86s | teajoin 快约 2× |
| daily 全市场 5571 行 | p50 **1.36s** | p50 **19.7s** | teajoin 快约 14× |
| daily_basic 全市场 | p50 2.22s | 5.5–98.5s（波动大） | teajoin 明显更快 |
| limit_list_d | p50 4.07s | 5.5–32.6s | teajoin 更快 |
| index_daily | p50 3.44s | 1.25s（小样本） | sxdata 略快 |

数值一致性（2026-11-13）：
- `daily` 000001.SZ：11 个字段**逐值完全一致**；`index_daily` 000300.SH：逐值一致；`daily_basic`：18 个字段名完全一致。
- `limit_list_d`：两边都是 91 行（sxdata 另有一次瞬时 0 行，属容量忙）。

## 6. 结论与接入建议

**不建议作为主源或盘中实时链路源。** 理由：全市场批量查询 p50 约 20s、高峰近百秒；小查询 p50 约 1.9s，均明显慢于 teajoin；共享池 503/504 需要重试兜底；`stock_basic` 无法拉全市场清单，VIP 接口与 MCP 不可用。

**建议小步接入、只做两件事：**

1. **末位兜底 provider**（priority 8，低于 teajoin 的 6；`max_concurrency=4`、`rate_limit_per_minute≈120`，不开 Leader、不进盘中延迟敏感链）。这样所有接口多一层 fallback，坏处可控、随时可关。
2. **历史回填专用源**（研究脚本按 dataset 显式 `request_pinned("sxdata", ...)` 或直接 HTTP）：补齐 2005–2020 的 daily / adj_factor / daily_basic / moneyflow / margin_detail / top_list / stk_factor，以及 2018 起的 cyq_perf，解锁更长区间的回测。按 2 路并发、约 20s/全市场日估算，补 10 年日线约一晚。

**不建议**把 `backup15000_` 配成第二个 provider（429 并发限制太严）；需要快查时人工/脚本单线程用它即可。

接入改动面（若确认）：`core/config.py`（`_PROVIDER_PREFIXES` + 4 个 settings）、`tushare_client.py`（provider 构造 + 健康投影）、`datasource/topology.py`（LABELS/PRO_PROVIDERS）、`data_sources.py`（`_ROUTABLE_PROVIDERS`）、`datasource/quota_view.py`、`data_quality_monitor.py`、`backend/.env`（URL/token/RPM/并发）、重算 `routing_table.json`。回退：清空 token 或数据源页停用即可。

## 7. 复现

```bash
cd backend
SXDATA_TOKEN=... .venv/bin/python scripts/probe_sxdata_source.py --phase all
```

证据文件：`docs/sxdata_probe_results.json`（sweep / range / perf / mcp / compare / recheck 分节）。
