# 双市场数据 Provider 接入需求文档

## Introduction

本文档定义新候选 Fastapic 与 ai-tool 两套数据源的生产接入要求。系统继续以本地 MySQL 行情池为查询主数据源；新上游只用于同步、缺口修复、盘中实时叠加和跨服务容灾。目标是在不改变现有本地池及业务调用契约的前提下，替换即将过期的旧 Fastapic Token，并用 ai-tool 提供最近三年历史分钟的可访问能力和实时分钟能力；当前生产阶段不立即执行全市场三年分钟回填。

## Scope

### In Scope

1. 保留 `StockQuote`、`DailyKbar`、`MinuteKbar` 等现有 MySQL 同步、幂等写入、健康诊断和缺口修复链路。
2. 盘中使用本地历史/基础池叠加实时查询；盘后同步并通过健康闸门后直接使用本地池，不在推荐查询阶段访问上游。
3. 新 Fastapic 作为日线、基础、财务、概念等宽覆盖同步接口的低并发主源。
4. ai-tool 作为 `stk_mins`、`rt_min`、`rt_k`、`anns_d` 主源及其他同步接口的备用源。
5. 统一动态 Tushare API、严格 `fetch_raw` envelope、独立限流、重试、熔断和故障切换。
6. 凭据环境变量化，移除旧 Token 的运行时和脚本硬编码依赖。

### Non-Goals

1. 不把上游 Provider 改成替代 MySQL 的在线查询主库。
2. 不承诺全市场 5,500 多只股票每分钟实时同步。
3. 不在当前上线阶段自动或立即回填全市场三年 1 分钟数据。
4. 不在本次接入中设计旧分钟数据的清理、归档或冷热分层机制。
5. 不把 Fastapic 方案 1/2 当作两个独立故障域。
6. 不新增第三方依赖或改变现有数据库 schema。
7. 不把合法空结果自动当作 Provider 故障。

## Requirements

### Requirement 1: 本地 MySQL 主池与交易时段分流

**User Story:** 作为策略调用方，我希望正常情况下始终使用本地数据池，从而获得稳定低延迟查询并减少对上游可用性的依赖。

#### Acceptance Criteria

1. `StockQuote`、`DailyKbar`、`MinuteKbar` SHALL 继续作为行情查询和策略计算的本地权威池；本次接入不得绕过或替换现有持久化链路。
2. 盘中自动模式 SHALL 使用 `local_pool_realtime`：本地历史 K 线/基础字段加实时查询结果，仅把实时部分作为当次运行的增量叠加。
3. 盘后流水线 SHALL 先执行现有同步与 `recommendation_readiness_from_diagnosis()` 健康闸门；健康通过后正式推荐 SHALL 使用 `local_pool` 纯本地只读模式。
4. 若盘后本地池未覆盖最新已收盘交易日、近期有关键缺口或健康检查失败，系统 SHALL 阻止推荐并报告 blocker，不得静默直连上游生成结果。
5. 历史日期/回测 SHALL 保持 local-only/as-of 语义，不得用当前实时行情覆盖历史数据。
6. Provider Router SHALL 只服务同步、缺口修复、辅助数据抓取和盘中实时查询边界；日常盘后查询不得直接依赖 Provider。

### Requirement 2: 安全配置与凭据轮换

**User Story:** 作为维护者，我希望新凭据只从安全配置读取，从而让旧 Token 完全退出运行时且不会泄漏到 Git 或日志。

#### Acceptance Criteria

1. 配置 SHALL 提供 Fastapic URL/Token、ai-tool URL/API Key、各 Provider 和分钟 API 的限速、熔断参数。
2. 所有凭据配置默认值 SHALL 为空，不得在 Git 跟踪代码、Spec、报告、测试 fixture 或日志中保存真实值。
3. 运行时 SHALL 从 `backend/.env`/环境变量读取新凭据；该文件必须保持 Git 忽略。
4. 若某 Provider 凭据缺失，系统 SHALL 跳过该 Provider；若同步调用时没有任何可用 Provider，`get_pro()` SHALL 给出不含凭据的明确配置错误。
5. 旧 Token SHALL 从客户端、配置默认值和维护脚本中移除，精确搜索命中为零。

