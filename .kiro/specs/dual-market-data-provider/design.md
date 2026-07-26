# 双市场数据 Provider 接入设计文档

## 1. 设计目标

系统继续采用“上游同步到本地 MySQL、业务查询以本地池为主”的架构。Fastapic 与 ai-tool 不直接替代本地数据池，而是通过 canonical `get_pro()` 为同步、缺口修复、辅助数据抓取和盘中实时叠加提供双 Provider 路由。盘中使用本地历史数据加实时查询；盘后在同步完成且健康闸门通过后直接使用本地池。

核心原则：

1. **MySQL 是查询主池**：`StockQuote`、`DailyKbar`、`MinuteKbar` 和辅助表保持现有权威查询地位。
2. **读写路径分离**：Provider Router 属于上游采集面；本地池属于策略查询面。
3. **交易时段分流**：盘中 local pool + realtime overlay；盘后 sync → health gate → local-only。
4. **调用契约不变**：现有 `pro.daily(...)`、`pro.stk_mins(...)`、`fetch_raw(...)` 及 MySQL upsert 调用方无需重写。
5. **数据域显式路由**：分钟/公告优先 ai-tool，宽覆盖同步接口优先 Fastapic。
6. **故障域隔离**：Session、认证、限流、breaker 和指标按 Provider 独立。
7. **安全默认**：凭据无代码默认值，异常和日志不回显凭据。
8. **合法空结果不是故障**：避免备用源差异制造伪数据。

## 2. 目标结构

```text
                         QUERY PLANE

盘中 auto ──> LocalPoolMarketSourceAdapter(include_realtime=True)
                  ├─ MySQL: StockQuote / DailyKbar / MinuteKbar
                  └─ realtime overlay
                       ├─ broad snapshot: existing realtime_quote
                       └─ final candidates: ai-tool rt_min / rt_k

盘后 cron ──> upstream sync ──> MySQL upsert ──> health/readiness gate
                                                    │ ready
                                                    ▼
                                  LocalPoolMarketSourceAdapter(refresh=False)
                                  （推荐阶段不访问上游）

                         INGESTION PLANE

sync / remediation / auxiliary / realtime helper
                         │
                         ▼
                tushare_client.get_pro()
                         │
                         ▼
                RoutedTushareSession
                ├─ route policy
                ├─ DataFrame adapter
                └─ strict fetch_raw adapter
                         │
               ┌─────────┴─────────┐
               ▼                   ▼
        FastapicProvider      AiToolProvider
        JSON token auth       X-API-Key auth
        30 QPM / conc. 1      API-specific QPM
        retry + breaker       retry + breaker
```

本次不修改 ORM 和本地池持久化契约。建议新增 `backend/app/services/tushare_transport.py` 承载 transport、限流、熔断和 router；`tushare_client.py` 保留 canonical 工厂及领域 helper。

## 3. 交易时段和本地池策略

### 3.1 盘中

现有 `normalize_data_source("auto")` 在工作日 09:15–15:00 选择 `local_pool_realtime`。`LocalPoolMarketSourceAdapter(include_realtime=True)` 调用 `get_market_universe_from_pool_with_realtime()`：

1. 从 MySQL `StockQuote` 读取市值、PE、行业等缓存字段；
2. 从 MySQL `DailyKbar` 批量读取历史 K 线；
3. 使用实时快照覆盖当日价格、开高低、量额并在内存拼接当日临时 K 线；
4. 对已经过日线筛选的最终候选，可按需调用 ai-tool `rt_min`/`rt_k`；
5. 不对 5,500 多只股票逐只调用 ai-tool 实时接口。

实时叠加不改变历史日 K 的权威性；收盘后由正式同步数据回补。盘中 broad snapshot 暂保留可批量的 `realtime_quote`，因为 ai-tool 多代码实时请求尚未验证可靠。

### 3.2 盘后

现有盘后流水线顺序保留：

```text
全量同步/缺口修复
→ diagnose_all(force_refresh=True)
→ recommendation_readiness_from_diagnosis(expected_trade_date)
→ outcome
→ formal recommendation
```

健康闸门检查 `daily_kbar`、`stock_quote`、`index_daily`、`limit_up_board` 是否覆盖最新已收盘交易日且近期无有效缺口。通过后，推荐 payload 必须显式使用 `data_source=local_pool`，而不是 `local_pool_preferred`，从而保证推荐阶段只读 MySQL。未通过则阻止推荐，不临时绕过本地池直连上游。

### 3.3 历史/回测

`as_of_date` 强制走本地只读，不刷新、不读取当前实时行情。Provider 只在预先执行的数据准备/缺口修复阶段使用。

### 3.4 分钟 K 历史窗口与回填决策

短线生产查询不需要把三年 1 分钟数据全部放入热池。不同用途的合理窗口为：

