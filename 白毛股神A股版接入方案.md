# 白毛股神 A 股版 · Agent 模式接入方案

> 项目：`~/Desktop/ashare-shortline-system`
> 模式：dual（规则粗筛 + Agent 精审）
> 数据源：Tushare 15000 积分（fina_indicator 已接入 ✓）
> 日期：2026-07-05 初稿 / 2026-07-06 据 fina_indicator 实际实现修订
> 状态：fina_indicator 数据层已接入，方案据实际实现修订中

---

## 一、策略定位与本土化映射

### 原版白毛股神（Serenity）
美股/欧股，产业链瓶颈猎人，中长期持有，定性研究为主，小盘高壁垒+并购潜力，高波动容忍。

### A 股映射版（bottleneck-hunter-a-share）
把"瓶颈猎人"哲学翻译成 A 股可执行框架——**国产替代卡脖子环节的小盘高壁垒成长股**。

| 白毛原则 | A 股映射 | 数据支撑 |
|---------|---------|---------|
| 物理瓶颈而非概念热点 | 国产替代卡脖子环节（半导体材料/设备/高端制造/专精特新） | 概念板块 `ths_index` + 主营业务 |
| CPO 等新兴架构主线 | 当前产业主线（AI 算力/先进封装/新材料/新能源卡脖子） | `sector_ladder_*` + 概念热度 |
| 小盘+高壁垒+并购潜力 | 自由市值<200 亿 + 毛利率>40% + ROE>15% | `free_mv_yi` + `gross_margin` + `roe`（**需补 fina_indicator**） |
| 长期持有+高波动容忍 | A 股波段/中线（1-4 周，系统需扩展 holding_days） | `meta.holding_days` |
| 独立验证+跨链路证据 | Agent 定性审查（壁垒强度+催化剂+证伪信号） | SKILL.md + Agent 工具 |

**取舍声明**：A 股没有美股的小盘并购逻辑，本土化时弱化"并购潜力"，强化"国产替代/政策催化/业绩验证"。这是有损映射，不是原版复刻。

---

## 二、架构选择：dual 模式

### 为什么不纯 preset（纯规则）
白毛精髓是定性研究（瓶颈层级判断、技术壁垒评估、催化剂研判），规则化会失真。`evaluator.py` 的安全 AST 表达式只能做数值比较，表达不了"这家公司是不是产业链瓶颈"。

### 为什么不纯 agent
白毛策略需要先用基本面硬筛（小盘+高毛利+高 ROE），再让 Agent 做产业链定性判断。纯 agent 会浪费 LLM 算力在明显不符合的标的上，且 15000 积分的 Tushare 数据量很大，全交给 Agent 反而低效。

### dual 分工
- **strategy.yaml（规则层）**：用已有字段做硬筛 + 基础打分。只放"能用数值客观判断"的条件。
- **SKILL.md（Agent 层）**：白毛方法论本土化的定性审查。只放"需要产业链判断"的条件。
- 规则先跑，把 5000+ 标的筛到几十只，Agent 再精审。`agent_skill_runner.py` 的 `enrich_signal_with_agent_review` 会在规则分上加 delta（-18~+16）。

---

## 三、数据层改造

### 现状（2026-07-06 实读代码）

fina_indicator 已接入，链路完整、字段映射正确，不是假的接入：

| 层 | 文件 | 实现情况 |
|----|------|---------|
| 拉取层 | `tushare_client.py:1526` `fetch_fina_indicator(code, limit, trade_date)` | ✓ 单股拉取，缺失字段保留 None（不伪装成 0），用 `_safe_float` 区分"无数据"和"真实 0" |
| 拉取层 | `tushare_client.py:1577` `fetch_fina_indicator_batch(trade_date, limit)` | ✓ 全市场按公告日批量拉取，适合 backfill |
| 模型层 | `tables.py:446` `FinaIndicator` | ✓ 字段对齐 Tushare 原始命名（`grossprofit_margin` 无下划线），upsert by (code, end_date) |
| 装配层 | `market_pool.py:2301` `_load_fina_indicator_for_stocks` | ✓ 本地查最新一期已披露，缺失时按需从 Tushare 拉取入库；字段名做了转换 `grossprofit_margin→gross_margin`、`or_yoy→revenue_yoy`、`netprofit_yoy→net_profit_yoy`，feature_builder 直接读转换后的名字 |
| 注入层 | `feature_builder.py:925-950` | ✓ fina_map 注入 profile，并标注 `has_real_fundamental` / `fina_end_date` / `fina_ann_date` |

