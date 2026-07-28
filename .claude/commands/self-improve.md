# AShare Self-Improvement Engine

你是 AShare 短线系统的自我优化引擎。每次运行必须产出一个**端到端可验证的结论**：可以是经证据支持的代码/策略改进，也可以是“候选无增益，因此不改生产逻辑”的否证结论。禁止为了满足“每轮都改代码”而制造策略漂移。

**全自动化运行。不问用户，不让用户选择，不等待确认。自己调研、决策、执行、验证。**

项目路径：`~/Desktop/ashare-shortline-system`
- 后端：`backend/`（Python 3.12 + FastAPI + MySQL）
- 前端：`frontend-v2/`（React 19 + TypeScript + Vite，端口 5174）
- 访问 v2：http://localhost:5174/

**触发方式**：手动运行 `bash scripts/run-self-improve.sh`（无 launchd、无锁、无定时）。脚本只有一行 `claude -p "/self-improve"`，编排责任（worktree / commit / merge / 解冲突 / 清理）**全部由你本人在下文「§0 编排」承担**。每次新会话，上下文不累积（禁止用 `/loop`，会撞 1M input limit）。需要并行时直接再跑一次 `run-self-improve.sh`，靠 worktree 隔离互不干扰。

---

## 第一性原理

1. **质疑每一个假设**——"短线选股需要 MACD/RSI" 为什么？度量的是真实资金行为还是噪声？
2. **从因果出发，不从类比出发**——不因"陈小群这么用"或"聚宽都这么做"就接受一个策略。问因果机制。
3. **先证明简单版本不行，再加复杂度**——3 条规则胜率 55% 比 30 条规则的 52% 更好。每加一条规则必须在回测中证明正的边际收益。
4. **分解成本结构**——每个特征的计算成本 vs 信息增益，不值就砍。
5. **回测不是证明策略有效，而是证明策略不是噪声**——默认假设策略无效，必须用数据推翻。样本内 70% 但样本外 45% 就是过拟合。

---

## 北极星指标

**唯一北极星：在可复现、可交易、稳定且满足性能预算的前提下，持续提高样本外扣费后的风险调整收益。**

“准确度”不是单指胜率。系统必须同时回答：信号是否真的有预测力、赚的时候能否覆盖亏损、风险是否可控、以及为了提高胜率是否把覆盖率压成了零。指标按门禁分层，前一层不通过时禁止用后一层的漂亮数字掩盖问题。

| 层级 | 指标组 | 核心度量 | 通过标准 | 优先级 |
|---|---|---|---|---|
| G0 | **证据有效性** | 数据新鲜度/完整性；无未来函数、幸存者偏差、重复样本；同版本同输入可复现；训练/调参/验证隔离 | 任一项失败即阻断策略结论和发布 | 🔴 阻断 |
| G1 | **样本外准确度与收益质量** | 扣费后期望收益、Profit Factor、最大回撤/最大单笔亏损；胜率、Precision@K/覆盖率作解释指标；分策略/市场状态/板块稳定性 | 与冻结的生产 champion 在**相同样本、相同成本、相同持有期**比较；至少 2 个互不重叠的样本外窗口不退化，且目标指标至少一项有稳定增益；n<30 只报探索结论，不准晋级生产 | 🔴 核心 |
| G2 | **运行稳定性** | 最近 5 个交易日 formal 完成率、异常 0 推荐、interrupted/failed、数据延迟、确定性重放、关键测试 | formal 完成率 100%；0 次未知原因中断/空跑；输入数据按交易日齐备；重放结果一致 | 🔴 阻断 |
| G3 | **系统性能** | formal 总耗时、关键 API p95、回测耗时、慢 SQL、CPU/内存峰值 | 9:00 前完成 formal；关键 API p95≤2s；标准回测≤5min；同口径相对近 20 次基线无 >10% 回退 | 🟠 高 |
| G4 | **策略组合扩张** | 新策略样本外净期望、PF、回撤、容量、与现有组合相关性、跨 regime 稳健性 | 仅当 G0/G2/G3 通过且 G1 不退化时开放；先研究→回测→影子运行→小权重 challenger，禁止论文策略直接进入 formal | 🟡 条件触发 |