### Requirement 3: Provider 认证与响应规范化

**User Story:** 作为同步和实时查询调用方，我希望两个不同认证协议的上游呈现同一个结果契约。

#### Acceptance Criteria

1. Fastapic 请求 SHALL 使用 Tushare JSON payload 中的 `token`；ai-tool 请求 SHALL 使用 `X-API-Key` header，且 payload 不伪造 Fastapic token。
2. 两个 Provider SHALL 使用禁用系统代理继承的独立 `requests.Session`，并具有有限连接/读取超时。
3. HTTP 非成功、非 JSON、畸形 payload 和非零业务 `code` SHALL 转为不含敏感信息的类型化错误。
4. 动态 `pro.<api_name>(**params)` SHALL 继续返回 `pandas.DataFrame`。
5. `fetch_raw()` SHALL 保持现有严格 envelope：非空 items 必须有唯一 fields，行长度必须匹配；空 items 可无 fields；错误消息和凭据不得进入 envelope。

### Requirement 4: 显式 API 路由和故障切换

**User Story:** 作为数据同步开发者，我希望无需修改现有同步调用方即可按数据域使用最合适的上游，并在服务故障时自动切换。

#### Acceptance Criteria

1. `stk_mins`、`rt_min`、`rt_k`、`anns_d` SHALL 以 ai-tool 为主源。
2. 其他 API SHALL 以 Fastapic 为主源，并在其不可用时尝试 ai-tool。
3. 网络错误、超时、HTTP 429/5xx、非 JSON、权限错误和频控业务错误 SHALL 被视为可切换故障。
4. 参数错误和其他明确不可重试业务错误 SHALL 直接返回，不盲目切换。
5. 合法空 DataFrame/envelope SHALL 默认直接返回，不触发 fallback。
6. fallback 全部失败时 SHALL 抛出包含 API、尝试过的 Provider 和稳定错误类别的异常，但不得包含 URL 查询凭据、请求头或 Token。
7. fallback 成功后的数据 SHALL 继续经过现有质量检查和 MySQL 幂等写入，不得直接跳过本地池提供给盘后推荐。

### Requirement 5: 独立限流、重试与熔断

**User Story:** 作为运维人员，我希望一个不稳定上游不会拖慢同步任务或耗尽整个系统。

#### Acceptance Criteria

1. Fastapic 与 ai-tool SHALL 使用独立的线程安全限流器，不再共享单一全局额度。
2. Fastapic 默认 SHALL 限制为 30 QPM、并发 1；ai-tool 通用接口默认 90 QPM，`rt_min` 默认 72 QPM，`stk_mins` 默认 48 QPM。
3. 重试 SHALL 有有限次数、指数退避和抖动；业务频控 SHALL 支持至少 60 秒退避提示。
4. Provider 连续达到阈值的可切换故障后 SHALL 打开短时 circuit breaker；半开探测成功后恢复。
5. breaker 打开时 SHALL 直接尝试下一个 Provider，不等待主源网络超时。
6. 日志 SHALL 记录 API、Provider、耗时、attempt、错误类别、fallback/breaker 状态，不记录凭据或完整 payload。

### Requirement 6: 分钟行情同步与盘中查询

**User Story:** 作为短线策略调用方，我希望历史分钟先同步到本地，盘中只按候选查询实时分钟，从而兼顾稳定性和时效。

#### Acceptance Criteria

