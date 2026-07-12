# 实施计划 · 架构深化重构（architecture-deepening）

> 每个阶段（S1–S8）独立成 1 个或多个主题 commit；新增文件一律 `git add`（需求 11）。
> 触库用例本环境跳过并标注，交 CI 带库（需求 1.3 / 10.3）。行为不变以 pytest 对拍验证（需求 1）。

## S1 · 地基：复制规则收口为深模块（in-process）
- [x] 1.1 新建 `engine/trading_calendar.py`（latest_allowed_bar_date / latest_allowed_quote_date / previous_weekday / normalize_trade_date），补 pytest；把 market_data、market_pool、data_gap_remediation、data_sources 4 处调用改为 import，删本地副本
  - _需求 2.1, 2.5, 1.1_
- [x] 1.2 将指标数学收口到既有 `indicator_computer.py` 为唯一实现；删 market_data 平行副本、feature_builder `_bias`、agents/tools `_bias`，改 import；补/迁 pytest
  - _需求 2.2, 2.5_
- [x] 1.3 新建 `engine/board_rules.py`（is_one_word_limit_up / main_board_up_limit / 20cm 识别），统一 outcome_service 与 overnight_holding 的漂移签名为单一定义，删副本，补 pytest
  - _需求 2.3, 2.5_
- [x] 1.4 新建 `services/market_pool/history_maturity.py`（_load_history_maturity_map / _is_natural_short_history，带 db，sqlite 可测）；迁移 market_pool、data_gap_remediation，删副本，补 pytest
  - _需求 2.4, 2.5_

## S2 · 因子框架（in-process）
- [x] 2.1 定义 `Factor` 协议（id/output_fields/depends_on/metadata/compute→dict）+ `FactorRegistry` + `@register_factor` + 拓扑排序驱动 + 「返回 key ⊆ output_fields」契约校验 + 循环依赖启动报错；补 pytest
  - _需求 3.1, 3.3, 3.4_
- [x] 2.2 实现 output_fields 自动注入 `evaluator.ALLOWED_NAMES` 的闭环；补 pytest 断言注入生效
  - _需求 3.2_
- [x] 2.3 将现有 6 因子迁移为 Factor 实现（返回 dict、声明 depends_on/output_fields/metadata），跨股聚合经 ctx 一次算好传入；用驱动替换 `apply_shared_shortline_factors`；对相同输入做特征值等价对拍
  - _需求 3.5, 1.1_
- [x] 2.4 由 metadata 通用生成因子解释，删除 `_attach_factor_explanations` 硬编码
  - _需求 3.1_

## S3 · 策略框架：命令式策略一等公民（in-process）
- [x] 3.1 定义 typed `Signal`（字段逐一对齐现 `_signal(...)`），`run_strategy` 改为构造 Signal；对 aggregator 输入做零变化对拍
  - _需求 4.4, 1.1_
- [x] 3.2 定义 `Strategy` 协议 + `DeclarativeStrategy`（委托 run_strategy）+ `StrategyRegistry`；run pipeline 与 backtest 预检改为经 registry 取策略实例
  - _需求 4.4_
- [x] 3.3 实现 `ImperativeStrategy` 模板方法基类（universe/prefilter 校验、evidence 上限、veto 管线、Signal 组装；暴露 `score(stock, ctx)` 钩子）；补 pytest
  - _需求 4.3_
- [x] 3.4 扩展 `load_skills` 识别 manifest `impl_class`：有则实例化命令式类，否则回退 DeclarativeStrategy；确认共用 SkillRegistry/enable-disable/preset/版本/agent review/skill_hash
  - _需求 4.1, 4.2, 4.5_
- [x] 3.5 新增一个 per-stock 命令式样例策略（skill 目录 + 类）；补端到端分发测试（被启用后 run pipeline 正常产出）
  - _需求 4.6_

## S4 · 行情采集 port（ports & adapters；enables DB-less 对拍）
- [x] 4.1 精读 market_data 采集段，确认源清单与方法粒度；定义 `market_source/port.py`（fetch_quotes / fetch_daily_bars）
  - _需求 8.1, 8.2_
- [x] 4.2 实现 `multi_source_adapter`（迁入多源与回退顺序，藏在 implementation）与 `in_memory_adapter`；`market_pool_adapters` 指向 port
  - _需求 8.1, 8.2_
- [-] 4.3 抽出 `candidates/screening`（候选召回/打分）与 `market_source/snapshot`（快照），移出 market_data 采集职责
  - _需求 8.3_
- [ ] 4.4 改造 `test_market_pool` 经 port 注入 `in_memory_adapter`，替换对内部私有函数的 monkeypatch，不留双份覆盖
  - _需求 8.1, 10.2_

## S5 · 行情池拆分（local-substitutable / sqlite 可测）
- [ ] 5.1 抽出 `market_pool/core`（报价+日K+readiness），核心池 interface 仅暴露 universe/snapshot/readiness
  - _需求 5.2_
- [ ] 5.2 定义 `AuxiliaryDataset` 描述符 + 通用 refresh/persist/load，将 ~15 组三件套迁为描述符（内部调 tushare_client）；对相同输入做结果一致对拍
  - _需求 5.1, 5.3, 1.1_

## S6 · 结果模拟与指标分离
- [ ] 6.1 抽出 `outcome/simulation`（模拟引擎，in-process），补本地 pytest 对拍
  - _需求 7.1, 1.1_
- [ ] 6.2 将 outcome/backtest/overnight 的指标统一汇流 `evaluation_metrics`，删除各自重造的 metric record
  - _需求 7.2, 2.5_

## S7 · 推荐运行编排深化（最高 leverage）
- [ ] 7.1 精读 `run_workbench` 与持久化 SQL，判定 sqlite 可移植性，决定 golden-master 端到端对拍 vs 退化方案
  - _需求 1.1, 10.4_
- [ ] 7.2 搭建 golden-master 夹具（in-memory adapter + sqlite；或纯计算件），锁定重构前基线（仅确定性字段）
  - _需求 1.1, 1.5_
- [ ] 7.3 下沉 filter-audit 到 `runs/filter_audit`（trace 累积器 + `build_filter_audit`），管线仅 append trace
  - _需求 6.2_
- [ ] 7.4 序列化下沉 `recommendations/projection`；skill/preset CRUD → `registry/*`；queue/subprocess/persistence 归位到 `runs/*`
  - _需求 6.1_
- [ ] 7.5 删除 `_resolve_prefilter_target_count`/`_resolve_skill_review_limit`/`_build_skill_rule_gate` 三个 shallow wrapper，编排直调 `runs/skill_gate`，run_workbench 收成薄编排
  - _需求 6.1_
- [ ] 7.6 跑 golden-master 对拍通过；校验 `app/api/*` 等 15 处调用点行为不变
  - _需求 1.1, 6.3_

## S8 · 回测家族统一（Speculative）
- [ ] 8.1 抽出 `backtest/result` 单一深模块（trades→指标/样本），backtest/overnight/stock 家族成员改为只喂 trades，指标走 evaluation_metrics
  - _需求 9.1, 7.2_
- [ ] 8.2 将形状二命令式回测策略对齐需求 4 的 Strategy 协议方向
  - _需求 9.2_