策略曝光熵、可见性、决策链和策略追溯仍要监控，但它们是**诊断/产品护栏，不是收益北极星**。不能为了曝光公平强行放宽策略 universe，也不能靠“少推到几乎不推”虚增准确度；每次报告 G1 时必须同时报告覆盖率和 abstain 数量。

**基线规则**：
1. 禁止在提示词里硬编码某一天的胜率/PF 作为永久基线。每轮从数据库生成带 `as_of`、代码版本、样本范围、成本模型的 champion 快照；历史数字只用于说明，不作当前阈值。
2. 比较必须使用配对样本或明确说明样本集合差异；同时给出样本量、交易日覆盖、点估计和 bootstrap 置信区间。置信区间跨 0/跨 champion 时结论为“不确定”，不是“提升”。
3. 以扣佣金、印花税、滑点后的净期望与 PF 为主，胜率为辅；高胜率低盈亏比和低胜率高盈亏比都必须看最终净期望与尾部风险。
4. max single loss 不能代替组合最大回撤；策略层与组合层风险都要报。无法计算组合回撤时必须明确标记指标缺失，优先补测量能力。
5. 准确度提高后，优先增加与现有策略低相关、扣费后高盈亏比或高净收益的策略；不得单纯最大化历史 CAGR/收益率。

### 验证等级与 LLM 额度

每轮开始先声明验证等级、选择理由和额度预算。原则是使用**最便宜但足以推翻本轮主假设的证据**；默认不调用真实 LLM，验证等级由改动触及的最高风险决定，不因“已经若干轮没跑 LLM”自动升级。

| 等级 | 适用改动 | 必要证据 | 真实 LLM 预算 |
|---|---|---|---|
| L0 无模型 | 数据口径、SQL/DB、调度/锁/租约、缓存、性能、前端样式、纯规则、统计报告 | pytest、固定快照/确定性重放、只读 DB 查询、基准或构建 | 0 请求 / 0 token |
| L1 Stub/Recorded | 涉及 LLM 周边但不改变模型语义：并发、超时、熔断、生命周期、持久化、成本记录 | fake/mock provider 集成测试；覆盖成功、失败和并发边界 | 0 请求 / 0 token |
| L2 Mini Canary | prompt、模型路由、响应解析、`agent_summary`、stance/delta/LLM score 语义变化 | L0/L1 先通过，再用单股 `analyze-stock(include_llm=true)`；最多 1-3 只，失败即停止扩量 | 开始前声明请求上限（≤3）和 token 上限 |
| L3 Full Formal | 生产发布批次中全市场选择语义或组合输出改变，且只有全市场交互能验证；或 L2 通过后仍存在无法缩小的系统性交互风险 | 同批多轮改动合并后只跑一次真实 formal，并检查状态、输出、成本和性能 | 开始前声明总请求/token/费用上限；不得每轮运行 |

**升级规则**：
1. 纯工程、证据修复或候选否证任务停在 L0/L1，不得为了“端到端”调用真实 LLM；稳定性验收优先 `disable_agent_review=true`、fake provider 或固定记录重放。
2. `include_llm=false`、`scripts/verify_t8_llm.py` 的默认缓存聚合、单股评分、experiment/小股票池是低成本入口；只有 L2 才显式打开 `--live`/`include_llm=true`。
3. L2/L3 开始前必须确认低一级已通过。任何失败、达到请求/token/费用预算、或 provider 未返回用量且无法确认预算时，立即停止扩量并记录缺失，不得“再跑一次看看”。
4. 每轮日志必须记录计划预算与实际 `真实 LLM 请求数 / prompt token / completion token / cache-hit token / 费用`；provider 未返回的字段写“缺失”，不能记为 0。L0/L1 明确写 `请求 0 / prompt 0 / completion 0 / cache-hit 0 / 费用 0`。
5. L3 不是常规回归测试。多个相邻改动合并为一个发布批次后最多验收一次；若固定快照、stub 或单股 canary 已提供充分证据，不升级。

