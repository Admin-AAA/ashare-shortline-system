# 需求文档 · 架构深化重构（architecture-deepening）

## 简介
本 spec 对 ashare-shortline-system 后端做架构深化：把复制的规则收口为深模块、拆解超大混职责模块、并用策略模式让**命令式（Python）策略**与**因子**开放扩展，同时不改变对外行为。单测沿用项目现有 pytest。验证边界：无本地 MySQL/Redis/外网，纯 in-process 项本环境验证，触库项标注跳过并交 CI 带库。设计见 design.md。

## 需求

### 需求 1 · 行为不变（重构基线）
**用户故事**：作为系统维护者，我希望重构后在相同输入下产出与重构前一致的结果，以便重构不引入回归。
**验收标准**
1. WHEN 以相同输入（技能集/行情快照/参数）运行 run_workbench，THEN 序列化推荐输出的确定性字段 SHALL 与重构前逐字段一致（golden-master，范围见第 5 条）。
2. THE 系统 SHALL 不修改 `models/tables.py` 的表结构。
3. IF 某校验需真实 DB 而本环境不可运行，THEN 该项 SHALL 标注跳过并交 CI 带库，禁止改连接配置或硬连别的地址绕过。
4. WHEN 每个落地阶段（S1–S8）完成，THEN 相关既有测试 SHALL 通过，或因测试的是被拆解模块的旧接口而被有意替换为新接口测试。
5. WHEN 执行 golden-master 对拍，THEN 对拍 SHALL 覆盖确定性字段（候选集/分数/phase/veto/setup/risk_flags/evidence/filter-audit 分布等），且 SHALL 排除非确定字段（run_id、创建/完成时间戳、运行时长、agent review 的 LLM 自由文本）。

### 需求 2 · 复制规则收口为深模块
**用户故事**：作为维护者，我希望交易日历/指标数学/涨跌停/历史成熟度各只有一处实现，以便一处可改、消除漂移。
**验收标准**
1. WHEN 全仓搜索 `_latest_allowed_bar_date`/`_previous_weekday`/`_latest_allowed_quote_date` 的定义，THEN SHALL 只存在于 `engine/trading_calendar.py`。
2. THE 指标数学（_rsi/_macd/_macd_status/_ema/_bias/_max_drawdown/_efficiency/_ma 等）SHALL 只存在于 `indicator_computer.py`。
3. THE 涨跌停规则 SHALL 只存在于 `engine/board_rules.py`，且原两处漂移签名统一为单一定义。
4. THE 历史成熟度（_load_history_maturity_map/_is_natural_short_history）SHALL 只存在于 `services/market_pool/history_maturity.py`。
5. WHEN 任一调用点需要上述规则，THEN SHALL 通过 import 单一深模块获得，代码中 SHALL 不存在本地副本。

### 需求 3 · 因子开放扩展（注册表 + 依赖排序）
**用户故事**：作为因子作者，我希望新增一个因子只需注册一个实现，以便扩展零改驱动。
**验收标准**
1. WHEN 定义并注册一个新 Factor（id/output_fields/depends_on/metadata/compute），THEN 驱动 SHALL 自动纳入计算，无需改动驱动、evaluator 白名单、解释生成三处。
2. WHEN 因子声明 output_fields，THEN 这些字段 SHALL 自动注入 `evaluator.ALLOWED_NAMES`。
3. IF 因子 compute 返回超出 output_fields 的字段，THEN 驱动 SHALL 抛出契约错误。
4. WHEN 因子间存在依赖，THEN 驱动 SHALL 按 depends_on 拓扑序执行；IF 存在循环依赖，THEN SHALL 在启动时报错。
5. WHEN 现有 6 因子迁移完成，THEN 相同输入下各 output 字段值 SHALL 与迁移前一致。