**字段映射链路（完整核对）：**
```
Tushare 原始字段        → FinaIndicator 表列        → _load_fina 转换后键   → feature_builder profile 键
roe                    → roe                       → roe                  → roe                  ✓
grossprofit_margin     → grossprofit_margin        → gross_margin         → gross_margin         ✓
netprofit_yoy          → netprofit_yoy             → net_profit_yoy       → net_profit_yoy       ✓
or_yoy                 → or_yoy                    → revenue_yoy          → revenue_yoy          ✓
netprofit_margin       → netprofit_margin          → netprofit_margin     → （未注入 profile）
eps                    → eps                       → eps                  → （未注入 profile）
bps                    → bps                       → bps                  → （未注入 profile）
```

白毛策略硬筛 4 字段（`roe` / `gross_margin` / `revenue_yoy` / `net_profit_yoy`）的字段映射链路是通的，**没有命名 bug**。

### 已实现中需要修的 5 个问题

虽然链路通了，但实际实现有 5 处会影响白毛策略精度，必须改：

#### 问题 1：`fetch_fina_indicator_batch` 的 `limit` 是死参数

`tushare_client.py:1589`：
```python
df = pro.fina_indicator(ann_date=target)   # limit 参数根本没传
```
注释说"返回最近 limit 期"，实际 limit 形同虚设。Tushare `fina_indicator(ann_date=X)` 返回的是该日公告的全部记录（每只股票一行，刚好一期），limit 语义本来就接不上。

**修法**：去掉 `limit` 参数，或改为 `periods` 语义（拉取该日公告 + 往前 N 期）。

#### 问题 2：`fetch_fina_indicator` 单股的 trade_date 过滤顺序有偏差

`tushare_client.py:1547-1557`：
```python
df = pro.fina_indicator(ts_code=tsc, limit=limit)   # 先拉最新 limit 期
if target:
    df = df[df["ann_date"].astype(str) <= target]   # 再过滤
```
Tushare `fina_indicator` 拿的是"截至今天最新 limit 期"，再用 `ann_date<=target` 过滤。回测场景下，如果 target 是半年前，过滤后可能只剩 1-2 期甚至 0 期（因为最新 4 期都在 target 之后）。

**修法**：用 Tushare 的 `period` 参数（end_date 的 YYYYMMDD）按期倒推拉取，或在批量拉取后入库时一次性把全历史持久化、查询时直接走本地。

#### 问题 3：缺关键字段（影响 Agent 定性判断精度）

白毛策略 SKILL.md 数据审计要"高毛利是否有技术壁垒支撑"，但当前 fina 没拉研发费用，Agent 没数据可判。建议补：

| 缺失字段 | 用途 | Tushare 权限 |
|---------|------|-------------|
| `rd_exp` | 研发费用，算研发费用率 → 判断"高毛利是否有技术壁垒" | 2000 积分，已满足 |
| `q_profit_yoy` | 单季归母净利同比 → 识别业绩拐点（累计同比会掩盖单季滑坡） | 2000 积分 |
| `update_flag` | 财报更新标志（1=本期最新）→ 避免拉到旧版快报 | 2000 积分 |
| `debt_to_assets` | 资产负债率 → 小盘成长股风控 | 2000 积分 |

#### 问题 4：`feature_builder.py:932` `profile["pe_ttm"] = fina.get("pe_ttm")` 是死代码

`fina_indicator` 接口本身**没有 pe_ttm 字段**（pe_ttm 在 `daily_basic` 里），`fina.get("pe_ttm")` 永远是 None，会被 `_apply_context_blocks:253` 的 `_num(profile.get("pe_ttm", ...), fallback)` 兜底回默认值。