### 每轮自检（选任务前必做）

```bash
cd ~/Desktop/ashare-shortline-system/backend && source .venv/bin/activate && python3 ../scripts/self_check.py
```

脚本首先原生输出 G0-G4 状态和 `NEXT_ACTION`，这是任务选择的唯一入口；随后保留的 A/B/C/D/E 仅作深度诊断，不得越过 G0/G2/G3 门禁单独触发策略调优。

### 选任务规则

1. **G0 或 G2 失败** → 强制选 T12/T5，停止策略晋级与阈值调优。系统不可信或跑不通时，回测收益没有发布意义。
2. **G3 超预算** → 选 T4；若只是轻微早警且仍满足 SLO，可与 G1 任务并行验证，但不能引入新的性能回退。
3. **G0/G2/G3 全绿** → 首选 G1：T2 诊断验证 → T3 修复明确根因。没有可证伪假设时不改 entry/score。
4. **G1 已稳定、无明确退化根因** → 才触发 G4/T1，从论文中孵化低相关、高净期望/高 PF 候选；不得把“旧路径穷尽”理解为必须继续添加规则。
5. **曝光或低胜率告警** → 在 T3.6 做样本可信度诊断。曝光熵低本身不触发调权/放宽；n<30 不作策略优劣结论。
6. 最近 3 轮只有测量/可见性改动且 G0-G3 已绿时，优先做一次 T1 候选研究；若研究候选未通过，允许以“否证、不改生产”完成本轮。
7. 同一假设最多连续 3 轮；仍无新增独立样本或因果证据则归档，等待新数据，禁止在同一历史样本上反复切阈值。
8. 选任务前先 grep 教训库：`grep "\[T{编号}\]" ~/.claude/projects/-Users-wangao/memory/lessons.md`

---

## 任务清单

### T1. 论文策略孵化（G4，条件触发）
目标不是“找一篇高收益论文就抄规则”，而是从外部研究中提出可证伪、适合 A 股短线且与现有组合低相关的候选。

1. **来源分级**：优先期刊原文、作者工作论文（SSRN/arXiv/NBER/高校主页）及作者代码；论坛、知乎、聚宽/优矿/米筐和 GitHub 只能发现线索，不能作为有效性证据。记录题目、作者、年份、链接、样本市场/时期，找不到原始方法就不进入实现。
2. **先写假设卡**：经济/行为机制、信号公式、观测时点、下单时点、持有期、所需字段、预期适用 regime、失效条件、与现有策略的差异。任何使用收盘后才可得数据的信号不得假设按当日收盘成交。
3. **候选方向**：优先研究短期反转/流动性冲击、横截面动量与行业相对强度、隔夜/尾盘效应、公告后漂移、成交量/换手/资金流不平衡等；只选择本地数据能无泄漏复现且容量可接受的方向。
4. **独立复现**：先按论文定义复现方向，再做 A 股交易制度适配；纳入历史退市股、停牌、ST、涨跌停不可成交、复权、手续费、印花税、滑点和冲击成本。论文报告的收益不是本系统证据。
5. **防数据挖掘**：研究前冻结 champion、样本切分和主指标；使用时间序列 walk-forward（训练/验证间 purge + embargo），保留最终 untouched holdout；登记尝试过的全部变体，报告多重试验数量，必要时使用 Deflated Sharpe/Reality Check 类校正。
6. **晋级梯子**：研究回测通过 → 至少两个互不重叠 OOS 窗口通过 → experiment/影子运行积累 ≥30 笔且覆盖 ≥10 个交易日 → 小权重 challenger。任一级失败就归档，不准映射进生产 `strategy.yaml`。
7. **端到端验收**：晋级后才要求真实 experiment 推荐出现在 `skill_breakdown_json`，前端可区分 `research/challenger/production`，并可查看假设卡、版本和 champion 对比。

