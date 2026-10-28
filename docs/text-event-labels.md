# 文本事件标签

文本事件只用于展示和研究。它不进入 `feature_builder`、`research_runner`、推荐排序、回测成交和结算。页面上固定写着「仅供参考，不参与选股」。

LLM 默认关闭。环境变量 `ASHARE_TEXT_EVENT_LLM_ENABLED` 不设或为 false 时，不会向模型发请求。入库脚本本身也不调用模型。

## 架构

```
teajoin / 现有上游
    │  get_pro()：420 次/分钟量级限速、重试、熔断、超时换源
    ▼
text_event                  原文、发布时间、available_at、时间质量
    │
    ├─ 规则预筛（股东会 / 律师意见书 / 制度修订 → 中性，model=rule）
    ▼
text_event_label            标签、分数、依据、旧关键词对照、input_hash
    │
    ├─ GET /api/text-events/labels?code=      只读
    └─ 个股抽屉「文本事件标签」
```

选股链路不读这两张表。`news_risk_count` 等旧字段保持原样，没有接上新标签。

模型调用走 `model_route_config` 里已经配好的 OpenAI 兼容路由：按优先级换路由、进程内熔断、`api_call_logs` 记成本（`skill_id=text_event_label`）。温度固定为 0，不使用路由行上的 temperature。提示词版本是 `text-event-v1`。

## 接入了哪些 teajoin 接口

这些接口本来就在 `TEAJOIN_APIS` 里，网关能调。这次补的是原文落库。日更脚本默认拉下面这些，再加一次 `cn_schedule` 快照。

| 接口 | 文本 | 时间 |
|---|---|---|
| `report_rc` | 标题、类型、机构、作者、评级 | `report_date`，只有日期 |
| `stk_surv` | `content` 全文，以及接待机构 | `surv_date`，只有日期 |
| `forecast` | `summary`、`change_reason`、预告类型、净利润变动幅度 | `ann_date`，只有日期 |
| `ths_hot` | `rank_reason`（没有原因的行丢弃） | `rank_time` 能解析则是精确时间 |
| `kpl_list` | `lu_desc`、`theme` | `lu_time` + 交易日，精确时间 |
| `limit_list_ths` | `lu_desc`、`tag` | `first_lu_time` + 交易日 |
| `st` | `st_reason`、`st_explain` | `pub_date`，只有日期 |
| `idx_anns` | 指数公告标题 | `ann_date`，无个股代码 |
| `express` | `perf_summary`、`remark` | `ann_date` |
| `fina_audit` | 审计结论 | `ann_date` |
| `eco_cal` | 宏观事件名称、公布值和前值 | `date` + `time` |
| `dividend` | 分红进度 `div_proc` | `ann_date`，不用除息日 |
| `repurchase` | 回购进度 `proc` | `ann_date` |
| `share_float` | 解禁股东和比例 | `ann_date`，不用未来的解禁日 |
| `stk_holdertrade` | 股东、增持或减持、比例 | `ann_date`；`avg_price` 不进正文 |
| `stk_alert` | `type`，通常是短码 | `start_date` |
| `stk_managers` | 姓名、职务、学历 | `ann_date` |
| `top_list` | 龙虎榜上榜原因 `reason` | `trade_date`，只有日期；收盘价和涨跌幅不进正文 |
| `top_inst` | 席位、买卖方向、上榜原因 | `trade_date` |
| `stk_shock` | 异常波动原因 | `trade_date` |
| `stk_high_shock` | 严重异常波动原因 | `trade_date` |
| `kpl_concept_cons` | 题材成分说明 `desc` | `trade_date`；个股代码取 `con_code` |

另外两条不在默认日更里，要带 `--ts-code`：

- `forecast_vip`：和 `forecast` 同一套文本。全市场 VIP 单次很重，只按股票回补。
- `namechange`：更名原因。探测里没有可用的全市场按日参数，只按股票回补。
- `express_vip`：和 `express` 同一套摘要。探测只能按股票拉，不进默认日更。

`cn_schedule` 没有按日参数，每次拉当前窗口。

已有公告表 `news_announcement` 不改。`--mirror-announcements` 把标题和最多 500 字摘要复制到 `text_event`，`source` 记成 `announcement:<原 source>`。旧的 `sentiment` 列保留。

`stk_surv` 的旧表 `institutional_surveys` 仍只存机构名和访客数，选股如果还在用那张表，口径不变。全文只在 `text_event.body`。

## 没有接入的接口