**修法**：删掉这行。pe_ttm 已经由 `fetch_daily_kbars` 从 `daily_basic` 拉取并走 item["pe_ttm"] 通道注入，不需要 fina 重复提供。

#### 问题 5：`has_real_fundamental` 判定阈值偏低

`feature_builder.py:938-941`：
```python
has_real_fina = any(
    fina.get(k) is not None
    for k in ("revenue_yoy", "net_profit_yoy", "roe", "gross_margin")
)
```
只要 4 个里**任意一个**非 None 就算 `has_real_fundamental=True`。但白毛策略硬筛 4 个全要，只拉到 roe 其他都 None 时，evaluator 会把 None 当 0 处理，硬筛全失败。

**修法（白毛策略层）**：universe 加守门 `has_real_fundamental == true`（见第四章），不允许进入"基本面数据不全"的标的。`has_real_fundamental` 本身的判定保持现状（任一非 None 即真，给其他策略用），白毛策略在 universe 层面单独把守。

### 仍需新增的 1 件事：财报季预热

现状是"按需补齐"（load 时缺失才拉）。日常够用，但财报季（4/8/10 月底前后 2 周）集中披露，按需补齐会瞬间打爆 Tushare 90 次/分钟限速。

**修法**：财报季前后跑一次 `fetch_fina_indicator_batch` 全市场预热缓存，日常仍走按需补齐。不需要定时任务，手动或 cron 在 4/8/10 月底 +1 周各跑一次即可。

### 工作量

数据层主要工作已完成。剩余 5 个问题修一遍 + 1 个预热脚本 ≈ 半天。

---

## 四、strategy.yaml 设计（规则粗筛层）

### 字段映射表（全部用 evaluator 白名单已有字段）

| 白毛维度 | evaluator 字段 | 阈值 | 说明 |
|---------|---------------|------|------|
| **数据完整守门** | `has_real_fundamental` | `== true` | **白毛独有**：基本面数据不全的标的不进入 universe（避免 None 被当 0 误杀） |
| 小盘 | `free_mv_yi` | `< 200` | 自由流通市值<200亿 |
| 高壁垒 | `gross_margin` | `>= 40` | 毛利率>40%（fina_indicator 真实数据） |
| 高 ROE | `roe` | `>= 12` | ROE>12%（fina_indicator 真实数据） |
| 成长性 | `revenue_yoy` | `>= 15` | 营收增速>15%（fina_indicator or_yoy 真实数据） |
| 成长性 | `net_profit_yoy` | `>= 20` | 净利润增速>20%（fina_indicator netprofit_yoy 真实数据） |
| 估值安全 | `pe_ttm` | `>= 10 and <= 80` | 排除极端估值（来自 daily_basic） |
| 行业景气 | `industry_positive_excess20_count` | `>= 3` | 行业近20日正超额>=3天 |
| 板块梯队 | `sector_ladder_level` | `>= 1` | 至少在板块第一层 |
| 排除 ST | `is_st` | `== false` | 硬排除 |
| 排除风险 | `news_risk_count` | `== 0` | 无负面新闻 |
| 排除风险 | `announcement_negative_count` | `== 0` | 无负面公告 |

> **注**：`has_real_fundamental` 字段需确认是否在 evaluator 的 `ALLOWED_NAMES` 白名单内。若不在，需要加一行白名单注册（feature_builder 已写入 item，evaluator 能读到，只是表达式白名单要放行）。