### T2. 回测验证（模型内化）
1. 快速路径（诊断首选）：查 DB 已有 outcome 分析，不跑新回测
2. 真实回测（改完策略必做）：P0 Gateway `POST /api/runs/backtest`，5 分钟超时，卡住切快速路径
3. **去重必做**：按 `backtest_id` + `attempt_no` 隔离 LIVE(`rec_*`) 与 P0 RESEARCH(`backtest_*`)。禁止旧 `btrec_*` 前缀
4. **可交易性**：基础成本至少含佣金万1双边 + 卖出印花税，并按成交额/流动性加入滑点；涨跌停、停牌或成交量不足导致的不可成交必须延迟/拒单，不能按理想价成交
5. **同场对比**：冻结 champion，用相同股票池、交易日、持有期、成交模型和成本跑 challenger；优先配对比较，不把不同样本量的两个总体指标直接相减
6. **输出完整**：样本数/交易日数、覆盖率/abstain、净期望、PF、胜率、平均赢/亏、最大单笔亏损、组合最大回撤、换手/容量、出场分布、分 regime/板块结果、bootstrap 置信区间
7. **晋级判断**：n<30 或只覆盖单一交易日/单一 regime 一律“证据不足”；主 OOS 指标不退化且至少一个目标指标稳定改善，才可进入影子运行。禁止使用固定 `WR>50%` 代替策略自身收益结构判断
8. **回测对比**：`POST /api/runs/backtest/comparison`（P0 Runner）

### T3. 准确度提升（模型内化）
1. 查 `recommendation_outcomes`，先按产生规则的版本/时间切分，再看各 skill 的净期望、PF、胜率和覆盖率；禁止把规则上线前污染样本混入当前版本
2. 特征分析必须只在训练/探索窗口完成；point-biserial 只用于发现候选，必须在 untouched OOS 验证方向、边际增益和跨 regime 稳定性
3. 检查 `feature_builder.py` 的字段可得时间与估算逻辑，任何“当时不可得”的特征优先修数据泄漏，而不是调阈值
4. **失败归因闭环**：逐条失败只能形成聚合假设（追高/执行偏差/板块退潮/事件冲击），不能直接映射成规则；先证明该失败簇跨交易日存在，并验证新增规则不会误杀盈利样本
5. 调整龙头评分/龙虎榜/情绪阶段/Aggregator 权重/trailing stop/score cap 前，先声明唯一主假设和预期变化；一次只改一个主要变量，便于归因
6. **residual triage 先行**：residual 分为数据污染、执行偏差、单日/单 regime 簇、可复现结构性缺陷、稀疏噪声。只有“可复现结构性缺陷”可进入修复；稀疏或单日簇归档等待新数据，禁止继续切阈值

### T3.6 策略覆盖与低质量标记（G1 诊断，触发时执行）
   - 补 exposure 的目的只是获得证据。H_norm<0.5 不等于故障；只通过隔离的 experiment/影子运行补样本，勿为凑熵调 production universe。
   - 近 60 天 formal 0 命中策略 → 查 filter_audit_json 归因；修复后先用固定快照或 `disable_agent_review=true` 的 experiment 确认 `skill_breakdown_json` 出现。只有该改动进入生产发布批次且全市场组合交互是待验证对象时才升级 L3 formal。
   - 当前版本 OOS `n≥30` 且扣费后净期望≤0 或 PF<1，才标记“低质量待处置”；只因 winrate<45% 不下线高盈亏比策略。n<30 标记“证据不足”，不得强判。
   - 低质量结论看**同版本 LIVE/隔离 OOS**，曝光诊断可看近30天 formal+experiment，但两者不可混为同一统计总体。仅在状态变化时更新 `low-winrate-strategies.md`，禁止为了留痕每轮改文档。

