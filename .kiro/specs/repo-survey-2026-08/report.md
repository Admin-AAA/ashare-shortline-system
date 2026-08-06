# 同类 A股/量化仓库横向调研与可迁移点评估

> 调研日期：2026-08-05
> 调研方式：8 个 GitHub 仓库全部浅克隆至 `/tmp/repo-survey/` 后读真实代码（禁止只凭 README），7 维度逐仓过一遍，本仓库侧用 codegraph + 读码确认「是否已有等价物」。
> 本报告不改动任何业务代码，只产出结论。

---

## 0. 一句话总览

**8 个仓库里，没有一个是和我们同一定位的「生产级短线推荐系统」。** 它们分三类：

- **选股/信号脚本**（ZBS、cn_stocks）：无回测、无风控、无验证，量化能力为零，全面落后我们一个量级。
- **教学级回测框架**（quant-trading-system、ai_stock_analysis）：模块骨架工整，但回测内核踩了前视/零成本/口径混杂等硬陷阱，数值结论不可信。
- **LLM 研报生成器**（daily_stock_analysis、llm-stock-team-analyzer、stock_agents）：LLM 编排和工程化包装成熟，但**没有策略回测/alpha 研究闭环**。
- **个人自动交易实验**（yang-yongxing-strategy）：风控分层和 LLM 克制用法有亮点，但仓库发布态跑不起来、回测四宗罪全占、自有证据已显示负期望却无证伪闭环。

**净结论：我们的「推荐→结算→复盘→self_check→反事实」证据链在 8 个仓库里无一对标；对方真正比我们强的，集中在 4 个点上——①横截面因子评估器（IC/RankIC/分层/单调性）、②技能权重的贝叶斯收缩在线调权、③LLM 成本预算闸门、④LLM 确定性护栏/防兜圈。** 详见第 3 节可采纳清单。

---

## 1. 本仓库对照系（评估「是否已有等价物」的基线）

在判断对方「比我们强」之前，先用 codegraph + 读码固定我们自己的现状，避免凭印象误判：

| 能力 | 本仓库现状（证据） | 结论 |
|---|---|---|
| 策略插拔 | 12 个 `backend/skills/*/strategy.yaml`，`universe.include/exclude` 策略自主过滤；装饰器式因子注册表 `services/factors/registry.py:69` | **已有且更成熟**（策略即配置 + AST 规则缓存） |
| 执行/成交假设 | `services/outcome_simulation.py` + `engine/execution_core.py`：滑点 bps、entry gap 上限、trailing stop、涨跌停不可成交、停牌 0 价防护、T+1 离场口径、`execution_policy.py` 不可变策略注册 | **8 仓无一对标** |
| 回测收益口径 | `pct_chg` 链式累乘（除权安全，`memory: ex-rights-detection`） | **做对了**；对方多用复权价比值（等价）或踩坑 |
| 因子评估 | 仅 `scripts/feature_signal_guard.py`：point-biserial + 时间分布判断（防单日 regime 伪影） | **缺 IC/RankIC/分层/单调性**（真实缺口） |
| 策略调权 | `services/calibrator.py` + `scripts/calibrate.py`：ROC AUC 优化、formal-only 口径、曾因过拟合禁 apply（`memory: score-no-discrimination`） | 有但是**一次性重校准**，非在线保守更新 |
| LLM 成本 | `agents/agno_skill_agent.py:137` 追踪 per-call token（含 prompt-cache `cached_tokens`）+ T8 per-skill 成本聚合 | **有成本追踪，无预算闸门** |
| LLM 护栏 | 熔断器（永久性错误首次熔断）、route-health、`_normalize_action` stance/action 一致性校验 | **有，但无防兜圈/token 截断纪律** |
| 数据源 | Tushare 主源 + Baostock 分钟 K 备用 + `data_source_health.py` 成功率追踪 | 有备用源，**非统一优先级管理器** |
| 风控 | 逐票 hard_stop（execution_core）、daily cap、regime veto | 逐票止损完善，**无组合级独立物理闸门**（但我们是推荐系统非自营盘） |
| 调度 | Prefect 编排 + DB 驱动 ScheduledTask + misfire 容错 + stale run 回收 | **8 仓无一对标**（对方多为 cron/自研/无） |
| 证据纪律 | self_check 护栏、feature_signal_guard、反事实验证、按月等权归因、backtest≠formal | **8 仓全无** |

---

## 2. 每仓库一张卡片

> 每张卡片：定位 / 架构一句话 / 7 维度要点（带证据路径）/ 比我们强 / 比我们弱 / 踩的已知陷阱。
> 「证据路径」均相对各仓库根（`/tmp/repo-survey/<repo>/`）。

---

### 卡片 1 · ZBS-Stock-Screener（A股，宣称 6 策略独立运行）

**定位**：选股 CLI + 个股数据采集脚本 + LLM 研报 SKILL 的组合。**不是量化系统**——无回测、无数据库、无调度、无风控、无测试。真正的本体是 646 行的 `SKILL.md` prompt 工程，代码只是数据采集器。

**架构一句话**：两个根级 CLI + `src/` 四模块，三代 HTML 渲染器并存且互相矛盾。

**7 维度要点**：
- **A 架构**：`stock_screener.py:88` 用 `STRATEGIES` dict + `getattr` 反射派发，策略是硬编码方法。`config.yaml` **无任何代码加载**（grep `yaml` 零命中）。零测试。`stock_full_report.py` 与 `src/data_fetcher.py` 大面积逐字重复且已漂移。
- **B 策略**：宣称 6 策略，**实现全是「涨跌幅+成交额+最新价」三字段过滤**（`stock_screener.py:171/231/287/347/402/462`）。docstring 宣称的 RSI/股息率/北向/换手率因子一个没用，`_get_kline()` 是未接线死代码。评分公式拍脑袋（`:191` `score = 50 + abs(change)*3 + amount/1e8*2`）。
- **C 数据**：全 akshare，K 线 `adjust="qfq"`。多源 fallback（新浪快照失败回退东财）是少数做对的事。**无增量/无本地池**，每次全量现拉。`_filter_valid_kline` 会丢弃 15:00 前未收盘 K 线（意识好）；但报告期硬编码 2023-2024 旧数据。
- **D 回测**：**无**。
- **E 风控**：**无**。
- **F LLM**：`SKILL.md` 是纯 prompt 工程。唯一亮点是 `:151-166` 的「LLM 产出分批 grep 机械校验流水线」。
- **G 工程**：无调度/无监控。HTML 报告渲染花了心思。

**比我们强**：仅两点设计细节——LLM 产出的 grep 校验流水线、盘中未收盘 K 线过滤。
**比我们弱**：全面。无回测/风控/数据池/验证/测试。
**踩的陷阱**：策略名与实现口径脱节（宣称 RSI 实为涨跌幅过滤）；拍脑袋评分权重；T+1 涨停接力无成交建模；幸存者偏差潜伏（当前在市股）。

---

### 卡片 2 · cn_stocks（A股，6 技术策略 + 综合投票）

**定位**：纯技术指标信号生成 + Plotly 可视化工具。**全仓 grep `回测/收益/滑点/止损/仓位/T+1` 0 命中**。`engine/` 目录名误导，实为多线程批量跑策略出 buy/sell/hold 信号的分发器。单 commit。

**架构一句话**：`analyzer/ → strategy/ → engine/ → database/ → visualization/`，装饰器工厂自注册是全家最干净的设计。

