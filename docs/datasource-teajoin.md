# 数据源 teajoin（DaoShare）接入与探测报告

探测时间：2026-10-26 15:10–15:40（收盘后，不影响实盘）。原始结果：`docs/teajoin_probe_results.json`（第一轮 87 项）、`docs/teajoin_probe_deep.json`（第二轮：重试、字段完整度、与本地库对账、稳定性）。探测脚本：`backend/scripts/probe_teajoin.py`、`probe_teajoin_deep.py`（密钥只从 `backend/.env` 读取，不打印不落盘）。

## 1. 它是什么、怎么鉴权
- 一个 **Tushare Pro 协议的第三方代理**（文档页 https://teajoin.com/guide ，文档本身需前端渲染，内容已全部读取）。
- 鉴权：HTTP POST 到根路径 `https://teajoin.com`，JSON `{"api_name","token","params","fields"}`，与 quantdata 同一种方式（本项目 `AuthMode.JSON_TOKEN`）。也支持 tushare SDK（改 `_DataApi__http_url`）和 MCP。
- 返回格式与 Tushare 一致（`data.fields` / `data.items`，大表有 `has_more` + `offset` 翻页，单页上限 3000～10000 随接口）。
- 套餐：**15000 积分通用套餐**，195～205 个接口。**不含**：实时行情（rt_k）、历史分钟（stk_mins，需单独开通，实测返回“当前套餐不包含分钟行情权限”）、新闻公告、研报、港美股。集合竞价类（stk_auction/_o/_c）已包含。
- 限流：官方最高 **450 次/分钟**，建议间隔 ≥0.2 秒；**文档没有写日额度**，我们按 120 次/分钟使用（`ASHARE_TUSHARE_TEAJOIN_RATE_LIMIT_PER_MINUTE`）。Key 的到期日需用兑换码在 https://teajoin.com/redeem 查看（我们手里只有 Key，没有兑换码，**到期时间未知**，建议用户确认）。
- 价格/续费：文档写“闲鱼购买兑换码、到期找卖家续费”，无公开价目。

## 2. 接入方式（小步、可回退）
- 密钥：只放 `backend/.env`（已被 .gitignore 覆盖，权限 600）：`ASHARE_TUSHARE_TEAJOIN_TOKEN`（另有 `_URL`、`_RATE_LIMIT_PER_MINUTE`、`_RESEARCH_INTRADAY_RPM`）。代码、文档、提交里没有密钥。
- 代码：`tushare_client._build_provider_router` 新增 provider `teajoin`，沿用 quantdata 同一套传输层（限流、重试、熔断、额度识别）。
  - **只做兜底**：优先级 6，排在 quantdata(1)、promax(4) 之后；
  - **只服务已验证的接口**（`TEAJOIN_APIS` 白名单，45 个）。`rt_k / rt_min / stk_mins / anns_d / news` 不在白名单，所以不会进入实时行情和分钟线链路；
  - 回退办法：删掉 `.env` 里的 `ASHARE_TUSHARE_TEAJOIN_TOKEN` 并重启，即完全回到接入前；或在数据源管理页停用 teajoin。
- 实测：4 个筹码空洞日（07-30、08-25、08-31、09-09）今天 15:4x 在 promax/quantdata 都额度耗尽时，路由自动回退到 teajoin 补齐（共约 2.2 万行，与本地库对账见下）。