### 结构草案
```yaml
id: bottleneck-hunter-a-share
name: 瓶颈猎人A股版
enabled: true
weight: 1.0

meta:
  author: system
  style: bottleneck-hunter-midline
  version: 0.1.0
  holding_days: [5, 10, 20]      # 中线，1-4周（需系统支持多周期）
  strategy_independence: 0.70
  default_preset_groups:
    experiment_bottleneck_hunter:
      weight: 1.0
      sort_order: 10

contract:
  type: strategy
  executable: true
  schema_version: 1

universe:
  include:
    - "has_real_fundamental == true"      # 数据完整守门：基本面字段不全的标的不入局
    - "free_mv_yi >= 5 and free_mv_yi <= 200"
    - "gross_margin >= 40"
    - "roe >= 12"
    - "revenue_yoy >= 15"
    - "net_profit_yoy >= 20"
    - "pe_ttm >= 10 and pe_ttm <= 80"
    - "is_st == false"
  exclude:
    - "announcement_negative_count > 0"
    - "news_risk_count > 0"

prefilter:
  market:
    include:
      - when: "industry_positive_excess20_count >= 3"
        reason: "requires_industry_momentum"
      - when: "sector_ladder_level >= 1"
        reason: "requires_sector_ladder"

scoring:
  base: 40
  rules:
    - when: "gross_margin >= 60"
      delta: 12
      reason: 高壁垒强确认
    - when: "gross_margin >= 40 and gross_margin < 60"
      delta: 6
      reason: 壁垒尚可
    - when: "roe >= 20"
      delta: 10
      reason: 高ROE
    - when: "net_profit_yoy >= 50"
      delta: 10
      reason: 高成长
    - when: "free_mv_yi <= 50"
      delta: 8
      reason: 小盘弹性大
    - when: "sector_ladder_level >= 2"
      delta: 8
      reason: 板块梯队完整
    - when: "pe_ttm > 60"
      delta: -10
      reason: 估值过高
    - when: "revenue_yoy < 15"
      delta: -15
      reason: 成长性不足

risk:
  rules:
    - when: "gross_margin < 30"
      level: high
      flag: 壁垒不足，可能非瓶颈环节
      score_delta: -15
    - when: "pe_ttm > 80 or pe_ttm < 5"
      level: high
      flag: 估值异常
      score_delta: -12
    - when: "net_profit_yoy < 0"
      level: high
      flag: 业绩负增长，证伪信号
      score_delta: -15
```

**注**：`setups` 和 `phases` 留给 Agent 层判断（瓶颈确认、催化剂、买卖时机），strategy.yaml 不写死。

---

## 五、SKILL.md 设计（Agent 定性审查层）

### 结构（仿 chen-xiaoqun-skill 模板）

```
# 瓶颈猎人 A 股版 · Agent 审查 Skill

## 角色定义
我是白毛股神 Serenity 的 A 股映射版，专注国产替代卡脖子环节的
小盘高壁垒成长股审查。用"瓶颈猎人"框架逐项审查。

## 核心原则（5条本土化）
1. 瓶颈优先于规模：不追龙头股，专找头部厂商离不开的上游卡脖子环节
2. 壁垒可验证：高毛利必须有技术壁垒支撑，不接受纯周期性高毛利
3. 成长可延续：高增长必须有产能/技术路线支撑，不接受一次性爆发
4. 催化剂临近：提前布局，但要在 1-2 个季度内有验证节点
5. 证伪即离场：逻辑证伪（技术路线变更/竞争加剧/业绩不达预期）无条件退出

## 核心心智模型（5个）
- 模型1：产业链层级定位（下游需求→中游制造→上游材料/设备，找最稀缺环节）
- 模型2：技术壁垒评估（专利/工艺/认证/客户粘性，高毛利的护城河）
- 模型3：催化剂日历（财报验证/产能投产/政策落地/客户突破）
- 模型4：估值容忍区间（小盘成长可容忍高估值，但要有成长性对冲）
- 模型5：证伪信号体系（技术路线变更/竞争加剧/业绩连续不达预期）

## 审查 Checklist（6项）
1. 瓶颈定位：是否处于产业链卡脖子环节？
2. 壁垒强度：高毛利是否有技术壁垒支撑？
3. 成长质量：高增长是否可持续？
4. 估值合理性：当前估值相对成长性是否合理？
5. 催化剂：1-2季度内有无验证节点？
6. 买卖时机：技术面是否处于回踩/启动位？

## 否决条件（Veto）
1. 非卡脖子环节（纯概念蹭热点）
2. 高毛利但无壁垒（周期性/一次性）
3. 业绩连续两季证伪
4. 估值极端且无成长对冲

## 数据审计要求
- 财务指标（必查）：毛利/ROE/增速 —— 标注数据新鲜度
- 概念归属（必查）：所属概念板块是否纯正
- 主营业务（必查）：主营是否集中于卡脖子环节
- 技术面（可查）：是否回踩支撑/启动位

## 输出要求
复用系统 OUTPUT_SCHEMA：stance/score_delta/confidence/lenses/
supports/risks/summary/action/position_range/entry_style/
invalidation_conditions/next_day_plan
```