**7 维度要点**：
- **A 架构**：策略装饰器工厂 `strategy/base_strategy.py:184`，新策略加 `@StrategyFactory.register_strategy` 即被全链路自动发现——**无「enabled 但没注册进 preset 就静默死亡」的坑**（对照我们的 strategy-preset-registration-blindspot）。但 `core/db_utils.py:6` 把 **MySQL 密码 + Tushare token 明文提交进 git**。零测试。
- **B 策略**：6 个全是教科书指标（ADX/MACD/RSI/布林/MA 交叉/KDJ）。信号只看最后一根 K 线（`rsi_strategy.py:113` `dataframe.iloc[-1]`）。置信度是拍脑袋累加（`:130` `confidence=0.7; +0.1 if rsi_rising; +0.15 if divergence...`）零校准。综合投票是简单多数表决，但 6 策略同源、相关性极高，**投票多样性是假的**。
- **C 数据**：baostock，只有 15min 线，**`adjustflag="3"` 不复权**，在原始价上算 MA/MACD/动量（`adx_trend_strategy.py:134` 原始价收益）。增量维护是**破坏性全量重灌**（`fetch_15min_data.py:340` 默认 `--clear-table`）。缺数据填 0 污染 OHLC。
- **D 回测**：**无**。
- **E 风控**：**无**，无 T+1 意识（15min A股信号系统却不提示当日买入不可卖）。
- **F LLM**：**无**。
- **G 工程**：Flask + Plotly 三级页面是唯一花了心思的地方，但 `web_app.py:118` 指标 `'indicators': {}  # 暂时设为空` 半成品，`debug=True host=0.0.0.0` 裸奔。

**比我们强**：①策略自注册不易静默死亡；②信号原因/指标归一化可查询存储（`analysis_db.py:199`，比我们把审计塞进单 blob `market_context_json` 更可查询——我们曾因此 Data too long）。
**比我们弱**：回测/风控/配置/增量/LLM/调度/监控/测试全面缺失。
**踩的陷阱**：**不复权原始价算指标**（除权跳空污染趋势/动量）；**当下流动性选股的选择偏差**（按当前成交额取 top N）；**拍脑袋置信度零校准**；**无视 T+1**。

---

### 卡片 3 · quant-trading-system（A股，宣称 8+ 策略可插拔）

**定位**：教科书式「教学级量化框架」。~43k 行 src + 13.5k 行测试（测试占比 31%）。模块骨架工整，**但核心回测引擎踩了「当日收盘产生信号并以当日收盘成交」的前视陷阱，ML 策略在批量回测下形同虚设**。全仓无 LLM。

**架构一句话**：`data/strategy/factor/ml/backtest/risk/trading/scheduler/web` 分层 + 装饰器注册表三件套贯穿。