| 用途 | 建议历史窗口 | 当前定位 |
|---|---:|---|
| 最终候选的即时买卖点、短线形态 | 5–20 个交易日 | 当前启动范围 |
| 分时成交量、波动率和时段基线 | 20–60 个交易日 | 生产目标范围 |
| 较长分钟级回放 | 120 个交易日 | 有明确策略需要时再扩展 |
| 多年度分钟回测 | 最近 3 年 | 未来离线研究能力，不是当前上线阻塞项 |

当前执行策略：

1. 只对候选池、持仓和观察列表按需初始化最近 20 个交易日；
2. 之后通过盘后每日增量自然积累，或按明确需求扩展到 60 个交易日；
3. 部署、启动和盘后流水线均不得自动触发全市场三年回填；
4. 本阶段不设计旧分钟数据的清理、归档或冷热分层机制。

按 300 只候选股、`stk_mins` 48 QPM、实测约 1.5 秒/请求估算：若后续把候选回填优化为“每股每 20 个交易日一个区间请求”，20 日约 300 次请求，含写库建议预留 15–30 分钟；60 日约 900 次请求，含写库建议预留 30–60 分钟。现有逐股票、逐交易日链路补 20 日需约 6,000 次请求，至少约 2–3 小时，因此后续需要补数时应走区间请求，不应直接扩大逐日修复循环。

全市场三年约为 `5,530 × 730 × 241 ≈ 9.73 亿` 行。沿用逐日链路约需 4,036,900 次请求，48 QPM 理论下限约 58.4 天，按 1.5 秒/请求串行约 70 天，尚未计 MySQL 写入；即使未来改为每股按 20 个自然日切片，也约需 304,000 次请求，上游理论下限约 4.4–5.3 天，考虑大响应、动态频控、重试及近十亿行写库后现实可能为 1–3 周。因此三年全市场回填明确延期。

## 4. 配置设计

`Settings` 新增/使用以下环境变量，所有敏感默认值为空：

```text
ASHARE_TUSHARE_FASTAPIC_URL
ASHARE_TUSHARE_FASTAPIC_TOKEN
ASHARE_TUSHARE_FASTAPIC_RATE_LIMIT_PER_MINUTE=30
ASHARE_TUSHARE_AI_TOOL_URL
ASHARE_TUSHARE_AI_TOOL_API_KEY
ASHARE_TUSHARE_AI_TOOL_RATE_LIMIT_PER_MINUTE=90
ASHARE_TUSHARE_AI_TOOL_RT_MIN_RATE_LIMIT_PER_MINUTE=72
ASHARE_TUSHARE_AI_TOOL_STK_MINS_RATE_LIMIT_PER_MINUTE=48
ASHARE_TUSHARE_PROVIDER_FAILURE_THRESHOLD=3
ASHARE_TUSHARE_PROVIDER_RECOVERY_SECONDS=60
```

真实值只写 `backend/.env`。旧 `tushare_token` 若暂留兼容入口，也必须为空默认值且新代码不得依赖。

## 5. Transport 契约

### 5.1 类型

```python
@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str
    credential: str
    auth_mode: AuthMode
    rate_limit_per_minute: int
    max_concurrency: int

class ProviderFailure(Exception):
    provider: str
    api_name: str
    category: FailureCategory
    retryable: bool
    fallback_allowed: bool
```

`FailureCategory` 至少区分 `network`、`timeout`、`http_rate_limit`、`http_server`、`non_json`、`business_rate_limit`、`permission`、`invalid_request`、`malformed_response` 和 `configuration`。

### 5.2 请求认证

Fastapic payload 包含 `token`；ai-tool 通过 `X-API-Key` header 认证且 payload 不包含 Fastapic token。Provider 自己负责认证构造。Session 设置 `trust_env=False` 并清空代理。

### 5.3 响应规范化

成功响应必须满足：根对象为 mapping、`code == 0`、`data`/`items` 类型正确；非空 items 时 fields 非空唯一且每行长度匹配；空 items 允许 fields 缺失。动态 API 转为 DataFrame，`fetch_raw` 保持现有严格不可变 envelope。

## 6. Provider 路由策略

```python
AI_TOOL_PRIMARY_APIS = frozenset({"stk_mins", "rt_min", "rt_k", "anns_d"})
```

- 集合内：`ai-tool → Fastapic`，但 Fastapic 分钟权限错误只作为快速失败，不视为可靠备用。
- 其他同步 API：`Fastapic → ai-tool`。
- 缺凭据 Provider 在候选列表中跳过。
- 只有 `fallback_allowed=True` 的失败继续下一个 Provider。
- 合法空 envelope 立即返回。
- 全部失败时只抛安全 provider/category 摘要。
- Router 返回的数据仍须经现有 completeness/OHLC/日期检查后 upsert 到 MySQL；fallback 不改变本地池质量门禁。

