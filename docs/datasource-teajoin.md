# 数据源 teajoin（DaoShare）接入与探测报告

探测时间：2026-10-26 15:10–15:40（收盘后，不影响实盘）。原始结果：`docs/teajoin_probe_results.json`（第一轮 87 项）、`docs/teajoin_probe_deep.json`（第二轮：重试、字段完整度、与本地库对账、稳定性）。探测脚本：`backend/scripts/probe_teajoin.py`、`probe_teajoin_deep.py`（密钥只从 `backend/.env` 读取，不打印不落盘）。

## 1. 它是什么、怎么鉴权
- 一个 **Tushare Pro 协议的第三方代理**（文档页 https://teajoin.com/guide ，文档本身需前端渲染，内容已全部读取）。
- 鉴权：HTTP POST 到根路径 `https://teajoin.com`，JSON `{"api_name","token","params","fields"}`，与 quantdata 同一种方式（本项目 `AuthMode.JSON_TOKEN`）。也支持 tushare SDK（改 `_DataApi__http_url`）和 MCP。
- 返回格式与 Tushare 一致（`data.fields` / `data.items`，大表有 `has_more` + `offset` 翻页，单页上限 3000～10000 随接口）。
- 套餐：**15000 积分通用套餐**。官方 guide（2026-10-26 18:49 重新通读）写“已支持接口目录 195 个”，“完整 catalog 接口名称 205 个”（含 2026-10-11 新增 7 个、独立权限的 stk_mins/etf_mins）；用户口头说的 193 个应为同一目录的不同口径。**以 205 个完整 catalog 为准**。**不含**：实时行情（rt_k）、新闻公告、港美股。**历史分钟 stk_mins：2026-10-26 21:00 用户已单独开通，21:0x 起实测可用，见 §8。**集合竞价类（stk_auction/_o/_c）已包含。
- 限流：guide 写最高 450 次/分钟，**卖家 19:32 口头确认 500 次/分钟**；我们按 **420 次/分钟**（留约 15% 余量，`ASHARE_TUSHARE_TEAJOIN_RATE_LIMIT_PER_MINUTE`），单进程并发上限 4（`ASHARE_TUSHARE_TEAJOIN_MAX_CONCURRENCY`）；盘中研究/回补进程另限 30 次/分钟给实盘让路。**文档没有写日额度**。注意：上游常见的瓶颈不是每分钟次数，而是“同时多路重查询”时返回 `query capacity busy`（见第 5 节）。Key 的到期日需用兑换码在 https://teajoin.com/redeem 查看（我们手里只有 Key，没有兑换码，**到期时间未知**，建议用户确认）。
- 价格/续费：文档写“闲鱼购买兑换码、到期找卖家续费”，无公开价目。

## 2. 接入方式（小步、可回退）
- 密钥：只放 `backend/.env`（已被 .gitignore 覆盖，权限 600）：`ASHARE_TUSHARE_TEAJOIN_TOKEN`（另有 `_URL`、`_RATE_LIMIT_PER_MINUTE`、`_MAX_CONCURRENCY`、`_RESEARCH_INTRADAY_RPM`、`_LEAD`）。代码、文档、提交里没有密钥。
- **能力登记**：`backend/app/services/datasource/teajoin_capability.py`（204 个已实测可用接口、延迟敏感名单、大表/小表超时分档、不路由名单及原因）。测试 `test_teajoin_lead_20261026.py` 会校验登记表与探测结果一致。
- **主源规则**（2026-10-26 18:48 用户指令“teajoin 优先级设到最高”）：`tushare_transport.LeaderSpec` + 路由器 `_apply_leaders`。
  1. 在 204 个已登记接口上 teajoin 排链首，promax/quantdata 退为备源；
  2. **失败/超时/容量忙/熔断/额度耗尽 → 当次立即换下一家**（主源调用只试 1 次，不退避重试；读超时：小表 15s、普通 40s、大表 90s）；
  3. **盘中（工作日 09:15–15:05）live 进程里延迟敏感的 36 个接口（日线、资金流、涨停榜、龙虎榜、竞价、交易日历等）teajoin 让位，排末位兜底**，保证实盘选股不被它的延迟（小请求约 3–5 秒，原源约 0.1–2 秒）拖慢；盘后、以及 research 回补进程（本来就限速）teajoin 仍是主源，这样批量回补不再消耗 promax/quantdata 额度，额度留给 rt_k；
  4. teajoin 近期健康度过低（`lb_health<0.25`）时不做主源，直接走原链路；
  5. **rt_k / rt_min / stk_mins / anns_d / news 不在名单内，链路完全不变**。