| 接口 | 原因 |
|---|---|
| `anns_d` | teajoin 目录没有。公告仍走东财 / 巨潮 / 小石，再镜像进文本层 |
| `news` | teajoin 目录没有 |
| `broker_recommend` | 只有月份、券商、代码，没有叙述。结构化表已经在用 |
| `disclosure_date` | 只有披露日期 |
| `dc_hot` | 探测字段只有排名、涨跌幅和现价，没有原因文本 |
| `rt_k` / `rt_min` | 行情，不是文本，teajoin 也不提供 |
| `stock_company` | 公司简介是静态档案，不是按日事件 |
| `pledge_stat` / `pledge_detail` | 没有原因叙述 |
| `fina_mainbz` | 主营构成是数值 |
| `fund_div` | 基金分红，不是个股事件 |
| `stk_rewards` | 职务和报酬数字；探测只能按股票拉，`title` 是职务 |
| `dc_concept_cons` | 按 `trade_date` 探测为空，且需要 `theme_code`，没有稳定的全市场按日文本 |

其余 teajoin 目录里的接口是行情、财务数字、期货、宏观数值或成分名单，没有可打标的叙述，不进这层。

网关行为：`routing_table.json` 的 `manual.first` 把上表新接口的 teajoin 放在链首。表里没点名的 promax / quantdata 仍按优先级接在后面。超时、`query capacity busy`、熔断都由现有路由器换下一家。单日失败记在脚本输出里，重跑同一天会按 `(source, event_id)` 覆盖，不会插出重复行。

密钥只读现有的 `ASHARE_TUSHARE_TEAJOIN_TOKEN` 和模型路由表里的 key。代码、文档和提交里不写 key。

## 表

启动后端时 `init_db()` 会把 alembic 升到 `0029_text_event_labels`，创建这两张表。也可以在 `backend` 下执行 `alembic upgrade head`。迁移是只向前的，不删表。

`text_event`

| 字段 | 含义 |
|---|---|
| `source` / `event_id` | 唯一键。`event_id` 由接口名和业务字段哈希，正文改了也不会换 id |
| `code` | 6 位代码；指数公告和宏观事件为空串 |
| `title` / `body` | 原文。正文是提示词白名单字段拼出来的，不含股价、涨跌幅、目标价 |
| `publish_date` | `YYYY-MM-DD` |
| `publish_time` | 有钟点才填，`YYYY-MM-DD HH:MM:SS` |
| `time_quality` | `precise` 或 `date_only` |
| `signal_trade_date` | 收盘时第一次允许使用这篇文本的交易日 |
| `entry_trade_date` | 信号日的下一交易日，研究上的开盘买入日 |
| `available_at` | `signal_trade_date` 的 `15:00:00` |
| `payload_json` | 白名单字段，同样不含价格 |

时间规则（交易日来自本地 `trade_calendar`；日历不可用时退回周一到周五，摘要里的 `calendar` 会写 `weekday`，节假日可能偏一天）：

- 精确时间，并且落在交易日 15:00 之前（含盘前）：当天收盘出信号，下一交易日开盘买。
- 15:00 及之后、非交易日、或只有日期：下一交易日收盘才出信号，再下一交易日开盘买。只有日期按盘后公告处理，避免把未知时点当成盘中已经看到。

`text_event_label`

| 字段 | 含义 |
|---|---|
| `label` | `利好` / `利空` / `中性` |
| `category` | `业绩` `回购` `减持` `解禁` `处罚` `重组` `定增` `人事` `股东会` `研报评级` `调研` `其他` |
| `score` | -100 到 100 的整数。负数是利空力度，正数是利好力度，0 是中性。**不是预测涨跌幅** |
| `confidence` | 0 到 1。规则预筛固定为 1 |
| `rationale` | 依据。提示词要求不写价格 |
| `model` | 路由上的模型名，或 `rule` |
| `prompt_version` | 当前为 `text-event-v1`。改提示词要换版本，旧行不会被当成同一缓存 |
| `label_origin` | `rule` 或 `llm` |
| `keyword_label` / `keyword_sentiment` / `keyword_hits` | 旧关键词对照，保留。几乎只判利空或中性 |
| `input_hash` | `sha256(提示词版本 + 来源 + id + 代码 + 日期 + 标题 + 全文)` |
| `status` | `ok` 或 `failed`。失败行下次会重试 |
| `prompt_tokens` / `completion_tokens` / `latency_ms` | 模型用量。规则行为 0 |

唯一键是 `(source, event_id, prompt_version)`。哈希没变且状态是 `ok` 时直接复用，不重复打模型。

## 规则和模型