## 7. 限流、重试和熔断

### 7.1 限流

使用线程安全滑动窗口或令牌桶。Fastapic 同时使用 `BoundedSemaphore(1)`，默认 30 QPM。ai-tool 使用通用额度，并为 `rt_min`、`stk_mins` 使用更低 API 额度。

### 7.2 重试

网络、超时、429、5xx、非 JSON和业务频控可有限重试；明确权限错误无需在同一 Provider 重试但可 fallback。指数退避加抖动并设上限。

### 7.3 熔断

`closed → open → half_open → closed/open`。连续可切换故障达到阈值后 open；恢复窗口后只允许一个 half-open 探测。合法空结果和调用方参数错误不计入 breaker。

## 8. canonical 客户端与本地同步兼容

`get_pro()` 继续缓存一个 session；`clear_pro_cache()` 清除 Router 及 Provider 状态。

```python
pro = get_pro()
pro.daily(trade_date="20260723")      # 同步层 DataFrame
pro.fetch_raw("daily", params={...}) # strict envelope
```

领域 helper：

- `_fetch_tushare_minute_kbars()` 调用 ai-tool `stk_mins`，输出继续进入现有 `MinuteKbar` bulk upsert；
- 删除“Fastapic 无分钟权限后永久禁用整个进程”的旧逻辑；
- 新增单股票 `fetch_realtime_minute_kbar()`，仅供盘中最终候选实时叠加；
- 新增 `fetch_realtime_k_snapshot()`，明确其非连续分钟序列；
- 日线、报价、辅助数据仍由现有 cron/remediation/MarketSourcePort 写入本地表。

## 9. 脚本和盘后编排收口

- `backend/scripts/tushare_init.py` 只调用 canonical `get_pro()`；
- `fetch_kbars_only.py`、`fill_kbars_batch.py` 改用 canonical 客户端/Settings；
- 盘后同步阶段继续使用 Router 获取上游数据并写 MySQL；
- `_postclose_pipeline_payload()` 将正式推荐数据源设为 `local_pool`；
- `recommendation_readiness_from_diagnosis()` 仍是进入 local-only 推荐前的硬闸门。

## 10. 可观测性与安全

Provider 日志字段：`api_name, provider, attempt, duration_ms, outcome, failure_category, fallback, breaker_state`。本地池运行证据继续记录 `run_data_source_mode`、`market_source_audit` 和 readiness。禁止记录 credential、header、完整 payload 或完整错误响应。

## 11. 测试设计

1. Provider：两种认证、成功/空结果、HTTP/JSON/业务错误、strict envelope。
2. Router：主源选择、fallback、分钟优先、空结果不切换、全失败。
3. 韧性：独立限流、breaker open/half-open/reset、不可重试错误。
4. 本地主池：盘中 auto → `local_pool_realtime`；盘后健康 → `local_pool`；历史 as-of local-only。
5. 盘后门禁：不健康阻止推荐，健康推荐阶段不调用上游。
6. 持久化回归：DailyKbar/MinuteKbar/StockQuote 原有 upsert 和缺口修复不变。
7. 真实 smoke：Fastapic `trade_cal`，ai-tool `stk_mins`/`rt_min`；只输出安全摘要。

## 12. 风险与缓解

| 风险 | 缓解 |
|---|---|
| Fastapic 低速仍返回 500 | 30 QPM、并发 1、短重试、breaker、ai-tool fallback |
| ai-tool `stk_mins` 动态频控 | 48 QPM 起步、识别 40203、至少 60 秒退避 |
| 备用源数据差异污染本地池 | 保留日期、完整性、OHLC、行数和健康门禁 |
| ai-tool 多股票实时非 JSON | broad snapshot 保留批量 realtime_quote；ai-tool 只查最终候选 |
| 盘后重复访问上游 | readiness 通过后显式 `local_pool`，测试断言无网络调用 |
| 本地池缺口被掩盖 | 不健康直接阻止盘后推荐，不临时直连上游 |
| 凭据泄漏 | 仅 `.env`，日志/异常脱敏，完成后安排轮换 |

## 13. 实施顺序

1. 修订盘后分流：健康后正式推荐使用 pure `local_pool`。
2. 更新 Settings 和本地 `.env`。
3. 实现 Provider transport、规范化、限流、breaker 和 Router。
4. 将 `get_pro()` 切换到 Router，保持同步/辅助调用契约。
5. 增加实时分钟 helper，历史分钟继续写 `MinuteKbar`。
6. 收口硬编码脚本。
7. 增加无网络 Router、本地池分流和盘后门禁测试。
8. 运行回归、真实 smoke、旧 Token 搜索、诊断和 diff 检查。
9. 回填评估报告实施记录。