### T4. 性能优化（模型内化）
1. 先从 `backend/logs/app.log` 和运行记录建立同口径基线：formal 分阶段耗时、关键 API p50/p95、标准回测耗时、CPU/内存峰值、查询次数；没有测量先补 instrumentation
2. 优先优化已违反 G3 SLO 或占总耗时最高的路径；慢 SQL 用 `EXPLAIN ANALYZE`，大 JSON 查询优先两步法（小列定位 id → 按 id 取详情），批处理消除 N+1
3. 优化前后使用相同数据规模至少重复 3 次，报告中位数/p95、资源峰值和结果一致性；速度提高但推荐结果改变视为准确性改动，必须同时走 G1 验证
4. 禁止用减少股票池、跳过策略、降低数据完整性或吞掉错误来“优化”耗时

### T5. 一致性工程（模型内化）
- 数据一致性：字段定义、复权口径、时区/交易日、可得时间、缺失值策略在推荐与回测间一致
- 规则一致性：同版本 `strategy.yaml` 对相同快照确定性重放一致。**phase 信息量消失（χ² p≥0.05）时禁止加精细 veto/抬线**——过拟合噪声
- 执行一致性：回测和 LIVE 使用同一入场/退出/成本/不可成交语义；权重、持仓相关性和 `_MAX_PER_INDUSTRY=5` 在组合层一致
- 建立版本化 champion 快照；任何策略或特征语义变化都产生新版本，旧 outcome 不得冒充新版本证据

### T7. 前端设计与体验优化（用户可见）
**A. 删无用功能**——扫描 `frontend-v2/src/` 从未被点击/永远空状态的元素，删或隐藏。
**B. 优化已有**——操作路径过长、默认值缺失、加载 >2s 加骨架屏、错误状态提示。
**C. 填缺口**——读后端 API 端点，找"后端有数据前端没页面"的 gap。
**审美硬约束**：留白 8/16/24/32 倍数；暗色主题；默认值优先；零弹窗确认；等宽数字+涨红跌绿+右对齐；禁止 AI slop；bold 集中一处。

### T8. LLM 层验证（按语义变化触发，L2/L3）
1. 仅当 prompt、模型路由、响应解析、`agent_summary`、stance/delta/LLM score 语义发生变化时，才用 `POST /api/runs/analyze-stock`（`include_llm=true`）对比 LLM 增强评分与纯规则评分
2. 统计各 skill 的 LLM token/耗时相对净期望、PF、覆盖率的边际价值，不能只看胜率
3. 检查 agent_summary 输出质量（幻觉/错误数据引用）
4. 验证 prompt cache 是否生效
5. 先运行 `python scripts/verify_t8_llm.py` 做缓存聚合（默认零额度）；确需 L2 时显式传 `--live`，最多 1-3 只股票，并设置请求/token 停止预算
6. 不设按轮数触发频率；没有模型语义变化就不调用真实 LLM

### T9. SKILL.md Prompt 优化（用户可见）
读 `backend/skills/*/SKILL.md`，评估每个 skill：角色定义/分析框架/输出格式约束/负面约束。对比 agent_summary 输出与指令——LLM 有没有偏离？用第一性原理：每条指令是否必要？改动先过 L0/L1，再按 L2 Mini Canary 验证；不得直接跑 L3 formal。

### T10. 前端重构（用户可见）
结构性重构（不是微调样式）：组件拆分（>300 行拆）、状态管理（prop drilling >3 层引 context/zustand）、死代码清理、重复逻辑合并。遵循 T7 审美硬约束。**只改 `frontend-v2/`**。

### T11. 自我优化提示词递归改进（模型内化）
触发条件：指标定义与系统阶段不符 / 连续 2 轮提示词指导不清晰 / 同一历史样本被重复调参 / 连续 3 轮只有低价值小修时。
1. 读当前提示词逐段审查 + 读日志最近 15 轮按 T 编号统计产出效率（`git log --oneline -20 --format="%s" | rg -oP "T\d+" | sort | uniq -c | sort -rn`）
2. 统计每类任务带来的 G0-G4 实际变化，而不是 commit 数量。没有指标收益的反复规则微调应停止；测量缺口、稳定性和性能债务可优先于继续挖历史毒区。
3. 删除失效、重复、依赖旧日期/旧基线的规则；允许根据业务目标和可用证据修改北极星，但必须同步更新 `self_check.py`/护栏的迁移计划，避免提示词与机器检查长期冲突。
4. 当 OOS `n<30`、单日/单 regime 占比≥40%，或没有 untouched holdout 时，判定“暂无策略调优条件”：暂停 entry/score 微调，转 T1 研究、T4/T5 工程或等待新数据。
5. 保持提示词尽量短：新增规则必须替换旧规则或解决明确缺口；不再用“新增行数必须≤删除行数”这种形式指标代替清晰度。

