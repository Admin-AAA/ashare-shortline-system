# 设计文档 · 架构深化重构（architecture-deepening）

> 状态：Design-first，grilling 已收敛（§15 全部拍板）→ 下一步 requirements.md
> 所有MySQL Redis都连接不上，如果需要测试MySQL，你可以考虑mock或者直接放弃测试
> 词汇遵循 `.agents/skills/codebase-design`：module / interface / implementation / depth / deep / shallow / seam / adapter / leverage / locality。

## 0. 验证边界
基于只读静态分析：实读 `skill_loader / strategy_runner / evaluator / shortline_factors` 与 `feature_builder` 调用点 + grep/wc 证据（重复分布、委托壳、行数、import 广度）。细粒度断言（`run_workbench` 精确行区间、`indicator_computer` 与 `market_data` 是否逐字相同、conftest 连生产库、持久化 SQL 方言）标 `【待核实】`，实现前逐一精读补证。纯 in-process 部分本环境可用 `test_feature_builder / test_aggregator` 验证；`production_mysql` 用例跳过标注。所有MySQL Redis都连接不上，如果需要测试MySQL，你可以考虑mock或者直接放弃测试

## 1. 依赖分类图例
`in-process`（纯计算，无替身）/ `local-substitutable`（DB session、文件 → sqlite/临时目录/注入）/ `ports&adapters`（Tushare 等真实外部 → port + in-memory adapter）/ `mock`（不得已，最少量）。

## 2. 目标模块地图
地基（trading_calendar / indicator_computer / board_rules / evaluator）→ 扩展框架（Factor 注册表、Strategy 注册表）→ 推荐运行薄编排（run_pipeline + filter_audit + projection + registry + queue）→ 行情数据（market_source port + market_pool.core/auxiliary + candidates/screening）→ 结果与回测（outcome/simulation + evaluation_metrics + backtest/result）。

## 3. 地基：复制的规则 → 深模块（Candidate 1，in-process）
| 新 deep module | 收拢 | 删除副本 |
|---|---|---|
| `engine/trading_calendar.py` | latest_allowed_bar_date / latest_allowed_quote_date / previous_weekday / normalize_trade_date | market_data、market_pool、data_gap_remediation、data_sources（4 份） |
| `services/indicator_computer.py`（既有→唯一实现） | _rsi/_macd/_macd_status/_ema/_bias/_max_drawdown/_efficiency/_ma/_upper_shadow_count | market_data 平行副本、feature_builder `_bias`、agents/tools `_bias` |
| `engine/board_rules.py` | is_one_word_limit_up / main_board_up_limit / 20cm 识别 | outcome_service、overnight_holding（2 份，签名漂移→统一） |
| `market_pool/history_maturity.py`（local-substitutable） | _load_history_maturity_map / _is_natural_short_history（带 db） | market_pool、data_gap_remediation（2 份） |

删除测试法：删任一副本→逻辑集中到唯一深模块（浅重复，通过）。Wins：locality / leverage / 消除漂移。

## 4. 策略与因子扩展框架（新增硬约束 · 策略模式）
现状：声明式策略已是好插件（技能=目录，`load_skills` 扫目录，`run_strategy` 通用契约解释器 + `evaluate` DSL，加数值策略=丢 YAML 零改代码）；因子是真痛点（`apply_shared_shortline_factors` 6 个 `_apply_X_factor` 硬编码顺序，leader 隐式依赖 sector/capital，新增因子改 4 处 + 手工加 `evaluator.ALLOWED_NAMES`）。

### 4.1 因子框架（in-process）— 返回 dict 契约
`Factor` 协议：`id / output_fields / depends_on / metadata / compute(stock, ctx) -> dict[str, Any]`。
- 驱动按 `depends_on` **拓扑排序**，每个因子返回的字段 dict 按序 merge 回 stock，供下游因子/策略读取——保今天 leader 读 sector/capital 的行为。
- 跨股聚合（sector_stats / sentiment_phase / main_industries）算一次，装进 `ctx` 显式传入。
- 驱动校验「返回 key ⊆ 声明 output_fields」，并把 output_fields **自动注入 `evaluator.ALLOWED_NAMES`**（删手工编辑点）。
- 解释由 metadata 通用生成（删 `_attach_factor_explanations` 硬编码）。
- `FactorRegistry` + `@register_factor`；6 因子各成一个实现，**新增因子 = 注册一个实现，驱动零改**。