## 3. 数据范围清单（✓=可用，△=有限制，✗=没有）
| 数据 | 结果 | 历史起点 | 最新 | 全市场 | 备注 |
|---|---|---|---|---|---|
| 日线 daily / daily_basic / adj_factor / stk_limit | ✓ | 2020-01-02 起已验证（3797 行）| 2026-10-23 | ✓ 约 5572 只 | 盘后当天数据晚间才出（15:10 查今天为空）|
| **涨停榜 limit_list_d** | ✓ | **2020-01-02 起**（2020/2023/2025/2026 抽查都有）| 2026-10-23 | ✓ 涨停(U)+跌停(D)+炸板(Z) | **含封单额 fd_amount、开板次数 open_times、首封/最后封板时间、连板数 limit_times、up_stat**；open_times 100% 非空；fd_amount 对涨停行完整（炸板/跌停行为空属正常）。每日仅 100 行左右 = 真实涨停数，不是被截断 |
| limit_list_ths / limit_cpt_list / limit_step | ✓ | 近期已验证 | 10-23 | ✓ | 同花顺涨停池含开板数 open_num、封单、涨停原因；limit_step 第一轮超时、重试成功 |
| kpl_list（开盘啦涨停榜）| △ | 2025 年有、2023 年为空、2020 年为空 | 10-23 | ✓ | 历史起点比 limit_list_d 晚 |
| **集合竞价 stk_auction_o（开盘竞价）** | ✓ | **2020-01-02 起**（3751 行；2023、2025 各约 5000+ 行）| 盘后当晚才有（15:10 查 10-23 为空）| ✓ | 字段：open/high/low/close/vol/amount/vwap |
| stk_auction_c（收盘竞价）| ✓ | 2025-01-02 已验证（5633 行）| — | ✓ | 更早历史未测 |
| stk_auction（9:25 竞价快照）| △ | 只有近期（2025、2023、2020 查询均为空）| 10-23（6096 行）| ✓ | 含价格/成交量/量比/换手；需早盘 9:26 后取 |
| **全市场历史分钟K stk_mins** | ✗ | — | — | — | **套餐无权限**，需向卖家单独购买“历史分钟”权限。etf_mins（ETF 分钟）可用，2024 年已验证 |
| rt_k / rt_min / 实时逐笔 / 盘口 / tick | ✗ | — | — | — | rt_k、rt_min 不支持；猜测的 rt_tick/realtime_tick/realtime_quote/stk_ticks 全部“未收录”。**tick/盘口缺口无法由 teajoin 补** |
| 筹码 cyq_perf | ✓ | 已验证 2026-07～10 | 10-23 | ✓ 约 5550 只 | **与本地库 cost_50 逐行对账 5565/5565 完全一致**；cyq_chips（分布）2026-06-25 起停更 |
| 资金流 moneyflow / moneyflow_dc / moneyflow_ths | ✓ | — | 10-23 | ✓ | 慢（18～23 秒）|
| 融资融券 margin_detail / margin | ✓ | — | 10-23 | ✓（4485 行）| — |
| 龙虎榜 top_list / top_inst / hm_list / hm_detail | ✓ | — | 10-23 | — | 游资名录 110、游资明细 326 行 |
| 板块 ths_index / ths_member / kpl_concept_cons / dc_index | ✓ | — | — | **ths_index 全量 2722 条**（promax 回落时只回 5 条）| dc_index 必须传 idx_type |
| 财务 fina_indicator(_vip) / income_vip / forecast / share_float / stk_holdertrade / stk_holdernumber / dividend / repurchase / block_trade | ✓ | — | — | ✓ | fina_indicator_vip、income_vip 单次 50～70 秒 |
| 技术因子 stk_factor | ✓ | — | 10-23 | ✓ | stk_factor_pro 22～42 秒且一次超时，**未放进白名单** |
| 公告 anns_d、新闻 news、研报 | ✗ | — | — | — | 不支持 |

## 4. 与现有数据源对比：能补哪些缺口
| 我们的缺口 | teajoin 能否补 |
|---|---|
| 全市场历史分钟K（2020 起）| **不能**（无权限；小石只保留近 3 个月）。需向卖家加购历史分钟权限，或另找来源 |
| 集合竞价 | **能**：开盘竞价 stk_auction_o 2020 起全市场；收盘竞价 2025 起；9:25 快照只有近期 |
| 涨停榜 limit_list_d（含封单额/开板次数）| **能**：2020-01-02 起全市场、含 U/D/Z。本地文件 limit_list_d 缓存只到 2020-08-21，可由 teajoin 补全（每个交易日 1 次请求约 1500 次；单次约 10～15 秒，串行约 4～5 小时，建议 3～4 并发约 1.5 小时，错开盘中）|
| tick / 盘口 | **不能** |
| 筹码空洞 | **已补**（4 个日子）|
| 额度：promax、quantdata 今天同时耗尽 | **缓解**：teajoin 没有公布日额度，作为第三个兜底源，已验证可在两家耗尽时接手批量接口（不能接手 rt_k）|