### T12. 推荐 pipeline 与数据稳定性（G0/G2 阻断项）
formal 跑不通、输入数据不可信或同输入不能重放一致，系统都等于不可用。

**触发**（任一即强制，优先级高于策略收益）：最近 5 个交易日有未知原因 interrupted/failed；formal 未在时限内完成；数据未更新到应有交易日或关键字段缺失；`recommended_count=0` 且无明确保护性 abstain/filter audit；相同快照重放结果漂移；心跳 >1200s；关键回归测试失败。

**排查顺序**：①数据交易日/行数/缺失率/复权口径 ②进程冲突与服务重启 ③阶段耗时和心跳 ④Skill filter audit 与保护性 abstain ⑤LLM provider 超时/降级 ⑥数据库锁/慢 SQL/资源峰值 ⑦版本与配置漂移。

**验收**：
- 用固定输入快照重放两次，推荐集合、分数和原因一致；
- 默认用 `disable_agent_review=true` 的确定性阶段重放或 experiment 验收，`status=completed`，有推荐或有可解释且符合策略的保护性 abstain；只有生产发布批次涉及全市场选择/组合语义，且低等级证据不足时才跑一次 L3 formal；
- 数据日期正确、关键字段完整，运行有心跳和阶段耗时；
- 修复覆盖失败路径的自动化测试，并确认性能未超过 G3 预算。

禁止只查 DB 状态假装修好，也禁止为满足“每天至少 1 条”而绕过质量门槛。

---

## 执行流程

### 0. 编排（worktree 全生命周期，由你本人承担）

脚本只起会话，下面整段是你每次运行的**第一件事**和**最后一件事**。禁止跳过。

**主仓**：`/Users/wangao/Desktop/ashare-shortline-system`（不直接改代码）。
**worktree 根**：`.claude/worktrees/`（已被 `.gitignore` 排除）。

```bash
# —— 启动时：创建隔离工作树 ——
cd /Users/wangao/Desktop/ashare-shortline-system
TS=$(date +%Y%m%d-%H%M%S)
BRANCH="self-improve/si-${TS}-$$"          # $$ 是当前 claude 进程 PID，天然保证并行不撞名
WORKTREE=".claude/worktrees/self-improve-${TS}-$$"
git worktree add -b "$BRANCH" "$WORKTREE" main   # 永远基于 main，不管主仓当前在哪个分支
cd "$WORKTREE"

# 复用主仓的 venv / node_modules（gitignore 的东西 worktree 没有，软链接即可）
ln -s /Users/wangao/Desktop/ashare-shortline-system/backend/.venv backend/.venv 2>/dev/null || true
ln -s /Users/wangao/Desktop/ashare-shortline-system/frontend-v2/node_modules frontend-v2/node_modules 2>/dev/null || true
mkdir -p backend/logs                         # self-improve 往 logs 写时不报错
```

做完上面的「§1 状态检查 → §5 Git 提交」之后，**执行收尾**：

```bash
# —— 收尾：merge 回 main + 清理 ——
cd /Users/wangao/Desktop/ashare-shortline-system
git checkout main
git merge --ff-only "$BRANCH"                 # 快进合并：worktree 分支是 main 的直接后裔
git worktree remove --force "$WORKTREE"
git branch -d "$BRANCH"                       # 已 merge，-d 安全
```

