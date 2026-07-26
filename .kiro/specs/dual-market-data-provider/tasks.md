# 双市场数据 Provider 接入实施任务

> 本地 MySQL 始终是查询主池。真实凭据只允许写入 Git 忽略的 `backend/.env`；任务、测试输出、报告和日志不得包含完整凭据。

## S0 — 本地主池与时段分流固化

- [x] 0.1 固化盘中查询路径
  - 保持 auto 在工作日 09:15–15:00 选择 `local_pool_realtime`。
  - 保持本地 StockQuote/DailyKbar 为基础，实时数据只做当次叠加。
  - _需求：1.1, 1.2, 1.5_

- [x] 0.2 固化盘后 local-only 路径
  - 保持同步 → diagnose/readiness 健康闸门。
  - 健康通过后的正式推荐显式使用 `local_pool`，不再使用 `local_pool_preferred`。
  - 不健康时阻止推荐，不临时直连上游。
  - _需求：1.3, 1.4, 1.6_

## S1 — 配置与安全基线

- [x] 1.1 扩展 Settings
  - 新增 Fastapic/ai-tool URL、凭据、Provider/API 限速和 breaker 参数。
  - 敏感默认值设为空，保留清晰的缺配置行为。
  - _需求：2, 5_

- [x] 1.2 迁移本地凭据并建立泄漏基线
  - 从用户提供的本地方案文本提取两套新凭据，仅写入被忽略的 `backend/.env`。
  - 记录旧 Token 精确搜索命中，后续必须清零 Git 跟踪运行时文件。
  - _需求：2_

## S2 — Provider Transport 与 Router

- [x] 2.1 实现严格响应模型和安全错误
  - 规范化 HTTP、JSON、业务 code、fields/items 和行长度。
  - 保持非空/空 `TushareRawEnvelope` 契约。
  - _需求：3_

- [x] 2.2 实现两个 Provider
  - Fastapic 使用 JSON token；ai-tool 使用 `X-API-Key`。
  - 独立 Session、超时、禁用环境代理和安全日志。
  - _需求：3_

- [x] 2.3 实现独立限流、重试和熔断
  - Fastapic 默认 30 QPM/并发 1。
  - ai-tool 通用、`rt_min`、`stk_mins` 使用独立额度。
  - 实现有限退避与 closed/open/half-open breaker。
  - _需求：5_

- [x] 2.4 实现 API 路由和 fallback
  - 分钟/公告 ai-tool 优先，其余同步 API Fastapic 优先。
  - 只对可切换故障 fallback；合法空结果不切换。
  - fallback 数据仍经过本地质量门禁和 MySQL upsert。
  - _需求：4_

## S3 — canonical 客户端、分钟能力与 MySQL 同步

- [x] 3.1 将 `get_pro()` 切换到 Router
  - 保持动态 DataFrame API、`fetch_raw()` 和 `clear_pro_cache()`。
  - 无可用 Provider 时返回安全配置错误。
  - _需求：2–4_

- [x] 3.2 修复历史分钟同步
  - 删除 Fastapic 权限错误导致的全进程永久跳过状态。
  - 让 `stk_mins` 默认通过 ai-tool，保留现有 `MinuteKbar` bulk upsert/缺口修复。
  - _需求：6.1, 6.2, 6.5, 6.7_

- [x] 3.3 增加盘中实时分钟和快照 helper
  - 增加单股票 `rt_min` helper，验证 1/5/15/30/60 分钟。
  - 增加 `rt_k` helper并明确其快照语义。
  - 只供最终候选按需调用，不做全市场逐股扫描。
  - _需求：6.3, 6.4, 6.6_

- [x] 3.4 保持本地持久化契约
  - 验证日线/报价/分钟同步仍写入 DailyKbar/StockQuote/MinuteKbar。
  - 不修改 ORM schema、自然键和既有幂等 upsert 语义。
  - _需求：1.1, 4.7, 7.3, 7.4_

- [x] 3.5 固化当前分钟历史窗口
  - 当前只对候选池、持仓和观察列表按需初始化最近 20 个交易日。
  - 通过每日增量自然积累或按需扩展到 60 个交易日。
  - 保留最近三年接口与分片访问能力，但不自动触发全市场三年回填。
  - _需求：6.5–6.9_

## Deferred — 非当前范围

> 以下事项不属于本次接入的 Definition of Done，不会由部署、启动或盘后任务自动执行。

- [ ] D1 执行全市场最近三年 1 分钟数据回填
  - 仅在未来离线研究/回测确有需求，且完成写入容量与长任务评估后单独立项。

- [ ] D2 设计旧分钟数据清理、归档或冷热分层机制
  - 用户当前明确暂不评估；不影响现有 Provider 接入与 20→60 日生产窗口。

## S4 — 维护脚本收口与旧凭据清理

- [x] 4.1 收口 `tushare_init.py`
  - 委托 canonical `get_pro()`，删除第二套 Session/Token。
  - _需求：7.1_

- [x] 4.2 收口硬编码回填脚本
  - 修改 `fetch_kbars_only.py`、`fill_kbars_batch.py` 使用 canonical 客户端或 Settings。
  - 保持原有参数、批处理和写本地 MySQL 行为。
  - _需求：7.2, 7.5_

- [x] 4.3 清除旧 Token 运行时依赖
  - 精确搜索配置、客户端和脚本，确认旧 Token 命中为零。
  - 确认新凭据未进入 Git 跟踪 diff。
  - _需求：2.2–2.5_

## S5 — 测试与验证

- [x] 5.1 执行无网络 Provider/Router 检查
  - 覆盖认证、主源、fallback、空结果、全失败、权限/频控、strict raw envelope。
  - 覆盖 breaker 和错误不泄密。
  - _需求：8.1, 8.2_

- [x] 5.2 验证本地主池和时段分流
  - 盘中 auto 选择 local_pool_realtime。
  - 盘后健康后选择 local_pool 且推荐阶段无上游调用。
  - 不健康时阻止推荐；历史 as-of 保持 local-only。
  - _需求：8.3, 8.4_

- [x] 5.3 运行目标回归与静态检查
  - 运行新路由测试及本地池、分钟、公告、市场数据、健康、盘后流水线测试。
  - 运行 diagnostics/compile、`git diff --check`。
  - _需求：8.5, 8.6_

- [x] 5.4 执行最小真实 smoke
  - Fastapic 调用通用只读接口。
  - ai-tool 调用单日 `stk_mins` 和单股票 `rt_min`。
  - 输出只包含成功状态、行数、字段和延迟。
  - _需求：8.7_

- [x] 5.5 更新评估报告实施记录
  - 记录本地主池分层、实现文件、测试/smoke、限速配置和剩余风险。
  - _需求：8.8_

## Definition of Done Checklist

- [x] MySQL 仍是查询主池，盘中实时叠加、盘后健康纯本地。
- [x] 旧 Token 已退出全部 Git 跟踪运行时代码和脚本。
- [x] 两套新 Provider 经同步/实时查询边界可用。
- [x] 历史分钟进入 MinuteKbar，实时分钟只按候选查询。
- [x] DataFrame 和 raw envelope 兼容性通过测试。
- [x] 限流、退避、熔断和日志脱敏有测试证据。
- [x] 真实 smoke 通过且没有输出凭据。
- [x] 报告包含最终实施和验证结论。