**7 维度要点**：
- **A 架构**：装饰器注册表三件套（策略 `strategy/base.py:58` / 数据源 `data/base.py:98` / 因子 `factor/base.py:100`），注册时类型校验 + 未命中列出可用项的友好报错 + `clear()` 测试隔离。Pydantic v2 四级配置 + 跨字段 `model_validator` + 聚合 `validate()` 一次列全错误。**876 个测试函数**，`test_walk_forward.py` 是真行为断言。
- **B 策略**：8+ 经典指标策略 + 3 ML 策略 + 复合投票（majority/unanimous/any）。无市场结构信号。
- **C 数据**：AKShare + Tushare，默认 `adjust="qfq"`（**除权安全，做对了**）。但 Parquet 缓存按完整区间键控，**换区间整体重拉、无增量**。清洗仅列裁剪，复权正确性外包给上游。
- **D 回测**：成交假设完整（滑点/佣金 min5 元/印花税单边/**分板块涨跌停** `broker.py:37`/T+1 pending rollover）。**❌ 前视**：`engine.py:158` 用 `row["close"]` 同时当信号输入价和成交价；海龟/双推力 `.shift(1)` 规避而 MA/RSI 不规避，**纪律不一**。有**真 walk-forward**（`walk_forward.py` 滚动窗口 + 净值拼接）。ML 标签无泄漏但 **ML 策略只写 `data.iloc[-1]` 信号 → 历史全 hold，无法被回测验证**（正确性硬伤）。
- **E 风控**：仓位三法（fixed/Kelly 负值不交易/ATR）+ 组合约束（单票≤30%/总仓≤80%）。TrailingStopManager 三模式实现完整**但未接主回路**。
- **F LLM**：**无**。
- **G 工程**：Streamlit + 自研 TaskScheduler + SQLite + Docker 非 root。

**比我们强**：①**横截面因子分析器 ≈ mini-Alphalens**（`factor/analyzer.py:154-201`，IC/RankIC/ICIR/分层收益/多空/单调性/覆盖率/自相关）——**这是我们最该抄的**；②注册表三件套 + 友好报错；③walk-forward 净值拼接；④分板块涨跌停矩阵一处收齐；⑤Pydantic 跨字段校验。
**比我们弱**：无增量数据池、单标的回测为主（无全市场宇宙→精选管线）、无 LLM、自研调度无容错、ML 策略回测失效。
**踩的陷阱**：**❌ 前视/不可成交**（收盘价决定又以收盘价成交，乐观偏差实锤）；⚠️ 前视纪律不统一；❌ ML 仅末日 1 根 bar 出信号；⚠️ 单标的网格直接对 `win_rate` 寻优（小样本幻觉温床，靠 walk-forward 人工兜底）；✅ 未踩「价格算收益」（qfq 做对）。

---

### 卡片 4 · ai_stock_analysis（A股，宣称 AI 预测 + 多策略回测）

**定位**：名不副实——`requirements.txt` **无任何 LLM 库**，「AI 预测」= ARIMA+GARCH/XGBoost/线性回归/Monte Carlo 随机游走，「新闻情感」= 关键词计数。本质是 Flask + backtrader 单机 demo。单 commit，零测试。

**架构一句话**：6 模块平铺（`app.py` 13 端点 / `engine.py` backtrader / `recommender.py` 多因子 / `predictor*.py` / `validator.py`）。

**7 维度要点**：
- **A 架构**：字典注册表 `engine.py:344`，但策略与参数 schema 两处手写无单一事实源。无配置体系（佣金/权重全硬编码）。import 时改写 `os.environ` 污染全局，难单测。
- **B 策略**：回测 3 个经典指标（MA 交叉/RSI/布林）。推荐 7 因子（动量 20d 30% / 相对强弱 20% / 趋势加速度 15% / 均线多头 15% / 低波动 10% / 量价 5% / RSI 5%），**权重全拍脑袋无验证**。亮点：F1-F7 用**绝对阈值映射**「避免矬子里拔将军」（`recommender.py:895`），只有相对强弱保留截面排名。
- **C 数据**：baostock，**全程 qfq 前复权**，价格差分==pct_chg（**陷阱实际避开，做对了**）。展示涨跌幅时切不复权取原生 pctChg，两处口径各自正确。baostock 容错（每次查询强制 logout+login 清 TCP 残留 + 连接错误定向重试 3 次）值得借鉴。但 **OHLCV 零缓存零增量**，推荐逐股串行查询。行业池硬编码且含大量退市股 + 行业错分 + 「同 15 只复制 6 遍凑成 100 只」的数据造假式填充。
- **D 回测**：开盘价成交合理，但**滑点=0、无印花税、无涨跌停不能成交、无显式 T+1**（偏乐观）。策略层无 walk-forward；但预测层有**真 walk-forward 验证器**（`validator.py:50` train 严格先于 test、逐步前移），且**把模型历史方向准确率透明展示给前端**。坑：测试窗口重叠（test_size 15 > step 10）+ XGBoost 验证用 close-only 特征而预测用全 OHLCV（口径不一致）。
- **E 风控**：**几乎为零**。`PercentSizer(95)` = 单股 all-in；无止损止盈（出场全靠反向信号，可一路拿到底）；无组合概念。
- **F LLM**：**无 LLM**。XGBoost 特征全后视、label `shift(-horizon)` 对齐、预测点排除出训练——**教科书式正确监督学习 setup，非 look-ahead**。真缺陷是小样本单资产（单股 ~480 样本对 30+ 特征）。「按 walk-forward 准确率 softmax 动态调权」是轻度过拟合。Monte Carlo 带漂移 GBM 当「预测」卖是误导。
- **G 工程**：无调度/监控/日志/测试/鉴权。**SSE 流式进度**是亮点（`app.py:495` queue+线程+EventSource 实时推进度条）。

**比我们强**：①预测模型 walk-forward 验证器 + 模型准确率前端披露；②全程 qfq 一致口径；③绝对阈值映射评分；④baostock 容错；⑤SSE 流式进度。
**比我们弱**：风控为零、因子零验证、推荐靠随机采样 100 只（非全市场扫描）、数据零持久化、无 LLM/调度/监控/测试。
**踩的陷阱**：小样本单特征幻觉（单股小样本训 XGBoost 再预测同一只）；按验证准确率调权重（重叠窗口噪声大）；口径混杂（随机池内做截面排名，同一股分数随样本漂）；回测偏乐观（滑点 0/无印花税/无 T+1 建模）。

---

### 卡片 5 · daily_stock_analysis（多市场，15 策略框架 + LLM，Star 5.5k+）⭐质量最高

**定位**：**LLM 驱动的自选股每日分析报告生成器**（非选股/回测系统）。「15 种策略框架」实为 15 个自然语言 YAML prompt 包（引导 LLM 从缠论/波浪/龙头/情绪视角解读，从不回测）。唯一的 `backtest_engine` 是「AI 建议事后验真器」。**工程化和 LLM 编排极成熟，量化方法论单薄。**

**架构一句话**：`api/apps/bot/data_provider/src/strategies` 分层，策略即 YAML 启动扫描自动加载，但有多个 200k 级上帝文件。

**7 维度要点**：
- **A 架构**：策略即 YAML 目录扫描自动加载，无需代码（`strategies/README.md`），`AGENT_SKILL_DIR` 可覆盖内置——比我们 preset 注册更轻。运行时路由选 ≤4 个策略各造 `SkillAgent`（`orchestrator.py:806`）。环境变量驱动配置即文档（`.env.example` 50k）。tests/ 262 文件，引擎类 DB 无关纯函数便于单测。
- **B 策略**：15 个 LLM prompt 策略 + 1 个确定性技术面打分器（`stock_analyzer.py` 算趋势/乖离/MACD/RSI/量能产 0-100 `signal_score`）+ 独立多因子选股模块 `src/services/screening/`（来自姊妹项目 AlphaSift）。硬过滤 per-strategy `HardFilterConfig`（剔 ST/最小成交额/换手，**与我们「策略自主 universe」同思路**）。screener 有**追高惩罚**设计（涨幅>5% 开始扣分）——我们「禁止追高规则」的镜像，但**它们无回测验证这些参数**。
- **C 数据**：**9 个行情源 + 优先级 fallback**（`data_provider/base.py` `priority` 排序自动降级 + 记录 fallback_from/to），且**清楚每个源的复权坑**（知道 akshare 美股复权有 bug 主动绕行 `:484`）。A股 qfq 前复权。内存 TTL 缓存 + SQLite + SQLAlchemy（含 schema migration）。**无本地增量日 K 池**（按需拉取，对自选股够用但撑不起全市场研究——定位差异）。
- **D 回测**：唯一引擎是「AI 建议验真器」（把历史 `operation_advice` 文本解析成方向对照后续日 K）。入场=决策日收盘、前瞻严格 `date > analysis_date`（无前视）、qfq 价格比值（A股成立）。**日内二义性处理老实**（同根 K 同时触止损止盈→记 `ambiguous` 按止损先触发 + 报 `ambiguous_rate`，`backtest_engine.py:171`）。**但零成本（无手续费/滑点/印花税）却展示胜率/收益 → 数字系统性偏乐观**；无 walk-forward/OOS；胜率全量平均无 regime 归一。
- **E 风控**：**风险层「只降级不升级」类型化状态机 + 校验器**（`risk_override.py` `_VALID_DOWNGRADE_TRANSITIONS`，任何升级/矛盾记录直接 raise）——纪律性强。止损来自 LLM 输出的「狙击手点位」，缺失则判 LLM 输出不完整拒绝。组合服务只分析用户导入的真实持仓，**无系统化仓位求解**。
- **F LLM（Star 高的主因，做得最深）**：多代理链（技术+情报+风险+组合+N 策略子代理+决策汇总）。**成本控制最细**：token 预算（`research.py` 超限即停）+ 墙钟预算 + 最小步长护栏 + 单策略超时/并发 + **路由只跑 ≤4 策略（砍掉 73% 调用）**。LiteLLM Router 多模型 fallback + 定价注册。**输出强 schema 校验 + json_repair 容错**（缺 stop_loss 直接拒）。**prompt-cache 命中率遥测**（`storage.py` 一整组 `normalized_cache_hit_ratio/cached_tokens` 列，量化「省了多少缓存钱」）。
- **G 工程（产品级，远超我们）**：零成本 GitHub Actions 每日跑 + Docker + 桌面端打包 + 6 渠道通知（企微/飞书/TG/Discord/Slack/邮件）+ Markdown 转图片 + 通知降噪。`phase_decision_guardrail.py` 盘前/数据降级时强制保守决策。

**比我们强**：①**技能权重贝叶斯收缩 + 硬钳制**（`skill_opinion_weight_service.py`：Beta 先验 15/15 压小样本回 0.5、min 30 样本门、权重钳 `[0.83,1.2]` 最多 ±20%、evidence_strength 收缩、对「常返回 unable」策略罚 0.25）——**直接对治我们「按胜率调权重过拟合」与「score 无区分度」两个老大难**；②**LLM 成本工程**（预算闸门 + 路由限流 + prompt-cache 遥测）；③**多源优先级 fallback**；④**风险只降级状态机**；⑤**LLM 输出强 schema 校验 + json_repair**；⑥**回测日内二义性保守处理**；⑦**零成本交付管道**。
**比我们弱**：无真正策略回测/alpha 研究闭环（只验「AI 建议准不准」）；回测零成本假设；胜率全量平均（正是我们「04月beta非alpha」证伪的口径，它无防护）；无系统化仓位；无本地增量池；技术面打分器固定手工加权从不按 outcome 校准。
**踩的陷阱**：**全量平均胜率无按月/regime 归一**；**零手续费/滑点**；⚠️ screening 几十个手工因子斜率/阈值无验证 harness（手工过拟合嫌疑）。**未踩**：前视（无前视）、价格算收益（qfq 对）、按胜率调权重（贝叶斯三重护栏直面并防住）。

---

### 卡片 6 · llm-stock-team-analyzer（多 Agent，宣称 4 策略视角）

**定位**：TradingAgents 框架的本地精简 fork——单股分析报告生成器。无回测/执行/仓位/持久化。「4 种策略视角」实为 Market Analyst 一个 agent prompt 里的 4 套指标组合预设（LLM 自选），不是 4 个独立 agent。

**架构一句话**：`main.py → graph/(LangGraph StateGraph) → agents/(node 工厂) → dataflows/ → configs/`，线性链 + Bull⇄Bear 辩论回路。

**7 维度要点**：
- **A 架构**：LangGraph `StateGraph`，analyst 白名单硬编码，加 analyst 要改 3 处。配置双轨（pydantic + YAML 拍平）有全局状态坏味道。tests 只测指标纯函数（含空 df/单值/常数序列边界，比我们部分测试细）。`reflection.py` 引用不存在的 state 是从原仓库未裁剪的死代码。
- **B 策略**：无规则化策略，4 套指标组合预设（顺势/震荡突破/反转/波动风险，`market_analyst.py:24-48`）。**prompt 里直接断言「MACD(5,13,9) 更敏感」「RSI_7 比 14 更敏感」——零样本拍脑袋调参写进 prompt 当结论**。RSI/ADX 实现偏离标准（非 Wilder 平滑），与 prompt 里写的标准阈值错配。
- **C 数据**：yfinance（美股/台股，**无 A股源**）。**无缓存**（data_cache 目录建了不用，同 run 内重复下载同区间）。复权靠 yfinance 隐式默认 + `fetch_kline_data` 把价格 `//1` 取整丢精度。
- **D 回测**：**无**。Reflector 的「根据 returns 反思」是从未调用的死代码。
- **E 风控**：**无**（BUY/HOLD/SELL 三态文字，不带数量/价格/失效条件）。
- **F LLM（重点，有真东西）**：**Bull⇄Bear 辩论状态机化**（计数器 + LangGraph 条件边驱动，双保险防死循环）。**token 纪律工程化**：analyst 间主动 `RemoveMessage` 清空历史、辩论历史**按发言边界截断**（`bull_researcher.py:27`，避免半句切断）、工具调用次数写进 prompt 硬约束。**防时间幻觉 prompt**（system+user 双层注入当前日期，`market_analyst.py:108`）。**全状态审计快照落盘**（每次 run 一份完整 JSON，天然支持复盘每个 agent 说了什么）。记忆机制设计好（ChromaDB 情境检索）但内存 client 每次启动为空、reflect 从未调用——**闭环是死代码**。
- **G 工程**：Docker + uv.lock 一键复现（比多数 side project 好）。全状态落盘是好的审计实践。无前端/调度/监控。

**比我们强**：①辩论编排状态机化（计数器驱动路由 + 强制退出）；②token 纪律工程化（主动清空 + 按发言边界截断 + 工具调用次数硬约束——我们 6700-token skill prompt 可参照分层）；③全状态审计快照；④防时间幻觉 prompt。
**比我们弱**：不是交易系统（无回测/风控/结算/DB/调度/前端）；无缓存；记忆/反思闭环死代码；配置 wiring bug（`max_debate_rounds=1` 配置 vs 节点硬编码 2 → 默认下 investment_plan 永不生成，同类「静默死亡」）。
**踩的陷阱**：**零样本单特征幻觉重度**（4 套「优化参数」无样本验证直接当结论）；**口径/契约不一致**（debate rounds 冲突致静默失效）；**隐性前视**（LLM 训练知识对历史日期有先验，仓库未提示）；指标口径非标准与阈值错配。

---

### 卡片 7 · stock_agents（宣称生产级，7 Agent 双架构）

**定位**：个股 on-demand 研报生成器（单股/多股分析 → 结构化研报）。**不是交易/推荐系统**——无股票池扫描、无每日管线、无 outcome 结算。「7 Agent 双架构」= 7 节点 LangGraph 顺序流水线 + 1 个 ReAct 自主 agent。「生产级」主要体现在部署和容错工程，量化方法论约等于零。

**架构一句话**：`app/{agents,orchestration,react_agent,tools,monitoring,resilience,storage,api}`，LangGraph 线性流水线 + 每节点条件边（continue/retry/error）。

**7 维度要点**：
- **A 架构**：agent 分有状态/无状态两基类，抽象干净。LangGraph 节点条件边，`_run_agent_node()` 统一做监控/广播/错误吞并（模板化好）。配置 pydantic-settings 单文件。**可测试性差**：核心分析/决策/风控/数据层零单测，无 CI。
- **B 策略**：无策略体系。信号全手写阈值打分（`analysis_agent.py:446` `if trend=="strong_bullish": score+=30`）。基本面 growth 维度是占位符直接返回 50 分。舆情是英文关键词袋套中文新闻全判 neutral。所有阈值零经验验证。
- **C 数据**：yfinance + AkShare + Finnhub。**无本地池/无增量/无批量**，A股现货 `ak.stock_zh_a_spot_em()` 全市场快照只为取 1 行（per-symbol 循环 → N 股 N 次全市场快照，性能炸弹）。缓存 ttl 参数接收后从不使用（假缓存）。**用原始价格差分算收益**（`risk_agent.py:87` `np.diff(closes)/closes[:-1]`，无 pct_chg/复权），且口径混杂（Yahoo 历史 + AkShare 现价拼同一股）。
- **D 回测**：backtrader + 4 玩具策略。佣金仅 0.001、无滑点/印花税/T+1。**默认 sizer=1 股**（$10000 买 1 股 AAPL，收益率不可比）。无 walk-forward/OOS。A股回测 `yf.Ticker(symbol)` 不加 `.SS` 后缀（与数据层不一致，大概率取空）。
- **E 风控**：VaR/波动率/最大回撤公式教科书正确，但 **beta 写死占位 1.0 仍参与评分展示**；止损=2×日波动率对涨跌停+T+1 无感知；组合分散度=数持仓只数（玩具级）。决策权重 0.3/0.4/0.15/0.15 纯钦定。
- **F LLM（有真东西）**：智谱 GLM + OpenAI 兜底。**ReAct 循环确定性护栏**：max_iterations=15 硬上限 + 重复 tool_call 检测强制收尾 + 旧 ToolMessage 截断防膨胀 + **状态机驱动 progression（reflect 不用 LLM，缺哪个分析工具就点名让 LLM 补，而非自由反思）**——把「反思」工程化成确定性推进，防 LLM 兜圈。**工具参数桥**（`_build_report_data` 由编排层替 LLM 装配复杂 schema 参数，承认「LLM 不知道 state 里有啥」）。但**成本闸门是空话**（`accumulated_cost` 从未累加，永不触发）。
- **G 工程（部分真亮点）**：**执行过程 WebSocket 实时推流**（按 thread 订阅广播每 agent 生命周期事件，前端能看 agent 逐个跑动——体验好）。**容错三件套按 agent 粒度落地**（熔断器 registry + 指数退避重试 + 超时限制器，经 `/api/monitoring/*` 暴露）。**部署闭环**（一键 deploy.sh rsync→build→健康检查、compose healthcheck 依赖、nginx 多项目路径分流、国内镜像源）。但宣传 vs 实现大面积货不对板（cost tracking 死代码、PostgreSQL checkpoint 实为 MemorySaver 且 save 有 TypeError bug、Prometheus/redis 未接线、LLM 研报注释掉、告警无外发通道）。

**比我们强**：①**执行事件 WebSocket 实时推流**（可迁到 run 详情页，比我们轮询体验好）；②**容错 registry 按 agent 粒度**（可推广到数据源/工具调用）；③**ReAct 确定性 progression 护栏**（防兜圈，我们做 LLM 自主循环时直接可用）；④**工具参数桥**；⑤**部署闭环**。
**比我们弱**：定位完全不同（无选股宇宙/每日管线/结算复盘闭环/formal-experiment 之分）；量化方法论为零；风控掺假（beta 占位）；宣传 vs 实现货不对板多处；双份实现漂移风险；舆情对 A股失效。
**踩的陷阱**：**用价格差分算收益**（无 pct_chg/复权）；**口径混杂**（Yahoo 历史 + AkShare 现价拼同一股、舆情首股兜底吞全部新闻、回测不加 `.SS`）；**无视 T+1**（回测允许当日往返、止损无视当日买入不可卖+涨跌停）；回测失真（1 股 sizer + 无成本 + 无 OOS）。

---

### 卡片 8 · yang-yongxing-strategy（A股，长短双策略 + 凤雏自动交易）

**定位**：个人自动交易实验——三套策略 + 四个角色（青鸾短线隔夜/仲达中线 SEPA/凤雏自动交易/北辰复盘），分布式跑 3 台机器 + 1 算力中心，全靠 cron + scp/SSH 文件契约串联。**仓库发布态跑不起来**（改名改一半 + 5 个 import 模块缺失）。

**架构一句话**：`run_*.py` 薄入口 + `src/{strategies,factors,backtest,tools}` + `config/*.yaml`（名存实亡）+ `cron_config/` + `日记/`（版本化决策记录）。

**7 维度要点**：
- **A 架构**：**主入口全部跑不起来**（`run_scan.py:71` 调未定义的 `fengchu_screen`；`run_backtest.py:17` import 不存在的 `fengchu` 模块；cron 引用缺失文件）。旗舰自动交易器 import 的 5 个模块全 MISSING。配置双套脱节（yaml 无人加载，真实参数是策略文件硬编码 dict）。**零测试**，79KB god-file + 全局可变状态。
- **B 策略**：青鸾（涨幅 3-5% + 量比>1 + 换手 5-10% + 流通市值 50-200 亿 + 20 日内有涨停 → 六项打分≥70 买）。仲达（SEPA 8 条件 + VCP + 板块 + 五因子，但**五因子是常量桩固定返回 12 分**）。凤雏（均值回归只买国资池 + DeepSeek 多因子动量 + 雷达<40 分空仓）。**信号源广度是亮点**（BOLL/周期统计/行业漏斗五维/东财涨停池/财联社免key/HMM regime/GARCH/GPU MLP/宏观 LLM）。
- **C 数据**：多源混合务实（akshare + 东财 push2 实时批量 + 腾讯实时 + requests 失败降级 curl）。**复权处理正确**（前复权 + 直接取 API `pct_change`，避开了原始价格陷阱）。SQLite `market_cache.db` 增量。**三个数据层陷阱**（见下）。
- **D 回测（重点，三套引擎全部不可信）**：①`run_backtest.py` 时空错配（用**当下**实时快照回测历史日期，直接作废）；②`backtest_engine.py` 买=当日快照卖=次日开盘方向合理，但**零成本**（对 +3%/-2% 薄利策略往返 0.15% 成本足以翻转结论）+ **止盈止损按次日开盘价夹断**（跳空+8% 只记+3%、-8% 只记-2%，盈亏比系统性虚高）+ **未来函数**（`has_20d_limit_up` 用截至今天的 `tail(20)` 服务历史买点）+ **回测/实盘口径混杂**（回测白送 above_vwap 等 25 分）；③`factors/engine.py` 当日收盘出信号当日收盘成交（前视）。**参数扫描=教科书过拟合机器**（`run_param_scan.py:53` 纯样本内网格选「最优夏普/最优收益」，无 OOS/walk-forward/多重检验）。**作者自曝**：`日记/` 里默认配置 60 天回测 -0.38%/胜率 28.6%，仍继续跑，无证伪闭环。
- **E 风控（全仓库完成度最高）**：**「硬熔断独立于模型」分层**（单票浮亏≤-7% 强平、组合回撤≤-3% 全清，注释明写「独立于模型的物理闸门」）。**行为冷却**（止损后冷却 5 天、连续 3 次止损暂停卖出 3 天、最小交易间隔 3 天，防过度交易）。涨停池排除追板、3 年价格分位>80% 禁买。但「半凯利」实为固定 3%（营销名词≠实现）、trailing 死代码（止盈判断在前遮蔽）。
- **F LLM（克制，值得借鉴）**：多角色人格化（凤雏执行/刘秀风控审核官/北辰复盘/宏观分析）。**刘秀 LLM 窄域审核官模式**：只在 -4~-5% 浮亏 / +7~+8% 浮盈边界被咨询，输出单票 approve/reject JSON，离线/超时兜底为硬逻辑批准（`fengchu_auto_trader.py:836`，temperature=0.1）。**成本≈0**（全本地 Ollama，极少触发）。但编排 bug（LLM 看一只股票批一堆股票）。
- **G 工程**：系统 cron 三机分布 + 算力中心 GPU 并行回测（multiprocessing + 只读 SQLite 无锁）。飞书机器人 + HTML 邮件日报 + daemon 全局异常捕获。**`日记/` 版本化决策记录**（每次改动带版本头 + 实测数据表，比 commit message 更适合复盘「当时为什么这么改」）。但绝对路径硬编码、状态散落 JSON 无事务、无心跳/幂等、83MB SQLite 每日 scp 当「数据同步」。

**比我们强**：①**「硬熔断独立于模型」的分层风控**（物理闸门与策略逻辑解耦、最高优先级，「哪怕模型全错也兜底」）；②**LLM 窄域审核官模式**（LLM 只做边界一票否决、不碰排序、可证伪、成本≈0——比我们 LLM 给 delta 参与排序更保守）；③**基本面质量去劣模块**（`quality_screen.py` 七指标 + 豁免条款 + 金融业识别 + fail-open——我们没有基本面质量筛）；④**regime→策略切换落地**（雷达分直接映射三策略 + 仓位缩放，我们 regime 只做事后归因）；⑤**行为冷却约束**；⑥算力分层架构；⑦免 key 数据源技巧；⑧`日记/` 版本化决策记录。
**比我们弱**：仓库自举即死（缺模块/入口 NameError/改名一半）；零测试；回测全面不可信且自曝负期望仍跑；配置与实现脱节；纸面交易无券商对接；god-file + 全局状态；零成本意识。
**踩的陷阱**：**未来函数两处**（`has_20d_limit_up` 用今天的数据服务历史买点；`run_backtest.py` 用当下快照回测历史）；**幸存者偏差**（按当前名称滤 ST/退市构造历史宇宙）；**小样本单特征幻觉典型**（`stock_cycle_stats.json` `n_cycles=6, win_rate=100%` 直接驱动真金白银买入）；**按指标调权过拟合典型**（样本内网格选最优夏普）；**口径混杂**（回测白送 25 分、快照缺失时 `volume_ratio=1.0` 架空硬过滤）；**T+1/涨跌停部分踩**（收盘信号当日成交、止盈止损开盘价夹断）；**零成本假设**。**做对的**：复权（前复权 + API pct_change）。

---

## 3. 横向对比总表（仓库 × 维度，★评级）

评级口径：★=缺失/零基础；★★=有雏形但有硬缺陷；★★★=可用；★★★★=成熟；★★★★★=对标生产级。「—」表示该维度不适用（如无 LLM）。**最右列「我们」作为参照锚。**

| 仓库 | A 架构 | B 策略信号 | C 数据 | D 回测 | E 风控 | F LLM | G 工程 |
|---|---|---|---|---|---|---|---|
| ZBS-Stock-Screener | ★★ | ★（名实脱节） | ★★ | —（无） | —（无） | ★★★（prompt 工程） | ★★ |
| cn_stocks | ★★ | ★★（教科书指标） | ★（不复权） | —（无） | —（无） | —（无） | ★★ |
| quant-trading-system | ★★★★ | ★★★ | ★★★（qfq对/无增量） | ★★（前视硬伤） | ★★★★ | —（无） | ★★★ |
| ai_stock_analysis | ★★ | ★★（qfq对/因子无验证） | ★★（零缓存） | ★★（零成本/wf有坑） | ★ | ★★（ML setup正确） | ★★ |
| **daily_stock_analysis** | ★★★★ | ★★★（prompt策略） | ★★★★（9源fallback） | ★★（零成本） | ★★★★（只降级状态机） | ★★★★★（成本工程最深） | ★★★★★（产品级交付） |
| llm-stock-team-analyzer | ★★★ | ★★（prompt预设） | ★（无缓存） | —（无） | —（无） | ★★★★（辩论+token纪律） | ★★ |
| stock_agents | ★★★ | ★（拍脑袋） | ★（价格差分） | ★（1股sizer） | ★★（beta占位） | ★★★★（ReAct护栏） | ★★★★（部署闭环） |
| yang-yongxing-strategy | ★（跑不起来） | ★★★（信号源广） | ★★★（qfq对/有陷阱） | ★（四宗罪全占） | ★★★★（硬熔断分层） | ★★★★（窄域审核官） | ★★★（cron分布式） |
| **我们（ashare-shortline）** | ★★★★ | ★★★★ | ★★★★ | ★★★★（pct_chg+T+1+成本） | ★★★★ | ★★★★ | ★★★★（Prefect+证据链） |

**读表要点**：
- 我们的 **D（回测严谨性）** 和 **证据纪律** 在 8 仓中无一对标——这是核心壁垒，不能丢。
- 我们被比下去的集中在：**F 的「成本预算闸门/防兜圈护栏」**（daily、llmteam、stockagents 各有真东西）、**B 的「因子评估系统度」**（qts 的 IC/分层）、**B 的「在线保守调权」**（daily 的贝叶斯收缩）。
- daily_stock_analysis 是唯一在 F、G 两维全面压过我们的仓库，值得重点抄。

---

## 4. 可采纳清单（按价值/成本分三档）

> 每条：是什么 / 对方怎么实现（证据）/ 我们是否已有等价物 / 如何融入 / 采纳风险 / 如何按我们的纪律验证。

### P0 —— 低成本高价值，可直接借鉴

**P0-1 · 横截面因子评估器（IC / RankIC / 分层收益 / 单调性）**
- **是什么**：一个标准化的因子有效性评估器，输出 IC、RankIC、ICIR、IC 胜率、分层（如 Q1-Q5）收益、多空收益、单调性、覆盖率、因子自相关——≈ mini-Alphalens。
- **对方实现**：`quant-trading-system/src/factor/analyzer.py:154-201`，逐期截面 `pearsonr/spearmanr` 算 IC，`return_data` 明确约定为未来收益，分层收益 + 单调性检验。
- **我们现状**：只有 `scripts/feature_signal_guard.py` 的 point-biserial 单因子二值相关 + 时间分布判断。**缺 IC/RankIC/分层/单调性**——是真缺口（已用 grep 确认无 IC/RankIC/分层评估器）。
- **如何融入**：新增 `scripts/factor_ic_analyzer.py`（纯函数脚本，不改业务代码），输入因子值序列 + 未来 N 日收益（用我们 pct_chg 链式口径），输出上述指标。与 feature_signal_guard 互补：guard 判「是否单日 regime 伪影」，IC 分析器判「截面排序力」。改动量：约 200-300 行新脚本 + 复用现有数据查询。
- **采纳风险**：低。纯只读分析脚本，不碰生产路径。**注意口径**：IC 必须用精选池而非信号池（`memory: factor-eval-universe-vs-selected-caliber`），且按月分组防单月 beta 扭曲（`memory: backtest-month-imbalance-april-beta`）。
- **验证方式**：先用已知有效因子（avg_cost/bias_ma5/pos20/chip_concentration）跑一遍，确认 IC 方向与我们 LIVE 结论一致（交叉验证分析器本身正确），再用于新因子初筛。

**P0-2 · 辩论/多角色 LLM 的 token 纪律三件套**
- **是什么**：①analyst 间主动清空消息历史（报告走 state 字段而非消息堆叠）；②长历史按「发言边界」截断（不切半句）；③工具调用次数写进 prompt 硬约束。
- **对方实现**：`llm-stock-team-analyzer/agents/utils/agent_utils.py:10-23`（`RemoveMessage` 清空）、`agents/researchers/bull_researcher.py:27-41`（按发言边界截断）、`dataflows/interface.py:46-48`（"Do NOT call this function multiple times"）。
- **我们现状**：LLM skill prompt 曾达 6700 token（`memory: mysql-json-table-skill-perf`），有 prompt 前缀重排 + llm_review_cache，但**无系统性的上下文清空/截断纪律**。
- **如何融入**：在我们的多 agent / 多 skill 编排处（`agents/review_runner.py`、`agents/agno_skill_agent.py`），对长上下文按语义边界截断而非字符硬切。改动量：小，单点函数。
- **采纳风险**：低。纯 token 优化，不改决策逻辑。
- **验证方式**：token 消耗前后对比（T8 已有 per-skill 成本统计，直接看数字下降），并抽查 LLM 输出质量无退化。

**P0-3 · 策略/因子注册表的「友好报错 + clear() 测试隔离」**
- **是什么**：注册时类型校验、未命中时列出所有可用项的报错、`clear()` 方法便于测试隔离。
- **对方实现**：`quant-trading-system/src/strategy/base.py:58-72`（`StrategyRegistry`）、`cn_stocks/strategy/base_strategy.py:184`（装饰器自注册，无「注册遗漏静默死亡」）。
- **我们现状**：有因子注册表 `services/factors/registry.py:69`，但策略靠 preset 注册，存在「enabled=true 但没进 preset_formal_opening 就静默 0 命中」的盲区（`memory: strategy-preset-registration-blindspot`）。
- **如何融入**：①给策略解析处加「策略 enabled 但未注册进任何 preset」的启动告警（复用 self_check 的 E 项 0 命中检测思路）；②注册表未命中时列出可用策略名。改动量：小。**注意**：我们是有意用 preset 做环境隔离的，不要改成 cn_stocks 那种无隔离的自注册——只借「友好报错 + 遗漏告警」。
- **采纳风险**：低。只加告警/报错，不改注册语义。
- **验证方式**：构造一个 enabled 但未注册 preset 的策略，确认启动/自检时报警。

**P0-4 · 回测日内二义性的「ambiguous」保守口径**
- **是什么**：日线无法判断止损/止盈在同一根 K 内的先后时，记 `first_hit="ambiguous"` 并按止损先触发（保守），并在 summary 里报 `ambiguous_rate`。
- **对方实现**：`daily_stock_analysis/src/core/backtest_engine.py:171-174, 753-757`。
- **我们现状**：`memory: trailing-stop-backtest-pitfall` 已用「日线时序不确定时触发价用 min(open, 触发价)」处理过类似问题，但**没有显式的 ambiguous 标记和占比上报**。
- **如何融入**：在 `outcome_simulation.py` 的出场解析里，当同一根 K 同时触及止损和止盈时，显式记 ambiguous 并在聚合时报告占比。改动量：小。
- **采纳风险**：低。只是更诚实的口径标注，方向与我们现有保守处理一致。
- **验证方式**：对历史 outcome 统计 ambiguous 占比，确认占比低（<5%）则说明影响可控；高则需重新审视出场假设。

### P1 —— 有价值但需适配或先验证

**P1-1 · 技能权重的贝叶斯收缩在线调权（对治「按胜率调权重过拟合」）**
- **是什么**：按各策略历史 outcome 自动调权，但用 Beta 先验把小样本收缩回 0.5、设最小样本门槛、权重硬钳制在 [0.83, 1.2]（最多 ±20%）、evidence_strength 随样本量渐进、对「常返回 unable」的策略惩罚。
- **对方实现**：`daily_stock_analysis/src/services/skill_opinion_weight_service.py`：`posterior_hit_rate = (hit+15)/(evaluated+30)`（收缩）、`clamp 到 [1/1.2, 1.2]`（最多调 20%）、min 30 样本门。
- **我们现状**：有 `calibrator.py` + `calibrate.py`（ROC AUC 优化、formal-only 口径），但**曾因过拟合被禁 apply**（`memory: score-no-discrimination-root-cause`——聚合权重 formal 上无泛化排序力，walk-forward AUC 0.49）。我们缺的不是「调权」而是「保守的在线调权」。
- **如何融入**：不重写 calibrator，而是**借鉴其三重护栏思路**改造：任何自动调权必须 ①Beta 收缩压小样本、②钳制在 ±20% 以内、③min 样本门。这正是我们 memory 里反复踩的坑（`exposure-winrate-mismatch-not-bug`、`alpha-decay-false-positive`）的防护。
- **采纳风险**：**中高**。「按胜率/outcome 调权重」是我们的红线区，daily 的贝叶斯收缩只是降低风险不是消除。**绝不能直接照抄后 apply**。
- **验证方式（必须符合我们的证据纪律）**：①先只**离线计算**权重但不 apply，做 walk-forward 样本外对比（调权 vs 不调权的 formal 精选池排序力 AUC）；②用 feature_signal_guard 判权重的时间分布是否单日 regime 伪影；③按月等权归因确认非单月 beta 驱动；④**至少一个完整月的 formal 前瞻影子运行**（影子权重落库但不影响真实推荐）后才考虑启用。任一环节不过则放弃。

**P1-2 · LLM 成本预算闸门（token + 墙钟双预算，超限即停）**
- **是什么**：给 LLM 审查层加预算约束——token 预算（累计超限即停）、墙钟预算（剩余不足就跳过非必要步）、路由限流（只跑最相关的 N 个策略而非全跑）。
- **对方实现**：`daily_stock_analysis/src/agent/research.py`（`if tokens_used >= token_budget: 停止`）、`runner.py`（墙钟预算 + 最小步长护栏）、`orchestrator.py`（路由只跑 ≤4 策略，砍掉 73% 调用）。
- **我们现状**：已有 per-call token 追踪（含 prompt-cache `cached_tokens`，`agno_skill_agent.py:137`）+ T8 per-skill 成本聚合 + 熔断器。**但无「预算超限即停」的闸门**——我们是「全跑到完 + 事后统计」，对方是「预算约束 + 途中止损」。
- **如何融入**：在 `review_runner.run_skill_reviews` 的批量审查循环里加 token/墙钟双预算检查，超预算时对低优先级候选跳过 LLM 审查（降级为纯规则分）。改动量：中（需定义预算来源 + 降级策略）。
- **采纳风险**：中。预算闸门会在高候选日截断 LLM 审查覆盖，需确保「跳过」有明确标记而非静默缺失（否则又是静默死亡）。
- **验证方式**：先在 experiment 模式跑一周，对比「有/无预算闸门」的 LLM 成本与推荐质量（D 项胜率无显著下降则证明成本节省是白拿的）。

**P1-3 · ReAct / 多步 LLM 的确定性 progression 护栏（防兜圈）**
- **是什么**：多步 LLM 循环里，reflect/推进节点不用 LLM 自由反思，而是用纯规则状态机——缺哪个分析就点名让 LLM 补、全部完成则强制收尾；外加重复 tool_call 检测 + max_iterations 硬上限。
- **对方实现**：`stock_agents/app/react_agent/react_agent.py:354-490`（max_iterations=15、重复调用检测强制收尾、状态机 progression）、`llm-stock-team-analyzer`（辩论计数器驱动路由 + 强制退出）。
- **我们现状**：我们的 LLM 层是**单次调用**（review_runner），暂无多步自主循环，所以这套**当前用不上**。
- **如何融入**：**仅当我们未来引入多步 agent 循环时**（如让 LLM 自主调用多个数据工具做深度研究），直接套用「状态机 progression + 重复检测 + 硬上限」三件套。
- **采纳风险**：低（因为是条件性采纳，不引入多步循环就不需要）。
- **验证方式**：引入多步循环时，用「是否在规定步数内收敛、无重复工具调用」作为验收标准。

**P1-4 · 多数据源统一优先级 fallback 管理器**
- **是什么**：把多数据源抽象成带 `priority` 的统一 fetcher，按优先级排序、失败自动降级到下一源、并记录 fallback_from/to 供追踪。
- **对方实现**：`daily_stock_analysis/data_provider/base.py`（`BaseFetcher.priority`，manager 按 priority 排序 + 自动降级 + fallback 追踪）；`stock_agents`（yfinance→finnhub→akshare→stooq 五级链）；`ZBS`（新浪快照失败回退东财）。
- **我们现状**：Tushare 主源 + Baostock 分钟 K 备用 + `data_source_health.py` 成功率追踪，但**是点对点硬编码 fallback，非统一优先级管理器**。
- **如何融入**：在数据加载层（`services/data_source_health.py` 附近）引入 fetcher 注册 + priority + 自动降级 + fallback 事件落库。改动量：中。**注意**：对方知道每个源的复权坑（akshare 美股复权 bug 主动绕行）——我们接入任何新源必须先钉死该源的复权/单位口径（我们踩过 `amount_yi` 单位 bug、margin_detail 单位/方向坑）。
- **采纳风险**：中。多源数据的口径一致性是最大风险（不同源复权方式、单位、字段含义可能不同），接入前必须逐源校验。
- **验证方式**：新源接入前用「同一股票同一日期，新源 vs Tushare 的 OHLCV/pct_chg 一致性」做交叉校验；fallback 触发时落库并告警。

### P2 —— 方向性启发，暂不落地

- **P2-1 · 执行过程 WebSocket 实时推流**（stock_agents `monitoring/broadcast.py`）：驾驶舱/Run 详情页从轮询改为推送 agent 生命周期事件。体验好但我们是推荐系统非高频交互，ROI 低，暂缓。
- **P2-2 · 硬熔断独立闸门层**（yang `fengchu_auto_trader.py:92`，单票 -7% 强平/组合回撤 -3% 全清，与模型解耦）：理念好，但**我们是推荐系统不是自营盘**，无真实持仓可强平。若未来接券商实盘再考虑。当前我们的逐票 hard_stop + daily cap + regime veto 已覆盖推荐侧风控。
- **P2-3 · LLM 窄域审核官模式**（yang，LLM 只在边界被咨询、输出 approve/reject JSON、离线兜底硬逻辑、成本≈0）：与我们「LLM 给 delta 参与排序」是不同的定位哲学，更保守更可证伪。值得在「是否让 LLM 从参与排序改为只做边界否决」的架构讨论中参考，但涉及 LLM 层定位重构，暂缓。
- **P2-4 · 全状态审计快照落盘**（llmteam，每次 run 一份完整 JSON）：与我们 `market_context_json` 思路一致，但他们单股单文件无膨胀问题。我们已用 `_slim_market_snapshot` 瘦身 98.9% 解决膨胀（`memory: market-context-json-bloat`），方向已对齐，无需再改。
- **P2-5 · 版本化决策记录 `日记/`**（yang，每次改动带版本头 + 实测数据表）：与我们的 self-improve log + memory 体系重叠，但「每个决策带当时实测数据表」的习惯可融入我们 self-improve log 模板。成本极低，顺手做。
- **P2-6 · 基本面质量去劣模块**（yang `quality_screen.py` 七指标 + 豁免 + 金融业识别 + fail-open）：我们没有基本面质量筛。但短线场景基本面权重低，且需先验证这些因子在我们精选池口径下的有效性（feature_signal_guard + IC 分析器），列为研究方向而非直接采纳。

---

## 5. 反建议清单（对方有但我们明确不该学）

> 这些做法在对方仓库里存在，但不符合我们的短线场景 / 证据纪律 / 架构原则，明确拒绝。

1. **拒绝「零成本回测还展示胜率/收益」**（daily、yang、stock_agents、ai_stock 全部如此）。零手续费/滑点/印花税的回测数字系统性偏乐观，对薄利短线策略可直接翻转结论。我们坚持全成本口径（commission + stamp + slippage），这是底线。

2. **拒绝「当日收盘价产生信号并以当日收盘价成交」**（quant-trading-system `engine.py:158`、yang `factors/engine.py`）。这是前视/不可成交的实锤——收盘价决定的信号无法在收盘价成交。我们坚持 T+1 离场口径 + 次日可成交价。

3. **拒绝「样本内网格直接选最优参数」**（yang `run_param_scan.py:53` 选最优夏普/收益、qts `optimizer.py` 对 win_rate argmax）。这是教科书过拟合。我们坚持 walk-forward / formal 前瞻 / 反事实验证。daily 的贝叶斯收缩也只配 P1 谨慎验证，不配直接 apply。

4. **拒绝「用价格而非 pct_chg 算收益 / 在不复权价格上算指标」**（stock_agents `np.diff(closes)`、cn_stocks `adjustflag=3` 算 MA/动量）。除权跳空会污染一切。我们坚持 pct_chg 链式累乘（除权安全）。

5. **拒绝「把 LLM 训练知识当历史回测依据」**（llmteam 让用户输历史日期做「回测式分析」，模型对后续走势有先验）。LLM 的知识截止构成隐性前视，历史日期的 LLM「分析」结论不可信。我们的 LLM 层只做当下审查，不做历史 replay 式评估。

6. **拒绝「拍脑袋权重/置信度零校准」**（cn_stocks confidence 0.7+0.1+0.15、stock_agents 决策权重 0.3/0.4/0.15/0.15、ai_stock 因子权重 30/20/15/15/10/5/5、llmteam 的「MACD(5,13,9) 更敏感」断言）。任何权重/阈值必须有数据背书（LIVE formal 或反事实），这是我们的核心纪律。

7. **拒绝「无 T+1 意识的 A股信号/回测」**（cn_stocks 15min 信号无 T+1 提示、stock_agents 回测允许当日往返）。A股当日买入不可卖是硬约束，无视它的信号/回测都是空中楼阁。

8. **拒绝「全量平均胜率/收益不做按月或 regime 归一」**（daily `compute_summary` 全量平均）。我们已用「04月 beta 非 alpha」证伪过这种口径——单月主升浪 beta 会扭曲全量平均。坚持按月等权 + regime 分段归因。

9. **拒绝「LLM 自由文本输出直接透传为决策、无 schema 校验」**（llmteam 的 BUY/SELL/HOLD 靠 LLM 自由文本抽取无 fallback）。应学的是 daily 的反面——**强 schema 校验 + json_repair + 缺关键字段直接拒**。我们已有 `_normalize_action` 一致性校验，方向正确，可补强。

10. **拒绝「为追求策略数量而引入无法被回测验证的策略」**（qts 的 ML 策略只在末日 1 根 bar 出信号 → 无法被回测验证，形同虚设）。任何策略必须能被我们的 formal/backtest 管线验证，否则不上。

---

## 6. 诚实声明（调研边界与可信度）

**哪些是通读、哪些只看了局部**：
- 8 个仓库均读了入口、策略、数据、回测、风控、LLM、工程化的关键文件，证据路径已在卡片中标注。
- **daily_stock_analysis 体量最大（含前端共 30 万行）**，其后端 `src/` 核心逻辑通读，但**前端 `apps/`/`webui` 及部分 God 文件（`analyzer.py` 206k、`search_service.py` 192k）只读了关键段落**，未逐行覆盖。其「回测零成本」等结论基于 grep 全仓无 `commission/slippage` 命中，可信度高。
- **quant-trading-system（43k 行）** 通读了 engine/broker/risk/factor/walk-forward/optimizer，但 876 个测试只抽查了 `test_walk_forward.py` 等少数，未全读。
- 其余 6 仓（4k-16k 行）关键文件基本通读。

**哪些仓库实际质量与水评（README/宣传）不符**：
- **ai_stock_analysis**：名字带「AI」但无任何 LLM 库，「AI 预测」是 ARIMA/XGBoost/蒙特卡洛随机游走。
- **stock_agents**：宣称「生产级」，但 cost tracking 是死代码、PostgreSQL checkpoint 实为 MemorySaver 且有 TypeError bug、Prometheus/redis 未接线、LLM 研报被注释、告警无外发——**宣传 vs 实现大面积货不对板**。
- **yang-yongxing-strategy**：宣称「五因子」「半凯利」「自动交易」，实际五因子是常量桩、半凯利是固定 3%、自动交易是纸面 JSON + 飞书通知，且**仓库 fresh clone 跑不起来**（缺 5 模块、入口 NameError、改名改一半）。
- **llm-stock-team-analyzer**：宣称「4 种策略视角」，实为 1 个 agent prompt 里的 4 套指标预设；宣称的「反思/记忆闭环」是从未调用的死代码。
- **daily_stock_analysis**：宣称「15 种策略框架」，实为 15 个从不回测的 LLM prompt 包；宣称的「回测」实为「AI 建议事后验真器」且无成本。**但这不妨碍它的 LLM 工程是真材实料。**

**对本报告结论的总体置信度**：
- 「对方踩了什么陷阱」类结论：**高置信**（均有代码证据 + 我们 memory 里的对应血泪记录交叉印证）。
- 「对方比我们强」类结论：**高置信**（已逐一用 codegraph/grep 确认本仓库无等价物，或对等价物的差异做了核实——如 LLM 成本追踪我们有但预算闸门没有、calibrator 我们有但贝叶斯在线收缩没有）。
- 「如何融入/改动量」类估计：**中等置信**（未实际写代码，改动量为量级估计）。

**给后续行动的一句话建议**：8 个仓库验证了我们证据链的稀缺性——**不要因对方的工程包装（Star、Docker、前端）而动摇我们的方法论底线**。真正值得动手的只有 P0 四项（尤其 P0-1 因子评估器），P1 四项需谨慎验证，其余是方向性参考。

---

## 7. 实施状态（2026-08-06，随 repo-survey-adoption-2026-08 Spec 收尾更新）

> 实施 Spec：`.kiro/specs/repo-survey-adoption-2026-08/`（requirements/design/tasks）。分支 `fix/show-replay-in-records`。以下状态以「代码已提交 + 测试通过 + 真实环境验收」为准，不以「测试环境通过」代替真实验收。

**已实施（7 项）：**

| 项 | 状态 | commit | 真实验收 |
|---|---|---|---|
| P0-1 横截面因子评估器 | ✅ 已实施 | 67dfbe5 | CLI 实跑生产 MySQL；artifact hash/event/evidence URI 可重解析、幂等 reused=true。**数据前提阻塞**：生产 5 条已结算 outcome 全为 P0-4 前旧口径（identity 字段全 NULL），被正确排除（observation_count=0）；待新口径样本积累后可做方向比对，不放宽 identity 门槛。 |
| P0-2 LLM 上下文纪律 | ✅ 已实施 | 20656a1 | 19 测试通过；migration 0009 生产已 apply；单轮 + 结构化输出 + cache identity 不变。 |
| P0-3 注册遗漏诊断 | ✅ 已实施 | 64a3a03 | 真实加载 13 skill/13 preset。**发现真实配置缺口**：`overnight-holding-method` 已下线（b1e6564）但仍被 `preset_formal_opening`/`ef480d33af98` 引用 → formal preflight 阻塞；修复决策待用户确认（见 tasks 4.9）。 |
| P0-4 日线出场二义性 | ✅ 已实施 | 1898e41 | 99 测试通过；默认收益 fixture 一致；canonical evidence 来自 execution core，adapter 无二次猜测。 |
| P1-1 贝叶斯影子调权 | ✅ 已实施（shadow-only） | — | 依赖边界测试证明无生产读取路径、不改 Signal.weight/preset/默认推荐；真实样本受 P0-1 同约束，不放宽 min_samples=30。 |
| P1-4 数据源 fallback trace | ✅ 已实施 | 9de10a9 | 生产已落 29 条真实 trace（daily 成功 + 28 条 stk_mins 自然 fallback chain）；Tushare vs Baostock 日线 7 日 OHLC/pct_chg 逐位一致；migration 0010 已 apply。分钟级对比因 stk_mins 无权限阻塞（如实记录）。 |
| P2-5 版本化决策记录 | ✅ 已实施 | — | governance store 复用、append-only index、并发安全测试通过；本 Spec 的 accepted 决策记录随文档收尾创建（tasks 8.9）。 |

**明确排除（不实施）：**
- **P1-2 LLM 成本预算闸门**：用户明确排除。避免高候选日截断 LLM 审查覆盖造成静默缺失。
- **P1-3 ReAct/多步 progression 护栏**：用户明确排除。本系统保持单轮 LLM 调用，不引入多步自主循环，故无需该护栏。

**遗留 blocker（不降低验收标准）：**
1. P0-3 暴露的 `overnight-holding-method` preset 引用缺口，需用户决策是否摘除（tasks 4.9）。
2. P0-1/P1-1 的「真实 LIVE 样本方向比对」需待新口径结算样本积累（tasks 2.3.3）。
3. 半年策略回测补跑（/strategies 页面当前版本窗口画像）见 tasks 9/10。