## 5. 延迟与稳定性
- 小请求：中位数 ≈4.3 秒，范围 1.6～14 秒（12 次连续小请求 0 失败）；整体比 promax/quantdata 慢得多。
- 全市场单日表（daily、moneyflow 等）：6～23 秒；财务全市场 VIP 50～70 秒；dc_index 20 秒。
- 第一轮 87 次请求中：2 次 ConnectTimeout（limit_step、block_trade，重试成功），约 10% 的“重查询”返回业务码“query capacity busy”（上游并发满，重试/稍后即成功）；1 次服务端 query timeout（stk_factor_pro）。
- 结论：**适合做兜底和批量回补，不适合做实时主源**。

## 6. 数据质量核对（2026-10-23，对本地库）
- daily 收盘价：5565/5565 一致（teajoin 5572 行，本地 5565 行）。
- cyq_perf cost_50：5565/5565 一致。

## 7. 网关梳理：已有机制、缺口、本次改动
**已有（核对代码）**：每源每分钟限流 + 分接口限流 + 并发上限；每源每接口熔断（3 次失败开，限流/额度立即开，半开单探针，跨进程共享，live/research 命名空间隔离）；重试 2 次，指数退避 0.5×2ⁿ 封顶 5 秒 + 抖动；额度耗尽识别（42901/“每日上限”→当天整源跳过）+ 补数 60% 预算闸；接口级选路表 + 同级负载均衡；promax 在 research 命名空间盘中限速 30 次/分钟并按窗口令牌桶让路给 rt_k。

**发现的缺口与本次处理**
1. 熔断半开探测每次失败固定再冷却 5 分钟 → 一个真挂了的源（如小石日K）每 5 分钟被打一次。**已改**：探测连续失败按 5→10→20 分钟指数拉长（上限 20 分钟，可用 `ASHARE_BREAKER_PROBE_MAX_SECONDS` 调），成功清零，带抖动；新增跨进程计数表。
2. 盘中让路只对 promax 生效。**已改**：对其他源增加同类限速（teajoin 默认 30 次/分钟；quantdata 默认 0=不限，保持原行为，可用 `ASHARE_TUSHARE_QUANTDATA_RESEARCH_INTRADAY_RPM` 打开）。
3. “HTTP 200 + 业务码 + query capacity busy” 被当作普通错误。**已改**：识别为容量忙，30 秒冷却并换源，不计硬故障。
4. 监控里没有“网关容量”：promax 额度耗尽导致 rt_k 失败时没有一项能直接看到。**已加**：数据质量巡检新增 `gateway:providers`（各源今日请求数/日额度/是否耗尽/live 熔断），全耗尽=红，任一耗尽或≥80% 额度=黄；在数据健康页和告警面板可见。
5. “成功但被截断”（ths_index 只回 5 条）没人发现。**已加**：主源返回 <500 条时自动向 teajoin 核实并采用更完整的结果；同时入库时告警。
6. 两家源同天耗尽即全线失败（单点）。**已缓解**：teajoin 兜底 45 个批量接口。
**建议但没做**（需要你批准或观察一周后再动）：
- 把耗额度的批量日更接口（cyq_perf、limit_list_d、margin_detail、moneyflow）的**主源**改成 teajoin，把 promax/quantdata 额度留给实盘 rt_k。可在数据源管理页拖拽优先级即时生效、可随时回退；目前未改，因为 teajoin 延迟高、偶发超时，先观察稳定性。
- rt_k 目前没有第三个 Tushare 协议备源（teajoin 不支持）。实盘行情在 promax/quantdata 全耗尽时只剩新浪与小石快照兜底，建议给 rt_k 预留固定额度（比如每日额度的 30%）且日更/补数任务不得占用。
