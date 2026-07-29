# 买入确认解释、盘中确认与历史状态持久化 Spec

日期：2026-07-29  
状态：已确认，待实现

## 1. 背景

推荐历史中大量 `entry_rule` 为“等待买点确认后再参与”。该文案没有说明买点名称、判断条件和关键价位，不能支持用户执行。当前次日实时确认已经能重跑原策略并返回 `buy / wait / cancel / not_ready / expired`，但仍存在三个缺口：

1. 盘中推荐被统一要求等待下一交易日，错过推荐当日可执行窗口。
2. “不在策略股票池”等泛化原因遮蔽了真正未通过的策略条件。
3. 实时确认结果只存在于进程缓存，推荐结算后无法回答“系统是否曾经命中过买入条件”。

生产库现状（2026-07-29）：719 条推荐中 309 条使用占位入场规则，其中 281 条没有明确 `setup_type`。因此本次改造必须同时处理判断语义、解释和持久化，不能只替换前端文案。

## 2. 目标

- 盘中推荐在推荐当日即可进行实时买入确认。
- 盘后推荐仍从下一交易日开始确认。
- 当前判断必须说明具体失败条件；“不在策略股票池”只能作为兜底，不得遮蔽已有详细原因。
- 历史页清楚区分原始计划、当前实时判断、系统历史命中和结算模拟。
- 持久化每笔推荐的最新有效判断，以及是否曾经出现 `buy`。
- 缓存命中不重复写库；行情未就绪和请求错误不覆盖最后一次有效判断。

## 3. 非目标

- 不代表用户真实下单或真实成交。
- 不把 `RecommendationOutcome.entry_triggered` 解释为实时买点命中。
- 不用未来数据倒推旧推荐当时是否命中。
- 不在前端解析策略表达式或另写一套买入规则。
- 本次不批量改写全部历史策略 YAML；先透出已有具体原因，缺失时诚实显示原始表达式或未知。

## 4. 统一术语

| 术语 | 定义 |
|---|---|
| 原始买点计划 | 推荐生成时保存的 `setup_type / entry_rule / ideal_buy / secondary_buy / stop_loss` |
| 当前买入确认 | 用当前实时 K 重跑原策略得到的 `entry_decision` |
| 系统曾命中 | 至少一次有效实时确认返回 `entry_decision.code == buy` |
| 最新判断 | 最近一次有效实时确认的状态；可在 buy、wait、cancel、expired 间变化 |
| 模拟入场 | Outcome 引擎按历史日 K 推演的 `entry_triggered`，与系统曾命中不同 |

前端不得把“系统曾命中”写成“已买入”或“已成交”。

## 5. 确认时间窗

### 5.1 盘中推荐

沿用 `outcome_simulation._is_intraday_recommendation` 的权威口径：`recommended_at` 使用 UTC 存储，北京交易时段 09:30–15:00 对应 UTC 01:30–07:00。

- 推荐日行情新鲜且当前时间尚未收盘：允许在推荐当日确认。
- 推荐当日重跑原策略：可返回 `buy / wait / cancel`。
- 推荐当日收盘后不再追认盘中信号，返回 `expired`。
- 下一交易日不重新打开该盘中推荐的入场窗口。

### 5.2 盘后推荐

- 推荐日返回 `not_ready`，文案为“等待下一交易日确认”。
- 推荐后的首个交易日盘中允许返回 `buy / wait / cancel`。
- 首个交易日收盘后返回 `expired`。
- 超过首个交易日返回 `expired`。

### 5.3 缺失时间

`recommended_at` 缺失时按盘后推荐处理，避免虚构当日可执行性。

## 6. 判断解释接口

实时确认结果继续以 `entry_decision` 为唯一动作来源，并新增稳定解释字段：

```json
{
  "entry_decision": {
    "code": "cancel",
    "label": "取消买入",
    "reason": "当日涨幅需为非负"
  },
  "condition_summary": {
    "status": "failed",
    "scope": "universe_include",
    "code": "universe_include",
    "label": "当日涨幅需为非负",
    "expression": "pct >= 0"
  }
}
```

规则：

- `prefilter_ok == false` 时优先使用 `prefilter_reason`，不得被 `signal.veto_reason == 不在策略股票池` 覆盖。
- 策略 YAML 提供 `reason/name` 时返回中文说明。
- 旧式字符串规则没有说明时，返回作用域和原始表达式，不伪造实际值。
- 数据缺失导致严格过滤时，文案必须体现“条件未通过或数据缺失”。
- `condition_summary` 由后端策略判断模块生成，前端只展示。

## 7. 持久化模型

新增表 `recommendation_execution_states`，每笔推荐至多一行：