- 回退：`ASHARE_TUSHARE_TEAJOIN_LEAD=false` 重启即回到“仅末位兜底”；删掉 `ASHARE_TUSHARE_TEAJOIN_TOKEN` 重启则完全回到接入前；也可在数据源管理页停用 teajoin。
- 监控：数据质量巡检 `gateway:providers` 会显示 teajoin 是否为主源；指标 `provider_lead_choice`、`provider_lead_skipped`、`provider_fallback` 可看主源命中和降级次数。

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
| **全市场历史分钟K stk_mins** | ✓ | 2015-06-01 起已验证 | 当日数据收盘后不立即有（10-26 21:15 仍为 0 行，T+1） | 1/5/15/30/60min | 21:00 开通；见 §8。ETF 分钟用 etf_mins；指数/ETF 走 stk_mins 返回空 |
| rt_k / rt_min / 实时逐笔 / 盘口 / tick | ✗ | — | — | — | rt_k、rt_min 不支持；猜测的 rt_tick/realtime_tick/realtime_quote/stk_ticks 全部“未收录”。**tick/盘口缺口无法由 teajoin 补** |
| 筹码 cyq_perf | ✓ | 已验证 2026-07～10 | 10-23 | ✓ 约 5550 只 | **与本地库 cost_50 逐行对账 5565/5565 完全一致**；cyq_chips（分布）2026-06-25 起停更 |
| 资金流 moneyflow / moneyflow_dc / moneyflow_ths | ✓ | — | 10-23 | ✓ | 慢（18～23 秒）|
| 融资融券 margin_detail / margin | ✓ | — | 10-23 | ✓（4485 行）| — |
| 龙虎榜 top_list / top_inst / hm_list / hm_detail | ✓ | — | 10-23 | — | 游资名录 110、游资明细 326 行 |
| 板块 ths_index / ths_member / kpl_concept_cons / dc_index | ✓ | — | — | **ths_index 全量 2722 条**（promax 回落时只回 5 条）| dc_index 必须传 idx_type |
| 财务 fina_indicator(_vip) / income_vip / forecast / share_float / stk_holdertrade / stk_holdernumber / dividend / repurchase / block_trade | ✓ | — | — | ✓ | fina_indicator_vip、income_vip 单次 50～70 秒 |
| 技术因子 stk_factor | ✓ | — | 10-23 | ✓ | stk_factor_pro 22～42 秒且一次超时，**未放进白名单** |
| 公告 anns_d、新闻 news、研报 | ✗ | — | — | — | 不支持 |

### 3.1 官方完整目录逐个实测（205 个：204 个已纳入，1 个不路由）
原始结果：`docs/teajoin_catalog_probe.json`（第一轮）+ `teajoin_catalog_probe_r2.json`（第二轮：参数不对/容量忙/超时的重试）。每个接口只做 1～4 次小调用（limit 50，2 线程、间隔 ≥1.5s）。**这轮探测验证“能不能调通、返回哪些字段、最新到哪天”，不代表全市场行数**（行数上限 50）。

结果：**204 个接口全部可调通**（含第二轮修正参数后的 hm_list/namechange/ths_index/ths_member/dc_index/index_daily/cyq_chips 等），其中 `dc_concept_cons`（需 theme_code 才有数据）、`stk_auction_c`（当晚数据收盘后才出）、`ggt_daily`/`index_weight`/`pledge_detail`/`fut_weekly_detail` 在个别参数下为空，属参数问题而非不可用。**stk_mins 已于 21:00 开通并纳入（共 205 个，全部路由）**；另外 rt_k/rt_min/anns_d/news/tick/盘口不在 teajoin 目录里。

