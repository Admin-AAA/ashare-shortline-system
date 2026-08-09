# 小盘题材动量质量门禁：冻结历史 Holdout v1

## 目的

验证已经在 2026-01-31～2026-07-29 发现窗内形成的“小盘主线共振 + 强势周期 + 85 分”质量门禁，是否能在未参与调参的更早历史样本中保持正期望。该验证只授予“历史合格”，不能直接切换生产策略。

## 冻结身份

- 策略：`small-cap-thematic-momentum`
- 策略版本：`39457718f5b53fcfb49d88ffc10ef2cc6adeeab4dea31bfe1f29bf84c5564d5d`
- 参数：`minimum_signal_score=70.0`
- 质量门禁：评分不低于 85；仅强势/结构性强势；必须主线题材；买点包含“小盘主线共振”或“小盘题材动量启动”；入场跳空不高于 6%
- 用户成本：佣金单边万 0.85、卖出印花税万 5；最低佣金 1 元因缺少账户资金暂不折算
- 质量指纹：`545cae589c221b63c0f493f3ba974be22f537fb286af210ff54f798210353a6b`

## 冻结样本

- 历史 holdout：2025-01-15～2026-01-16（244 个本地交易日，不超过单次 canonical 上限 250 日）
- 冻结交易日集合指纹：`3037c3c58fc4b8b74f4152509b3c03c50b94d7c344b2aa563bcec585f5d80e9d`
- 发现窗开始：2026-01-31
- 边界隔离：15 个自然日，大于成交标签最长 7 个交易日（入场观察 2 日 + 持有 5 日）
- 禁止根据 holdout 结果修改阈值后在同一窗口重跑并声称 OOS；若修改候选，必须更换质量指纹和 holdout
- 证据指纹必须同时绑定 `backtest_id`、`snapshot_id`、输入 manifest、执行版本和请求指纹；脚本从已落库的 run/snapshot 读取，spec 不允许自声明这些来源
- 正式 evaluator 只接受真实 snapshot population 在揭盲前已有的 claim，claim 的 experiment identity 绑定本 spec 指纹；不存在 claim 时拒绝读取 outcome artifact
- completed run 的 candidate/trade/attribution 禁止追加；run result 冻结其有序输出证据指纹，正式 evaluator 必须重算一致
- 当前 snapshot source schema 明确标记 `Stock.industry/is_st/name` 为非时点化历史数据，因此 `point_in_time_stock_metadata` 硬门禁固定失败；本轮输出只能用于诊断，不能取得 `HISTORICALLY_QUALIFIED`

## 验收门禁

以下条件必须全部满足：

- 质量门禁后至少 100 笔已成交交易、至少 20 个信号日
- 至少覆盖 2 种允许的市场周期
- 扣除用户比例手续费后，平均单笔收益大于 0，盈亏比大于 1.10
- 最大回撤不高于同一 holdout 的全量策略基线
- 按时间顺序切成 3 折，至少 2 折同时满足平均收益大于 0、盈亏比大于 1.0

即使全部通过，状态也只能是 `HISTORICALLY_QUALIFIED`；生产切换仍需未来 canary 验证。

## 可复现命令

从 `backend` 加载项目 `.env` 后运行预检：

```bash
.venv/bin/python scripts/run_smallcap_frozen_holdout.py
```

当前预检会在读取 holdout 前返回 `INVALID_EVIDENCE`、`holdout_consumed=false` 和退出码 2。原因是系统尚无“先生成真实不可变 snapshot 并原子 claim、再揭示 outcome”的两阶段能力，且股票元数据不是时点化历史数据。补齐两项基础能力前，不允许用占位 snapshot 或事后 claim 绕过。