| 字段 | 含义 |
|---|---|
| `recommendation_id` | 主键，关联推荐 |
| `latest_decision_code` | 最新有效判断 |
| `latest_decision_label` | 最新展示标签 |
| `latest_reason` | 最新原因 |
| `latest_checked_at` | 后端判断时间 |
| `latest_quote_time` | 行情时间 |
| `latest_data_as_of` | 行情交易日 |
| `latest_price` | 判断时当前价 |
| `latest_strategy_id` | 本次重跑策略 |
| `latest_setup_match` | 本次命中买点 |
| `latest_condition_json` | 条件解释快照 |
| `ever_buy_confirmed` | 是否曾返回 buy；只允许 false → true |
| `first_buy_confirmed_at` | 首次 buy 时间；只写一次 |
| `first_buy_confirmed_price` | 首次 buy 价格；只写一次 |
| `created_at / updated_at` | 审计时间 |

持久化规则：

- 仅持久化 `fresh == true` 且结果完整的 `buy / wait / cancel / expired`。
- `not_ready / error` 不覆盖最后一次有效状态。
- 只有实际重新计算的 owner 请求写库；3 分钟缓存命中和 single-flight 等待者不重复写。
- 批量写入一次提交，单票失败不得阻断其余状态写入。
- `ever_buy_confirmed` 单调递增；后续 cancel/expired 只更新 latest 字段。
- 删除推荐或 run 时同步删除状态行。
- 旧推荐不回填 `ever_buy_confirmed`；历史页显示“历史未记录”。

## 8. 历史接口

`GET /api/recommendations/history` 对当前页 ID 一次批量加载持久化状态，增加：

```json
{
  "execution_state": {
    "latest_decision_code": "cancel",
    "latest_decision_label": "取消买入",
    "latest_reason": "当日涨幅需为非负",
    "latest_checked_at": "2026-07-29T12:28:00",
    "latest_price": 8.34,
    "ever_buy_confirmed": true,
    "first_buy_confirmed_at": "2026-07-29T10:02:00",
    "first_buy_confirmed_price": 8.41,
    "condition_summary": {}
  }
}
```

已结算记录不再发起实时确认，但可展示持久化状态。

## 9. 前端展示

历史页“执行规则”单元格按以下顺序展示：

1. 当前未结算推荐：实时判断标签、当前价、行情时间。
2. 已结算推荐：最后持久化判断标签和判断时间，不发实时请求。
3. 若 `ever_buy_confirmed == true`：展示“系统曾命中买入条件”，并显示首次时间和价格。
4. 显示具体未通过条件；“不在策略股票池”仅在没有任何更详细原因时显示。
5. 显示原始计划：明确 `entry_rule` 正常展示；占位文案或空值展示“原推荐未保存明确买点”。
6. 保留关键价位、失效条件和完整 title。

盘中推荐的等待文案：

- 盘中推荐且行情未形成：`盘中行情尚未就绪`。
- 盘后推荐当日：`等待下一交易日确认`。
- 不再统一显示“尚未到次日确认”。

## 10. 深模块与 seam

执行确认模块的外部 interface 保持为批量确认和单票确认。复杂性收口在两个内部模块：

- 判断模块：输入推荐、实时上下文和原策略，返回完整 `ExecutionAssessment`。
- 状态仓储模块：输入 assessment 列表，按持久化不变量更新状态；历史投影只读取它。

前端、历史查询和持久化都消费同一 assessment 结构，不复制决策逻辑。数据库是状态仓储的生产 adapter，测试使用 SQLite adapter。

## 11. 验收标准

### 后端

- 盘中推荐在 age=0 且窗口开放时可返回 buy/wait/cancel。
- 盘后推荐 age=0 返回 not_ready。
- 盘中推荐收盘后返回 expired。
- prefilter 失败优先返回具体原因。
- fresh buy 首次写入 `ever_buy_confirmed=true` 和 first-buy 字段。
- buy 后 cancel 保留 ever/first，更新 latest。
- not_ready/error/cache hit 不重复或覆盖状态。
- 历史接口批量返回状态，已结算记录也可读取。
- 删除推荐/run 同步删除状态。
- 迁移重复运行幂等。

### 前端

- 占位规则不再显示为可执行提示。
- “不在策略股票池”有具体原因时不再出现。
- 显示当前判断、条件解释和系统曾命中信息。
- 已结算行不请求实时行情，但展示历史状态。
- 页面刷新命中 3 分钟缓存时不产生重复状态写入。

## 12. 验证

- 执行确认状态机单测。
- 批量确认持久化与缓存集成测试。
- 迁移测试。
- 历史接口契约测试。
- 前端纯逻辑测试与生产构建。
- 本地 MySQL 应用迁移后，用盘中推荐和盘后推荐各一笔做真实请求验证。