为什么此前只有 45 个：第一轮只探测了“我们系统已经在用的数据集”并要求与本地库对账，白名单是**刻意收窄**不是漏掉；本轮按用户指令把目录全部实测后扩到 204 个。目录里与我们系统直接对应的有 59 个（日线/指标/复权/涨跌停/涨停榜/龙虎榜/资金流/融资融券/筹码/竞价/财务/股东/板块/沪深港通/周月线等），其余（期货、期权、可转债、基金、宏观、美港股参考等）暂无消费方，已登记但没有调用方，不影响现有链路。

| 分类 | 接口（✓可用 / ⚪调用成功但当次参数下为空；括号=探测到的最新日期） |
|---|---|
| 股票基础与行情 | stock_basic✓(20000511)、trade_cal✓(20261023)、hk_tradecal✓(20261031)、us_tradecal✓(20261031)、namechange✓(20120801)、stk_rewards✓(20251231)、bak_basic✓(20261023)、stock_st✓(20261023)、st✓(20260701)、stock_hsgt✓(20261026)、bse_mapping✓(20240111)、daily✓(20261023)、weekly✓(20261023)、monthly✓(20260831)、stk_weekly_monthly✓(20261023)、stk_week_month_adj✓(20261023)、adj_factor✓(20261023)、suspend_d✓(20261023)、daily_basic✓(20261023)、stk_limit✓(20261023)、moneyflow✓(20261023)、moneyflow_hsgt✓(20261023)、hsgt_top10✓(20261023)、ggt_top10✓(20261023)、hk_hold✓(20261023)、bak_daily✓(20261023) |
| 财务、公司与市场参考 | income✓(20260630)、income_vip✓(20260630)、balancesheet✓(20260630)、balancesheet_vip✓(20260630)、cashflow✓(20260630)、cashflow_vip✓(20260630)、forecast✓(20260930)、forecast_vip✓(20151231)、express✓(20221231)、express_vip✓(20221231)、dividend✓(20260930)、fina_indicator✓(20260930)、fina_indicator_vip✓(20260930)、fina_audit✓(20251231)、fina_mainbz✓(20260630)、fina_mainbz_vip✓(20260630)、disclosure_date✓(20260930)、top10_holders✓(20260630)、top10_floatholders✓(20260630)、top_inst✓(20261023)、pledge_stat✓(20261016)、pledge_detail✓(20181114)、repurchase✓(20261027)、share_float✓(20261023)、stk_seasoned✓(20261023)、block_trade✓(20261012)、stk_holdernumber✓(20261022)、stk_holdertrade✓(20261012)、broker_recommend✓(202610) |
| 资金流、打板与题材数据 | margin✓(20261023)、margin_detail✓(20261023)、moneyflow_ths✓(20261023)、moneyflow_dc✓(20261023)、moneyflow_ind_ths✓(20261023)、moneyflow_ind_dc✓(20261023)、moneyflow_cnt_ths✓(20261023)、moneyflow_mkt_dc✓(20261023)、limit_list_d✓(20261023)、limit_list_ths✓(20261023)、limit_cpt_list✓(20261023)、limit_step✓(20261023)、top_list✓(20261023)、kpl_list✓(20261023)、kpl_concept_cons✓(20261023)、kpl_concept✓(20261023)、ccass_hold✓(20261023)、ths_hot✓(20261023)、dc_hot✓(20261023)、dc_index✓(20261023)、dc_member✓(20261023)、dc_daily✓(20261023)、dc_concept✓(20261023)、dc_concept_cons⚪、tdx_index✓(20261023)、tdx_member✓(20261023)、tdx_daily✓(20261023) |
| 同花顺、游资、筹码与量化特色数据 | ths_daily✓(20261023)、ths_index✓(20191231)、ths_member✓、hm_list✓、hm_detail✓(20261023)、cyq_perf✓(20261023)、cyq_chips✓(20260625)、stk_factor✓(20261023)、stk_factor_pro✓(20261023)、stk_surv✓、stk_nineturn✓(2026-10-23 00:00:00)、stk_auction✓(20261023)、stk_auction_o✓(20261022)、stk_auction_c⚪、report_rc✓(20261023) |
| 指数、基金与 ETF | index_basic✓(20120109)、index_daily✓(20261023)、index_weekly✓(20261023)、index_monthly✓(20260930)、index_weight✓(20260930)、index_dailybasic✓(20261023)、index_classify✓、index_member_all✓、sw_daily✓(20261023)、ci_index_member✓、fund_basic✓(20120817)、fund_company✓(20091229)、fund_manager✓(20150612)、fund_nav✓(20261024)、fund_daily✓(20261023)、fund_adj✓(20261023)、fund_div✓(20261023)、fund_portfolio✓(20260630)、fund_share✓(20261023)、etf_auction✓(20261023)、etf_basic✓(20260611)、etf_index✓(20250120)、etf_limit✓(20261023)、etf_share_size✓(20261023) |
| 期货、期权、可转债、债券与宏观 | fut_basic✓(20060717)、fut_daily✓(20261023)、fut_holding✓(20261023)、fut_wsr✓(20261023)、fut_settle✓(20261023)、fut_mapping✓(20261026)、fut_index_daily✓(20261023)、fut_trade_cal✓(20261031)、fut_weekly_monthly✓(20261023)、fut_weekly_detail✓、opt_basic✓(20150212)、ft_limit✓(20261023)、opt_daily✓(20261023)、cb_basic✓(20171129)、cb_daily✓(20261023)、cb_call✓(20261022)、cb_issue✓(20261023)、cb_rate✓、cb_rating✓(20200518)、cb_share✓(20210201)、cb_factor_pro✓(20261023)、top10_cb_holders✓(20201231)、bond_blk✓(20261023)、bond_blk_detail✓(20261023)、bc_otcqt✓(20261023)、bc_bestotcqt✓(20261023)、hk_basic✓(20211230)、us_basic✓、us_tbr✓(20261023)、us_tltr✓(20261023)、us_trltr✓(20261023)、us_tycr✓(20261023)、cn_gdp✓、cn_cpi✓(200712)、cn_ppi✓(202512)、cn_pmi✓、cn_m✓(202512)、cn_schedule✓(202612)、shibor✓(20261023)、shibor_quote✓(20261023)、shibor_lpr✓(20260820)、hibor✓(20261023)、libor✓(20200624)、eco_cal✓(20261023)、repo_daily✓(20261023)、sge_basic✓(白天：9:00至15:30，夜间：20:00至次日02:30)、sge_daily✓(20261023)、mkt_idx_bmk✓、gz_index✓(20190304)、wz_index✓(20190301)、new_share✓、stk_account✓(20190222)、slb_len✓(20250725)、slb_len_mm✓(20250725)、slb_sec✓(20240930)、slb_sec_detail✓(20240710)、ci_daily✓(20261023)、idx_anns✓(20261023)、idx_factor_pro✓(20261023)、fund_factor_pro✓(20261023) |
| 目录其余（2026-10-11 新增等） | daily_info✓(20261023)、etf_mins✓(2026-10-23 09:35:00)、etf_sh_cons✓(20261023)、etf_sz_cons✓(20261023)、fut_daily_adj✓(20261023)、fut_inv_weekly✓(20261023)、fut_rcpt_mat✓(20261009)、fut_trade_param✓(20261023)、fx_daily✓(20261023)、fx_obasic✓、ggt_daily✓(20260909)、index_global✓(20261023)、margin_secs✓(20261023)、sf_month✓(202512)、stk_ah_comparison✓(20261026)、stk_alert✓(20261103)、stk_high_shock✓(20261023)、stk_managers✓(20251216)、stk_shock✓(20261023)、stock_company✓、sz_daily_info✓(20261023)、us_trycr✓(20261023)、vix_index✓(20261023) |