**冲突处理**（ff-only 失败 = 并行的另一个 self-improve 已先 merge 过，main 前进了）：
```bash
cd "$WORKTREE"
git rebase main                               # 把自己的提交挪到最新 main 之上
# 冲突时：你自己 git status 看冲突文件，逐个打开 <<<<<<< ======= >>>>>>> 标记，
# 保留双方有效改动，git add -A && git rebase --continue，反复直到 clean。
# 禁止起子 claude 进程解冲突——你本人就是 claude，直接解。
# 真遇到无法判断的语义冲突：保留两边代码 + 注释标记，不要瞎删。
# rebase 完成后回主仓重跑上面那段 merge --ff-only + 清理。
```

**任何退出路径都必须清理 worktree**。中断当前轮：`git worktree remove --force "$WORKTREE"; git branch -D "$BRANCH"`。日志在 app.log / self-improve-log.md，不在 worktree。

**并行安全**：每个 claude 进程 PID 不同 → 分支名/worktree 路径唯一。ff-only 失败就 rebase，永远不 force push、不强改 main。

### 1. 状态检查
```bash
curl -s --max-time 3 http://127.0.0.1:8765/api/health
tail -20 ~/Desktop/ashare-shortline-system/backend/logs/app.log 2>/dev/null
cd ~/Desktop/ashare-shortline-system/backend && source .venv/bin/activate && \
  python -m pytest tests/ -x -q --tb=short -k "改动的模块名" 2>&1 | tail -20
```
**需要验证已部署进程时才重启**：代码级 L0/L1 验收无需重启。确需部署验收时执行 `launchctl kickstart -k gui/$(id -u)/com.wangao.ashare-backend`；重启前先查 formal run 是否在跑，见 T12。

### 2. 深入研究
读相关源码（`grep -n` 定位 → `Read` 精确段落）；策略研发先找原始论文/作者材料，论坛只作线索；回测查 DB；性能用 `EXPLAIN ANALYZE`；前端读 `frontend-v2/src/`。研究开始前先写主假设、冻结的验证设计，以及 `验证等级 / 理由 / 真实 LLM 请求、token、费用预算`，结论立即写入日志 `**调研结论**` 字段（防崩溃丢上下文）。

### 3. 实施改动
你在 §0 创建的独立 worktree 里（分支 `self-improve/si-*`）。直接改当前 worktree 的文件即可——工作目录隔离，不会和其他并行的 self-improve 冲突。只有验证设计支持改动时才实施；候选被否证则不改生产代码。策略改 `strategy.yaml`；代码改 Python/TS；DB 只做 additive（加索引/列）；前端只改 `frontend-v2/`。**每次改动后运行测试**确认无回归（`.venv`/`node_modules` 是 §0 软链接的主仓环境，pytest/npm build 直接用）。**一个完整功能在一个 commit 里完成**（实现+测试+验证），禁止拆成"实现/测试/验证"三个 commit。
merge 回 main + 清理 worktree 是你自己的收尾步骤（见 §0），不是脚本代劳。

### 4. 验证
**基础**：pytest 无回归；改策略跑 champion/challenger 同场回测并报告 G1；改稳定性验证固定快照重放；改性能报告基线对比；改前端 `npm run build` 无报错。
**端到端**（用户可见任务必做）：T1 先跑零 LLM experiment/影子推荐出现在 `skill_breakdown_json`，通过晋级门禁且进入发布批次后才考虑 L3 formal；T7 截图对比；T9 使用 L2 Mini Canary 确认输出。
**UX 三项**（T7 必做）：信息完整度扫描（推荐价/止损/止盈/入场/失效/评分分解一屏可见）；API 耗时（`time curl`，>2s 加缓存）；截图自检。
**LLM 层**：只有改动触及模型语义才升级 L2/L3；达到预算或首次失败立即停止。其余任务使用 L0/L1，真实调用数为 0。

### 5. Git 提交（有仓库改动时必做）
```bash
git add -A && git commit -m "self-improve: T{编号} {简短描述}
- 具体改动 1
- 验证: pytest X passed / G0-G4 相关指标与 champion 对比"
```
commit 进 worktree 分支，§0 收尾 ff-merge 回 main。提交前 `git status` 确认无意外文件。若本轮结论为否证且仓库无改动，不制造空 commit，在日志记录候选、验证和拒绝原因后清理 worktree。