关键词词表只留在 `app/services/text_events/keywords.py`。东财公告、巨潮、小石、anns_d 和东财新闻都改成调用它，判定和原来相同：风险词优先，公告路径默认不判利好。对照列用的也是这张表，所以「解除ST风险警示」「减持计划完成」仍会被关键词打成利空。这是故意留着的对照，不在关键词层里修正。

规则预筛只看标题：

- 股东会 / 股东大会 → 中性，类别「股东会」
- 法律意见、律师意见、律师事务所 → 中性
- 制度修订、公司章程、议事规则 → 中性

其余才交给模型。提示词禁止预测股价和后续走势，用户消息里也没有价格字段。正文超过 6000 字会在提示词里截断；`input_hash` 仍按全文计算。

并发和限速：

- `ASHARE_TEXT_EVENT_LLM_CONCURRENCY` 默认 2
- `ASHARE_TEXT_EVENT_LLM_QPM` 默认 20（按固定间隔放行）
- `ASHARE_TEXT_EVENT_LLM_MAX_RETRIES` 默认 2。JSON 不符合合同、或同一路由的暂时失败会重试；鉴权、余额、模型不存在会换下一条路由
- 金额估算要另设 `ASHARE_TEXT_EVENT_LLM_PRICE_PER_MILLION_PROMPT` 和 `_COMPLETION`（每百万 token 的单价）。不设就只报 token

## 怎么在本机试跑

云端没有 teajoin key、模型 key 和数据库，下面的步骤要在你自己的机器上做。先确认 `backend/.env` 里已有 teajoin token，并且 `model_route_config` 里有一条能用的路由。

```bash
cd backend
.venv/bin/alembic upgrade head

# 拉一个交易日的原文。不调用模型。
.venv/bin/python scripts/text_events/ingest_text_events.py --date 2026-10-23

# 把已有公告标题/摘要镜像进来（可选）
.venv/bin/python scripts/text_events/ingest_text_events.py --mirror-announcements --start 2026-10-01 --end 2026-10-23

# 先看规则预筛，确认不会打模型（开关默认关）
.venv/bin/python scripts/text_events/label_sample.py --limit 500

# 真正打 500 条
ASHARE_TEXT_EVENT_LLM_ENABLED=true \
  .venv/bin/python scripts/text_events/label_sample.py --limit 500 --out /tmp/text-event-sample.json

# 研究用超额收益。样本的 entry_trade_date 要落在 --start/--end 里，日历要覆盖到之后约 25 个交易日
.venv/bin/python scripts/text_events/excess_return_study.py \
  --start 2024-01-01 --end 2026-10-23 --out /tmp/text-event-excess.json
```

抽样报告里有：标签分布、类别分布、失败率、延迟均值 / p50 / p95、token、能算则给出的金额、和旧关键词一致的比例，以及最多 20 条分歧样例。

只读接口：`GET /api/text-events/labels?code=000001&limit=5`。个股抽屉（中线/长线点开一只股票）在入选原因下面显示标签、分数和依据。

## 超额收益口径

`excess_return_study.py` 只读 `text_event_label`、`daily_kbars`、`daily_basic`、`trade_calendar`。

- 买入日 = 入库时算好的 `entry_trade_date`（信号日收盘之后的下一交易日开盘）
- T+k = 第 k 个交易日收盘 / 买入开盘 − 1。T+1 就是买入当天开盘到收盘
- 基准 = 同一个买入日、同一个市值五分位（`daily_basic.total_mv`）里其他股票的等权收益
- 同档对照少于 5 只，或没有市值，这条不进均值，计在 `events_skipped`
- 输出里的 `participates_in_selection` 固定为 false

## 风险

- **训练集泄露**：提示词如果夹进股价或之后的涨跌，模型会用结果倒推标签。现在的合同把价格字段挡在正文外，并写明不许预测走势。换提示词必须升 `prompt_version`，并抽查 `user_prompt` 里没有价格。
- **时间对齐**：只有日期的公告被当成盘后，信号再晚一个交易日。这比「公告夜发布、次日开盘就能买」更保守。精确时间和日期对不上时也退回只有日期。日历缺失时用工作日，长假会偏。
- **对照基线**：关键词对照故意保留误伤，不能拿它当正确标签。分歧样例才是要看的。
- **成本**：默认关。500 条的 token 以抽样报告为准；没配单价时 `cost` 为 null。
- **上游**：teajoin 慢、会返回容量忙。日更失败的日期重跑即可。`express` / `stk_managers` 若拒绝 `ann_date`，该日记 error，不挡住别的接口。
- **不要接进选股**：这些分数没有经过和现有策略相同的因果检验。接进 `feature_builder` 会改变 picks。见教训 L-195。