## 4. 与现有数据源对比：能补哪些缺口
| 我们的缺口 | teajoin 能否补 |
|---|---|
| 全市场历史分钟K（2020 起）| **不能**（无权限；小石只保留近 3 个月）。需向卖家加购历史分钟权限，或另找来源 |
| 集合竞价 | **能**：开盘竞价 stk_auction_o 2020 起全市场；收盘竞价 2025 起；9:25 快照只有近期 |
| 涨停榜 limit_list_d（含封单额/开板次数）| **能**：teajoin 上游 2020-01-02 起全市场、含 U/D/Z；**本地只补近 3 年（2023-10-27 起）**，见第 5 节 |
| tick / 盘口 | **不能** |
| 筹码空洞 | **已补**（4 个日子）|
| 额度：promax、quantdata 今天同时耗尽 | **缓解**：teajoin 没有公布日额度，作为第三个兜底源，已验证可在两家耗尽时接手批量接口（不能接手 rt_k）|

## 4.1 每类接口最终源顺序（2026-10-26 21:30 起生效；promax 已降为少额度用户）
promax 额度**专留 rt_k / rt_min**；其余接口 teajoin 做主源，promax 不再进入这些接口的链路（唯一源时除外），补数/研究进程**绝不动用 promax**。
| 接口类别 | 盘后/研究回补 | 盘中（工作日 09:15–15:05）live 进程 |
|---|---|---|
| rt_k、rt_min（实时） | promax → …（回补不调用） | promax → 新浪 → 腾讯 → AKShare 全市场快照 → 分钟 rt_min → 小石（见 §9） |
| **stk_mins（历史分钟）** | **teajoin**（quantdata 无权限；promax 不参与） | **teajoin**（超时 15s 自动降级；今日数据 T+1，当日空时由上层走小石分钟） |
| anns_d（公告） | promax（唯一源，live 可用；回补不可用） | 同左 |
| 其余全部 teajoin 支持的接口（日线/指标/复权/涨跌停价/停复牌/资金流/涨停榜/龙虎榜/筹码/集合竞价/ETF 分钟/财务…） | **teajoin** → quantdata（promax 不进链） | **teajoin**（盘中读超时 15s，重表 30s，超时/失败立即降级到 quantdata）→ quantdata |
| promax/quantdata 本来就不支持的接口（板块行情、东财/通达信、基金、期货、可转债、宏观等） | teajoin（唯一源） | teajoin（唯一源） |
| 链路内主源失败（超时/容量忙/偶发 invalid api key/熔断） | 批量：就地短退避重试（5 次）再换源；不开熔断 | live：不重试，30s 冷却并立即换源 |