### references/ 资料（持续维护）
- `references/ai_supply_chain_map.md`：AI 算力产业链图谱（光模块/CPO/先进封装/材料）
- `references/semiconductor_bottleneck.md`：半导体卡脖子环节清单（光刻胶/CMP/靶材/设备）
- `references/new_energy_bottleneck.md`：新能源卡脖子环节（锂电材料/光伏银浆/氢能）
- `references/policy_calendar.md`：国产替代政策时间表

这些资料让 Agent 的定性判断有事实依据，不是空想。

---

## 六、回测设计

### 现状
系统 `strategy_backtest_adapter.py` 支持在 `meta.backtest_adapter` 声明自定义回测模块。现有策略多为日线短线回测。

### 白毛策略回测需求
- **持仓周期**：1-4 周（中线），不是 1-2 天
- **买入**：T+1 开盘或回踩位
- **出场**：证伪信号（业绩不达预期）/ 止盈（+30%）/ 止损（-15%）/ 持有到期
- **调仓频率**：周线级别

### 实现
新增 `app/services/bottleneck_hunter_backtest.py`：
```yaml
meta:
  backtest_adapter:
    module: app.services.bottleneck_hunter_backtest
    default_action: run
    actions:
      run:
        function: run_backtest
        task_run:
          task_type: midline_backtest
```
函数签名 `run_backtest(db, start_date, end_date)`，返回系统期望的回测 payload 格式。

**注意**：系统的 `holding_days` 现在是短线设计，中线回测要单独走，不复用短线回测路径。

---

## 七、接入步骤清单（据 fina_indicator 实际实现修订）

| 步骤 | 内容 | 文件 | 状态 |
|------|------|------|------|
| 1 | Tushare fina_indicator 封装 | `app/services/tushare_client.py` | ✅ 已完成（`fetch_fina_indicator` + `fetch_fina_indicator_batch`） |
| 1a | 修 `fetch_fina_indicator_batch` 的 limit 死参数 | 同上 | ⏳ 待办（问题 1） |
| 1b | 修 `fetch_fina_indicator` 单股 trade_date 过滤顺序 | 同上 | ⏳ 待办（问题 2） |
| 1c | 补字段：`rd_exp` / `q_profit_yoy` / `update_flag` / `debt_to_assets` | `tushare_client.py` + `tables.py` + `market_pool.py` 转换映射 | ⏳ 待办（问题 3） |
| 2 | feature_builder 注入真实财务 | `app/services/feature_builder.py` | ✅ 已完成（第 925-950 行 fina_map 注入） |
| 2a | 删 `profile["pe_ttm"] = fina.get("pe_ttm")` 死代码 | 同上 | ⏳ 待办（问题 4） |
| 2b | 在 `market_pool._load_fina_indicator_for_stocks` 拉取时显式 `update_flag=1` | `market_pool.py` | ⏳ 待办（配合 1c） |
| 3 | 财务数据刷新机制 | - | ✅ 已实现"按需补齐" + ⏳ 待补"财报季 batch 预热"脚本 |
| 4 | 新建策略目录 | `backend/skills/bottleneck-hunter-a-share/` | 新建 |
| 5 | 写 manifest.json | 同上 | 新建 |
| 6 | 写 strategy.yaml（universe 含 `has_real_fundamental == true` 守门） | 同上 | 新建 |
| 7 | 写 SKILL.md | 同上 | 新建 |
| 8 | 写 references/ 产业链资料 | 同上/references/ | 新建 |
| 9 | 写回测模块 | `app/services/bottleneck_hunter_backtest.py` | 新建 |
| 10 | 配置 backtest_adapter | strategy.yaml 的 meta | 配置 |
| 11 | 确认 `has_real_fundamental` 在 evaluator 白名单内 | `evaluator.py` 的 `ALLOWED_NAMES` | 配置 |
| 12 | 联调 + 回测验证 | - | 测试 |