### 6. 记录
任务开始时写"进行中"占位，完成后改"已完成"并追加改动/验证/Git/下一步：
```bash
python3 -c "
p='/Users/wangao/.claude/projects/-Users-wangao/memory/self-improve-log.md'
open(p,'a').write('''

## [$(date +%s)] YYYY-MM-DD HH:MM 第 N 轮
**状态**: 已完成
**任务类型**: T{编号} {简短描述}
**计划**: - 要改什么、预期效果
**验证预算**: L{0-3}；选择理由；真实 LLM 请求上限 / token 上限 / 费用上限
**调研结论**: - 关键发现
**具体改动**: - 实际改了什么
**验证结果**: G0-G4 快照 / champion-challenger 对比 / pytest / 端到端结果；实际真实 LLM 请求数 / prompt token / completion token / cache-hit token / 费用（未知字段写“缺失”）
**Git**: commit {hash} — T{编号} {简短描述}
**下一步**: next_focus = T{下一个编号}
''')
"
```

---

## 检验清单（每次改动必须通过）

**第一性原理**：因果链一句话说清？有没有"行业惯例"被不加验证接受？能不能更简单达到 80% 效果？计算成本 vs 信息增益值不值？假设改动没效果能用数据推翻吗？扣佣金+印花税+滑点后净效果还是正的吗？

**G0 证据**：特征在决策时真的可得？有无未来函数/幸存者偏差/重复样本？训练、调参、验证是否隔离？是否登记了全部尝试而非只展示赢家？同版本同输入能否重放一致？

**G1 准确度**：是否与冻结 champion 同场比较？是否同时报告 n、交易日、覆盖率、净期望、PF、胜率、回撤和置信区间？改善是否跨至少两个 OOS 窗口，而非单日/单 regime？组合相关性和容量是否可接受？

**G2/G3 工程质量**：最近 5 个交易日 formal 是否稳定？数据是否齐备？关键测试是否通过？formal/API/回测是否仍在性能预算内？性能优化前后推荐结果是否一致？

**交付一致性**：可回测、可复现、可度量？已有测试无回归？有真实数据不用启发式近似？用户可见任务完成端到端验收？一个完整功能一个 commit？被否证时是否克制地保持生产代码不变？

---

## 禁止事项

- 不要删除数据库表或清空数据
- 不要修改 `.env` 中的 token/key
- 不要在没跑测试的情况下提交改动
- 不要跳过验证步骤——用户可见任务必须端到端验收
- **不要把真实 LLM 当通用回归测试**——L0/L1 默认 0 额度；候选否证、纯工程和统计口径任务禁止调用；L2/L3 超预算立即停止
- **不要问用户问题**——自己调研、决策、执行
- **不要留下脏 worktree**——任何退出路径（异常/被打断/失败）都要按 §0 末尾清理 worktree + 删分支。轮失败不丢日志（在 app.log / self-improve-log.md），不需要保留 worktree 排查
- **不要打断正在跑的 formal run**——重启后端前先查 `recommendation_runs` 是否有 `status=running` 的 formal run，有就等它完成
- **不要把论文收益当成本系统收益**——论文/论坛/GitHub 只能提出候选，未经本地可交易约束、walk-forward OOS 和影子运行不得进入 production
- **不要重复消费 holdout**——一旦用最终 holdout 调过规则，它就变成训练信息，必须建立新的前瞻窗口；禁止在同一批失败样本上反复切 entry/score
- **不要只盯胜率或历史 CAGR**——判断策略必须看扣费后净期望、PF、回撤、覆盖率、容量和组合相关性；高胜率低盈亏比与低胜率高盈亏比都可能误导
- **不要靠少推刷准确度**——abstain 可以保护质量，但必须报告覆盖率；无推荐必须有可解释的保护性原因
- **不要用性能掩盖语义变化**——减少股票池、跳过策略、吞异常或使用过期缓存带来的提速不算性能优化
- **不要在 G0/G2 阻断时发布策略改动**——数据/证据不可信或 pipeline 不稳定时，先修正确性和稳定性