开关（都可回退）：`ASHARE_TUSHARE_TEAJOIN_LEAD`（teajoin 主源总开关）、`ASHARE_TUSHARE_TEAJOIN_INTRADAY_YIELD=true`（恢复盘中 36 个延迟敏感接口让原源先走）、`ASHARE_TUSHARE_TEAJOIN_INTRADAY_READ_TIMEOUT_SECONDS`（默认 15）、`ASHARE_TUSHARE_PROMAX_RTK_RESERVED=false`（让 promax 回到末位兜底）、`ASHARE_TUSHARE_PROMAX_RESEARCH_ENABLED=true`（允许补数进程用 promax）。

## 5. 延迟与稳定性
- 小请求：中位数 ≈4.3 秒，范围 1.6～14 秒（12 次连续小请求 0 失败）；整体比 promax/quantdata 慢得多。
- 全市场单日表（daily、moneyflow 等）：6～23 秒；财务全市场 VIP 50～70 秒；dc_index 20 秒。
- 第一轮 87 次请求中：2 次 ConnectTimeout（limit_step、block_trade，重试成功），约 10% 的“重查询”返回业务码“query capacity busy”（上游并发满，重试/稍后即成功）；1 次服务端 query timeout（stk_factor_pro）。
- 结论：**适合做盘后/批量主源，不适合做盘中实时主源**（所以盘中延迟敏感接口让位，实时接口根本不路由）。2026-10-26 19:30 后卖家确认限流 500 次/分钟，按用户指令不再继续做速度/延迟探测。
- **容量忙（query capacity busy）的真实形态**：HTTP **429** + `{"code":-1,"msg":"query capacity busy"}` + `Retry-After: 2`（不是 200+业务码）；4 路并发的历史单日重查询约一半会遇到。
  - **批量/补数进程**（research 命名空间）：就地短退避重试，最多 5 次，间隔 2–5 秒×次数（带抖动），**不计熔断**；重试仍忙才交给路由器换下一家；指标 `provider_capacity_busy_retry`、`provider_capacity_busy{mode=batch}`。
  - **live 进程**：保持保守，不重试，30 秒冷却并立即降级到原链路；指标 `provider_capacity_busy{mode=live}`。
  - teajoin 建连超时放宽到 15 秒（上游拥塞时 5 秒会误判为超时并开熔断）；容量忙对负载均衡健康度只记 0.5 分，不会因偶发忙就丢掉主源。
  - 实测：经网关 4 路并发 limit_list_d 6 个历史日全部成功（含 2 次就地重试），熔断保持关闭。