1. 现有历史分钟 helper SHALL 通过统一 `get_pro().stk_mins(...)` 路由到 ai-tool，不因 Fastapic 无权限而全进程永久禁用。
2. 历史分钟 SHALL 继续通过现有 `MinuteKbar` bulk upsert/缺口修复链落入 MySQL，查询与回测优先使用本地分钟池。
3. 系统 SHALL 提供单股票 `rt_min` helper，支持 1/5/15/30/60 分钟，用于盘中最终候选的实时叠加，不承担全市场扫描。
4. 系统 SHALL 提供单股票 `rt_k` helper，并明确其为日内聚合快照而非连续分钟序列。
5. 历史分钟调用 SHALL 保持支持最近三年的日期参数、HTTP 200 业务错误检查和 8,000 行上限认知；该能力不等于当前执行三年全量回填。
6. 当前生产初始化 SHALL 只针对候选池、持仓和观察列表按需补最近 20 个交易日，并通过每日增量或显式需求逐步扩展到 60 个交易日。
7. 最近三年历史范围 SHALL 只作为未来离线研究/回测的可访问能力保留；部署、启动和盘后流水线不得自动触发全市场三年回填，且三年回填不属于当前 Definition of Done。
8. 多股票实时和全市场分钟 SHALL 不被标记为已保证能力。
9. 收盘后 SHALL 使用 `stk_mins` 回补/校验当日 `MinuteKbar`；健康的本地分钟池 SHALL 可供后续策略直接读取。

### Requirement 7: 脚本与调用方收口

**User Story:** 作为维护者，我希望所有同步及维护入口共享同一套 Provider 策略，避免脚本继续使用过期凭据。

#### Acceptance Criteria

1. `backend/scripts/tushare_init.py` SHALL 委托 canonical `get_pro()`，不维护第二套 Token/Session。
2. 硬编码旧 Token 的回填脚本 SHALL 改为 canonical 客户端或 Settings Provider 配置。
3. 现有同步、`MarketSourcePort`、健康诊断和缺口修复调用方式 SHALL 保持兼容。
4. `LocalPoolMarketSourceAdapter`、MySQL ORM 和本地 upsert 契约 SHALL 保持不变，除非为盘中实时 seam 做最小适配。
5. 不得因重构修改与数据源无关的业务选择、评分或持久化语义。

### Requirement 8: 验证与可运维性

**User Story:** 作为发布负责人，我希望通过无网络测试和最小真实烟雾确认本地主池分流、路由、容灾和新凭据可用。

#### Acceptance Criteria

1. 测试 SHALL 覆盖主源路由、Fastapic 故障切 ai-tool、合法空结果不切换、全失败、熔断和严格 raw envelope。
2. 测试 SHALL 覆盖两种认证方式且断言错误/日志不含凭据。
3. 测试 SHALL 证明盘中 auto 选择 `local_pool_realtime`，盘后健康流水线选择 `local_pool`，且盘后不执行上游查询。
4. 测试 SHALL 证明盘后健康不通过时阻止推荐，而不是临时从上游生成结果。
5. 现有本地池、分钟、公告、市场数据、健康和盘后流水线目标测试 SHALL 通过。
6. 修改文件 SHALL 通过 Python compile/diagnostics、目标 pytest 和 `git diff --check`。
7. 最小真实烟雾 SHALL 验证 Fastapic 通用接口、ai-tool `stk_mins`、ai-tool `rt_min`；不在输出中显示凭据。
8. 最终报告 SHALL 记录实现文件、验证结果、实际 fallback 行为和仍未验证的 SLA 风险。

## Definition of Done

1. MySQL 仍是行情查询主池；盘中为本地池+实时叠加，盘后健康后为纯本地读取。
2. 旧 Token 不再存在于 Git 跟踪运行时代码和脚本。
3. 新 Fastapic 与 ai-tool 均可通过同步/实时查询边界使用，路由和 fallback 符合需求。
4. 现有 DataFrame 动态 API 与严格 `fetch_raw` 契约保持兼容。
5. 历史分钟保持可按需写入 `MinuteKbar`，当前生产窗口为候选/持仓/观察列表 20→60 个交易日；三年全市场回填不属于当前完成标准。
6. Provider 独立限流、有限重试和熔断已实现并有测试证据。
7. 凭据只保存在 Git 忽略配置中，日志和报告无完整凭据。
8. 目标测试、诊断、真实 smoke 和报告更新完成。