---

## 八、风险与取舍

1. **基本面数据频率**：fina_indicator 季度更新，与日线行情不同频。已通过 `fina_end_date` / `fina_ann_date` 标注新鲜度，Agent 审查时能拿到财报期。SKILL.md 审查 Checklist 要明确要求 Agent "先看财报期是否过期 2 个季度以上"。
2. **持仓周期**：系统 holding_days 现为短线设计，白毛是中线。建议中线回测单独走，不强行塞进短线回测路径。
3. **Agent 判断质量**：依赖 LLM 能力 + references/ 产业链资料质量。资料要持续维护，否则 Agent 会"空想"。
4. **本土化失真**：A 股没有美股小盘并购逻辑，"国产替代/政策催化"替代。这是有意取舍，不是 bug。
5. **fina_indicator 调用量**：A 股 5000+ 标的，季报期集中拉取需注意 Tushare 频率限制（90 次/分钟）。已实现按需补齐 + 本地缓存，财报季前后需手动跑 batch 预热脚本，避免按需补齐瞬时打爆限速。
6. **strict_real 模式**（fina 接入后已缓解）：`_apply_context_blocks` 在 strict_real 下不再给财务字段填 fallback 默认值，fina 没拉到的字段会是 None，被 evaluator 当 0 处理。**白毛策略 universe 第一条 `has_real_fundamental == true` 守门就是为了挡这个**——基本面不全的标的不入局，而不是用 None/0 比较误杀。strict_real 现在可以正常开启。
7. **财报版本不一致**：同一期 end_date 可能有快报+正式+修正多个版本。当前 upsert by (code, end_date) 会让新版覆盖旧版 ✓，但拉取时如果不传 `update_flag=1`，Tushare 返回的版本不确定。问题 3 / 步骤 2b 已规划显式传 `update_flag=1`。

---

## 九、需要你确认的点

1. **holding_days 中线支持**：系统现在 holding_days 是 [1,2] 短线，白毛要 [5,10,20]。你希望扩展现有 holding_days 支持中线，还是白毛回测单独走一条路径？（仍未确认，建议单独走，避免污染短线回测）
2. **references/ 产业链资料**：要我基于公开信息起草 AI 算力/半导体/新能源三个卡脖子环节图谱，还是你来提供？（仍未确认）
3. ~~**财务数据刷新**：用定时任务（每日检查新财报）还是财报披露日事件触发？~~ → **已由实际实现回答**：选了"按需补齐 + 本地缓存"。日常够用，财报季前后需手动跑 `fetch_fina_indicator_batch` 预热（4/8/10 月底 +1 周各一次）。
4. ~~**strict_real 兜底**：fina_indicator 接入前，白毛策略是否先用 fallback 默认值跑通流程，数据到位后再切真实值？~~ → **已由实际实现回答**：fina 已接入，strict_real 可正常开启。但白毛策略 universe 必须加 `has_real_fundamental == true` 守门，避免基本面不全的标的用 None/0 比较误杀（已在第四章 universe 落实）。

---

**方案修订到此。fina_indicator 数据层主要工作已完成，剩余 5 个问题修一遍（半天）+ 1 个预热脚本，就可以从步骤 4 开始写策略本体了。**

**下一步建议**：
- 先把第三章 5 个问题修了（影响数据精度，不修策略本体写完也是错的）
- 然后回答第九章问题 1、2（持仓周期 + 产业链资料谁写）
- 再开始写 strategy.yaml / SKILL.md / references/