### 4.2 策略框架（in-process）— 命令式策略一等公民
形状一（per-stock 打分策略，产出 Signal 喂 aggregator）。本版落地命令式扩展入口。
- `Strategy` 协议：`evaluate(stock, ctx) -> Signal`。
- typed `Signal`（dataclass/TypedDict），字段与今天 `_signal(...)` **逐一对应、零行为变化**（strategy_id/strategy_name/version_id/strategy_independence/weight/phase/score/score_cap/confidence/setup_match/entry_rule/invalid_rule/setup_quality/hard_veto/veto_reason/risk_flags/evidence）。
- `DeclarativeStrategy`：委托现有 `run_strategy(skill, ...)`，**YAML 路径一字不动**。
- `ImperativeStrategy` 基类（模板方法 + 策略）：基类做不变步骤（universe/prefilter 复用 `evaluate` + 契约、`_evidence_score_cap` 诚实性上限、veto 管线、Signal 组装），子类**只覆写 `score(stock, ctx)`** 的 novel 打分逻辑。加命令式策略只写打分，风控/上限与声明式一致（locality 不破）。
- 加载/注册统一：命令式仍是 **skill 目录**（`manifest.json` + `SKILL.md` 供 agent review + 可选 `strategy.yaml` 只写 universe/prefilter），manifest 声明 `impl_class: "module:ClassName"`。`load_skills` 扩展：有 impl_class → 实例化命令式类；否则 → `DeclarativeStrategy`。共用 SkillRegistry / enable-disable / preset / 版本 / agent review / skill_hash。
- `StrategyRegistry` 统一推荐运行与 backtest 预检的策略分发。
- 命令式样例：一个 per-stock 打分最小实现（非 overnight）。
- overnight/backtest（形状二：时间序列回测）不在此列，留 S8。

## 5. 推荐运行编排深化（Candidate 2，local-substitutable，最高 leverage/15 处调用）
`workbench_service`(5480) → 薄编排 + 深模块：`runs/filter_audit`（~1000 行分布助手）、`recommendations/projection`（扩容）、`registry/{skills,presets}`、`runs/queue`+`runs/subprocess`、`runs/persistence`（扩容）。删 3 个 shallow wrapper（@2862/@2866/@3039），编排直调 `runs/skill_gate`；消除与 `candidates/trace` 的分布重复。【待核实】run_workbench 行区间。

## 6. 行情采集 seam（Candidate 5，ports&adapters，先于 3/7）
`market_pool_adapters.py`(85 行纯转发、测试已 monkeypatch)提升为真正 port `market_source/port.py`：只覆盖 `fetch_quotes(scope)` + `fetch_daily_bars(codes, date_range)`；辅助数据不进此 port。生产 `multi_source_adapter`（多源+回退顺序藏在 implementation），测试 `in_memory_adapter`。`market_data`(1751) 非采集职责移出：候选打分→`candidates/screening`，指标→地基，快照→`market_source/snapshot`。落地后推荐运行可 DB-less 对拍。【待核实】源清单与方法粒度，S4 精读微调。

## 7. 行情池拆分（Candidate 3，local-substitutable/sqlite 可测）
`market_pool/core`（报价+日K+readiness）、`market_pool/auxiliary`（描述符驱动：`AuxiliaryDataset` 把 ~15 组 refresh/persist/load 三件套坍缩成通用逻辑 + 15 描述符，内部调 `tushare_client`）、`market_pool/history_maturity`（§3）。新增辅助数据=加一个描述符。

## 8. 结果模拟 vs 指标上报（Candidate 4）
`outcome/simulation`（模拟引擎，in-process，本环境可测）独立；指标汇流既有 `evaluation_metrics.compute_full_metrics`；`outcome_service` 瘦成只读上报。停止 backtest/overnight/outcome 各自重造 metric record。

## 10. 回测家族统一（Candidate 6，Speculative，最后做）
`backtest/result`（trades→指标/样本 唯一深模块），家族成员只喂 trades；命令式回测策略（形状二）在此与 §4.2 协议对齐；指标走 §8。

## 11-12. 横切 & 测试
不改 `models/tables.py` schema（行为不变基线）；evaluator 白名单由因子注册表喂入；测试替换而非叠加（绑定被删 wrapper/旧私有函数的用例改写为新接口，不留双份）；`run_workbench` golden-master 对拍（需 §6 in-memory adapter；持久化若非 sqlite 可移植则退化为纯计算件本地对拍 + 触库部分 CI 带库）。

## 13. 落地顺序（地基优先，每阶段独立 commit）
- S1 地基：trading_calendar / indicator_computer 收口 / board_rules / history_maturity，删全部副本。
- S2 因子框架：Factor 协议 + 返回 dict + 拓扑排序驱动 + output_fields 自动注入白名单 + registry，迁移 6 因子。
- S3 策略框架：Strategy 协议 + typed Signal + DeclarativeStrategy(wrap run_strategy) + ImperativeStrategy 模板方法基类 + 一个命令式样例 + StrategyRegistry + `load_skills` 扩展(impl_class)。in-process/低风险，声明式路径不变，命令式为增量。
- S4 采集 port + candidates/screening 抽出（enables DB-less 对拍）。
- S5 行情池拆分（core / auxiliary 描述符 / history_maturity）。
- S6 结果/指标拆分（outcome/simulation + evaluation_metrics 汇流）。
- S7 推荐运行深化（薄编排 + filter_audit 下沉 + 删 wrapper），golden-master 对拍。
- S8 回测家族统一（含形状二命令式回测策略并入协议）。