- **limit_list_d 本地缓存范围（用户 2026-10-26 20:08 决定：只补近 3 年）**：`data/research_cache/limit_list_d/` 覆盖 **2023-10-27 起至最新交易日**（回补进行中）；另保留此前已落盘的**早期片段**：2020-01-02~2020-12-31 全年、2021-01-04~01-19、2023-01-03~2023-10-26 内已落的部分——这些是中途停下的残留，不保证连续，**研究时请以 2023-10-27 之后为准**。数据质量巡检里该项标注“回补范围 2023-10-27 起”，回补期间为 provisional（最高黄色）。
- 回补脚本：统一用走网关的 `scripts/shortline_research/fetch_limit_list_d.py <start> <end>`（限流/监控/额度统计都在）；直连脚本 `fetch_limit_list_d_teajoin.py` 已弃用但保留文件。

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
**第 7 步（用户 18:48 指令，已做）**：teajoin 在 204 个接口上做主源（规则见第 2 节），带超时分档、1 次尝试、立即降级、盘中让位。
**仍建议**：
- rt_k 目前没有第三个 Tushare 协议备源（teajoin 不支持）。实盘行情在 promax/quantdata 全耗尽时只剩新浪与小石快照兜底，建议给 rt_k 预留固定额度（比如每日额度的 30%）且日更/补数任务不得占用。

## 8. stk_mins（历史分钟）实测（2026-10-26 21:00–21:30，严格小量，约 100 次请求）
脚本 `backend/scripts/probe_teajoin_stk_mins.py`，原始摘要 `docs/teajoin_stk_mins_probe.json`。