### 需求 4 · 命令式策略开放扩展（策略模式，形状一）
**用户故事**：作为策略作者，我希望用 Python 写复杂选股策略并像声明式技能一样被系统管理，以便频繁新增命令式策略。
**验收标准**
1. WHEN 新建 skill 目录含 `manifest.json`（声明 `impl_class`）+ `SKILL.md`，THEN `load_skills` SHALL 实例化该命令式策略类。
2. IF manifest 未声明 impl_class，THEN SHALL 回退为 `DeclarativeStrategy`，且声明式 YAML 路径行为逐字段不变。
3. WHEN 命令式策略类继承 `ImperativeStrategy` 且仅实现 `score(stock, ctx)`，THEN 基类 SHALL 提供 universe/prefilter 校验、evidence 上限、veto 管线、Signal 组装。
4. WHEN 命令式或声明式策略产出结果，THEN 二者 SHALL 返回同一 typed `Signal` 契约，aggregator SHALL 无需区分来源。
5. THE 命令式策略 SHALL 与声明式共用 enable/disable、preset 选择、版本、agent review、skill_hash 机制。
6. WHEN 提供的 per-stock 命令式样例策略被启用，THEN run pipeline SHALL 正常分发并产出推荐。

### 需求 5 · 行情池拆分（核心 vs 描述符驱动辅助）
**用户故事**：作为维护者，我希望核心报价池与辅助数据集分离，新增辅助数据只加描述符。
**验收标准**
1. WHEN 新增一种辅助数据集，THEN SHALL 只需新增一个 `AuxiliaryDataset` 描述符，SHALL NOT 复制 refresh/persist/load 三件套。
2. THE 核心池 module 对外 SHALL 只暴露 universe/snapshot/readiness。
3. WHEN 相同输入，THEN 辅助数据 refresh/persist/load 结果 SHALL 与拆分前一致。

### 需求 6 · 推荐运行编排深化
**用户故事**：作为维护者，我希望 run_workbench 是薄编排，审计/序列化/CRUD 各自成深模块。
**验收标准**
1. THE `_resolve_prefilter_target_count`/`_resolve_skill_review_limit`/`_build_skill_rule_gate` 三个 shallow wrapper SHALL 被删除，编排直调 `runs/skill_gate`。
2. THE filter-audit 逻辑（分布助手）SHALL 全部位于 `runs/filter_audit`，管线仅向 trace 累积器 append。
3. WHEN 通过 `app/api/*` 等 15 处调用点访问，THEN 其可见行为 SHALL 不变。

### 需求 7 · 结果模拟与指标分离
**验收标准**
1. THE 交易结果模拟 SHALL 独立为 `outcome/simulation`（in-process 可测）。
2. THE 所有指标 SHALL 经 `evaluation_metrics` 计算；outcome/backtest/overnight SHALL NOT 各自重造 metric record。

### 需求 8 · 行情采集 port（ports & adapters）
**验收标准**
1. WHEN 测试需要替身行情源，THEN SHALL 通过 `market_source` port 注入 `in_memory_adapter`，SHALL NOT monkeypatch 内部私有函数。
2. THE 生产多源与回退顺序 SHALL 隐藏在 `multi_source_adapter` 实现内。
3. THE 候选打分/指标/快照构建 SHALL 移出 market_data 的采集职责。

### 需求 9 · 回测家族统一（Speculative / S8）
**验收标准**
1. THE trade/指标/样本成形 SHALL 收敛到 `backtest/result` 单一深模块。
2. THE 形状二命令式回测策略 SHALL 对齐需求 4 的 Strategy 协议方向（S8）。

### 需求 10 · 测试与验证
**验收标准**
1. THE 新增/变更的纯 in-process 模块（地基/因子/策略/模拟/聚合）SHALL 有 pytest 单测，且本环境可运行。
2. WHEN 某接口被删除或替换，THEN 绑定该旧接口的测试 SHALL 改写为新接口测试，SHALL NOT 保留双份覆盖。
3. THE 触库测试 SHALL 以 `production_mysql` 标注，在 CI 带库运行，本环境跳过。
4. IF 持久化 SQL 非 sqlite 可移植，THEN S7 golden-master SHALL 退化为纯计算件本地对拍 + 触库部分 CI 带库（见 design §12）。

### 需求 11 · 交付与版本控制
**验收标准**
1. THE 重构 SHALL 按 S1–S8 拆成多个主题 commit，每个可独立落地。
2. WHEN 新增文件，THEN SHALL 执行 git add 纳入版本控制。