## 14. Grilling 议题（已全部收敛，见 §15）
1 因子 compute 契约 · 2 策略框架范围 · 3 port 形状 · 4 历史成熟度落点 · 5 golden-master · 6 filter_audit 边界。

## 15. Grilling 决议记录
1. **因子 compute = 返回 dict**：`compute(stock, ctx) -> dict`，驱动按 depends_on 拓扑序 merge；校验 output_fields 并自动注入 evaluator 白名单；跨股聚合经 ctx 一次算好传入。理由：output_fields 成可信契约、白名单闭环可靠、因子成纯 in-process 单元可本地测。
2. **策略框架 = 命令式一等公民，本版落地**：
   - A 形状一（per-stock Signal）确认。
   - B 引入 typed Signal，字段对齐今天 dict，零行为变化。
   - C `ImperativeStrategy` 模板方法基类，子类只覆写 `score()`。
   - D 命令式 = skill 目录 + manifest `impl_class`，共用全套加载/注册/开关/preset/版本/agent review（不走纯代码注册表）。
   - E S3 扩容但仍 in-process/低风险；overnight（形状二）留 S8；样例挑 per-stock 最小实现。
3. **market_source port** 只覆盖 fetch_quotes + fetch_daily_bars；aux 不进此 port（走 §7 描述符，内部调 tushare_client）。生产一个 multi_source_adapter（多源+回退藏 implementation），测试 in_memory_adapter。方法粒度 S4 精读后微调【待核实】。
4. **历史成熟度** → `services/market_pool/history_maturity.py`（local-substitutable）。
5. **golden-master** 投入 in-memory adapter + sqlite，S7 run_workbench 端到端对拍序列化输出；风险：持久化若含 MySQL 方言 upsert/JSON 则 sqlite 不可移植【待核实 S4/S7】，兜底=纯计算件本地单元 golden-master + 触库部分 CI 带库。

**【S7.1 已核实，结论：不迁移 sqlite】** 用一次性探针脚本实测（非猜测）：
- `sqlalchemy.dialects.mysql.LONGTEXT`（`RecommendationRun.filter_audit_json`、`BacktestRun.result_json` 等未带 `.with_variant(Text(), "sqlite")` 的列）在 sqlite 引擎上 `create_all()` 直接抛 `CompileError: ... can't render element of type LONGTEXT`。
- `sqlalchemy.dialects.mysql.insert(...).on_duplicate_key_update(...)`（`market_pool/core.py`、`market_pool/auxiliary.py`、`data_gap_remediation_service.py`、`overnight_holding_service.py`、`workbench_service.py` 等 12+ 处）在 sqlite 引擎上执行抛 `UnsupportedCompilationError: ... can't render element of type OnDuplicateClause`。
- sqlite 有自己的 `sqlalchemy.dialects.sqlite.insert(...).on_conflict_do_update(...)`，探针验证语义等价可用，但要让 12+ 处 upsert 站点在 MySQL/sqlite 间切换，需要抽一层数据库无关的 upsert helper（按方言分发），且要把所有 `LONGTEXT`/`MEDIUMTEXT` 列统一补 `.with_variant(Text(), "sqlite")`（多数列尚未补，只有 `Recommendation.feature_snapshot_json`/`AgentDecisionLog.review_json` 两处做过）。
- 本次重构目标是"行为不变"（需求 1），不是新增"多数据库支持"能力；为了一个不改变对外行为、只服务于测试基线的 golden-master，去改 12+ 处生产持久化代码（哪怕只加 `.with_variant`）本身就带来行为漂移风险，与需求 1.2（不改表结构）的谨慎精神相悖，成本/风险明显超过收益。
- 决定：**S7 golden-master 采用需求 10.4 / design §12 的退化方案**——纯计算件本地对拍：用 `market_source.in_memory_adapter` 注入行情，`db` 参数传入一个真实 sqlite `Session`（`create_all` 时只建 golden-master 实际会写到的表，跳过含 MySQL-only 类型的列所在的持久化调用，或对这些调用打 monkeypatch 短路），对拍范围收窄到"确定性计算字段"（候选集/分数/phase/veto/setup/risk_flags/evidence/filter-audit 分布），不对拍真实持久化落库结果；触库部分（`_persist_market`/`_upsert_stock_rows`/`_upsert_daily_bar_rows`/aux upsert 等 MySQL 专属路径）标 `production_mysql` 交 CI 带库验证。
6. **filter_audit**：管线只 append trace（复用 candidates/trace 累积器），末尾 `build_filter_audit(trace,...)` 出最终 audit/分布，全部下沉 `runs/filter_audit`，编排不留残件。