| 项目 | 结论 |
|---|---|
| 可用性 | ✓ 可用。HTTP 200，字段 `ts_code, trade_time, open, close, high, low, vol, amount` |
| 频率 | 1min / 5min / 15min / 30min / 60min（其它值报“freq 仅支持…”）。单日 1min = 241 根（含 09:30 集合竞价成交那一根，到 15:00）；5min 48 根、15min 16、30min 8、60min 4 |
| 历史起点 | 至少到 **2015-06-01**（1min 当日 241 根），2020-01-02、2023-10-27、2025-03-14 均完整。我们只需 2023-10-27 之后，**完全覆盖** |
| 覆盖范围 | 沪深主板/创业板/科创板/北交所新代码（920xxx）✓；**已退市股**可取（920305.BJ 退市前 1928 根 OK；更早退市的 600290.SH 退市前 10 天为 0 行，需按真实停牌/整理期核对）；**指数（000001.SH/399006.SZ）和 ETF（510300/159915）经 stk_mins 返回 0 行**，ETF 要用 etf_mins（510300 1 天 241 根 ✓）；北交所旧代码 430047 0 行（已换 920xxx） |
| 单次上限 | **8000 行，超出静默截断且保留最新的**（3 个月请求只回最近 8000 行，起点变成 08-31 14:14，没有任何报错）。**必须按 ≤30 个自然日（≈22 个交易日≈5300 行）窗口取**——现有 `_minute_query_windows`（`_STK_MINS_WINDOW_DAYS`）和 `len(frame)>=8000` 的截断保护已经满足，不用改 |
| 分页 | 支持 `limit` / `offset`（从最新一端往回数：limit=50 回最近 50 根；offset=1000,limit=300 回 10-16 13:26～10-19 14:24）。也可按日期窗口切 |
| 复权 | **不复权**（原始价）。与 tushare daily 同日 OHLC：3 只股票 × 4 个日子（2023-10-27、2025-03-14、2026-08-18、2026-10-23）日内 open/close 逐一吻合，high/low 个别日差 0.02～0.56 元（日线 high/low 含集合竞价价，分钟线只含连续竞价），属正常 |
| 单位 | **vol = 股，amount = 元**（不是手/千元）。与 tushare daily 对账：Σvol ÷ (日线 vol×100) = 1.0000，Σamount ÷ (日线 amount×1000) = 1.0000（12 个样本，最大偏差 0.01%）。与小石归档 `xiaoshi_min_all`（volume 股、amount 元、adjustment=raw）单位一致，000001 在 2026-08-18 的日合计量额完全一致（小石 238 根，比 teajoin 少 3 根） |
| 与库内 minute_kbars 对账 | 2026-10-23 的 000001/600519/300750：时间轴逐分钟对齐（偏移 0 时 close 100% 吻合，偏移 ±1 分钟只剩 2.5%），**volume 94% 逐根一致，日合计 100% 一致**；库里 **amount 全是 0**（teajoin 可补齐）；库里 high/low 与 teajoin 在约 20% 的分钟上差 0.01～0.1 元（库里是快照合成，teajoin 是真实分钟 OHLC，以 teajoin 为准）；2026-10-22 库里该日数据不完整（如 14:57 volume=0，teajoin 为 1348700） |
| 当日数据 | 10-23 可取，**10-26 在 21:15 仍是 0 行**（T+1 更新，非实时），所以今天的分钟线仍靠小石/实时源 |
| 速度 | 单次 1 天 ≈ 1.5～5s；1 个月（4579 行）≈ 4.5～7s；8000 行 ≈ 4s。并发 2 / 4 / 8 路：吞吐 15 / 41 / 57～83 次/分，**40 次并发请求中 busy / 429 = 0 次**。teajoin 当时对 limit_list_d 另有约 10% 偶发 403 “invalid api key”（见 §5/§7，已按瞬时拥塞处理） |
| 预估：近 3 年（2023-10-27 起，725 个交易日）全市场 1 分钟 | 约 5,700 只 × 每只 25 个 30 天窗口 ≈ **14.2 万次请求**，数据量约 9.7 亿行；按并发 8、约 70 次/分钟 ≈ **34 小时**（并发 4 约 55 小时；理论上限 420 次/分 ≈ 5.6 小时但未实测容量忙）。建议并发 6～8、分月落 parquet、可断点续传，夜间和周末跑 |
| 预估：只补首板（涨停）日 | 库里近 3 年涨停日（主板≥9.8%，创业板/科创板≥19.8%）约 5.6 万个“股票-日”，其中首板约 3 万；每个事件（含前后各 1 天）1 次请求 ≈ **3～5.6 万次请求，约 8～14 小时**，数据量约 3000 万行，完全可以 1 个晚上跑完 |

接入：`stk_mins` 已纳入 `TEAJOIN_APIS`，teajoin 做主源；teajoin 的 stk_mins 与 promax 共用同一道“分钟线合同闸门”（整分钟、仅连续竞价时段、无重复分钟，否则拒收并降级）；`NOT_ROUTED` 不再含 stk_mins。

## 9. rt_k（实时行情）在 promax 耗尽后的兜底
teajoin 不提供 rt_k，promax 是唯一 rt_k 主源，已降为少额度用户。现网 rt_k 链路（`quote_realtime.py`，缺哪几只就只向下一级要哪几只）：promax rt_k（1.5s 硬超时、熔断）→ 新浪 realtime_quote（tushare 爬虫，50 只/批）→ 腾讯 qt.gtimg（`.env` 里 `ASHARE_DATASOURCE_CUTOVER` 已含 quote.realtime.tencent，当前启用）→ AKShare 全市场快照（见下）→ quantdata rt_min（逐只、最多 60 只，只有现价）→ 小石快照。

