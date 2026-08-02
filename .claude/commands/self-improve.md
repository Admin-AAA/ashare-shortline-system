# AShare 自主优化协议

你是 AShare 短线系统的自主研究与优化任务。这个命令会被一天运行很多次；你的首要责任是让每轮研究可重复、可证伪、不会因为删票而虚假变好。

## 0. 当前治理模式

当前 selection policy 处于 **audit-only**：

- 可以诊断、修复测量工具、生成冻结候选快照、运行 canonical 双臂实验、发布 `FINDING` 或 `MEASUREMENT_FIX`。
- 不得发布新的 selection policy，不得改变生产板块、策略桶排除、regime penalty、hard veto、动态分数线、软降线或输出限制。
- `selection_policy_governance.audit_only` 缺失时按 `true` 处理；只有一次人工治理变更可以关闭它，研究轮不得自行关闭。
- 止损、止盈、trailing、仓位规则仍由当前 `ExecutionPolicy` 管理，不借本轮 selection 测量改造顺手变更。

目标是在真实可执行、无未来函数、扣除费用的条件下改善 **purged OOS 组合结果**，同时保持策略有生命力。覆盖率、机会捕获和连续机会空仓是发布硬门禁，不是可忽略的展示指标。

兼容且仍有效的执行契约：多窗口 `/loop` 直接调用是本项目唯一使用方式；每轮使用 `si-<当前时间戳>` 作为 researcher id。财务比较必须调用 `evaluate_challenger()`，selection 实验再叠加 ReleasePolicy v2。所有状态变更走 `publish-champion` / `publish-finding` / 状态机子命令，禁止直接写状态文件。

## 1. 唯一事实源

- 生产 selection：`backend/app/engine/production_policy.py` 与 repo 内版本化 policy artifact。
- 生产和回测统一 evaluator：`evaluate_selection_policy()`；禁止在脚本中复制 regime、板块、桶排除或 score-line 规则。
- 正式实验入口：`backend/scripts/run_canonical_backtest_experiment.py`。
- 正式 selection 实验类型：`--experiment-kind production-policy`。
- 正式证据：数据库 `backtest_*` 研究表中 `backtest_experiments.conclusion_json` 的 schema v2 evidence。
- 发布门禁：`ReleasePolicy v2`；只使用 purged OOS 指标与 coverage/liveness gates。
- 状态与发布：`backend/scripts/self_improve_state.py`。
- 健康诊断：`scripts/self_check.py`。
- PATH-A：`backend/scripts/si_path_a_signal_backtest.py`，仅是 `EXPLORATORY` candidate-source adapter，不得生成或声称 `PRODUCTION` evidence。
- sequential replay：仅诊断旧 formal outcome/退出链；`n=0` 是 `INVALID_BASELINE`，不能作为完成证据。

调用工具前使用项目虚拟环境：`backend/.venv/bin/python`。具体状态字段和命令示例查 `.claude/self-improve/OPERATIONS.md`。

## 2. 高频运行的幂等协议

每轮先生成 `input_fingerprint`，至少包含：

- 当前 champion commit、policy id/version/fingerprint；
- repo commit、ExecutionPolicy fingerprint；
- candidate source 与候选快照 fingerprint；
- discovery/validation/holdout 日期边界；
- 数据最新交易日、关键表水位；
- hypothesis id、changed rule id 或策略参数差异。

读取 `self-improve-state.json`、最近 runs 和 lessons。若相同 `input_fingerprint + hypothesis_id + experiment_kind` 已完成：

- 不重复跑同一实验，不重复扫同一网格，不重新消费 untouched holdout；
- 改做新的独立假设、未使用窗口、数据真实性修复或测量修复；
- 没有新输入不是退出理由，但也不是重复利用同一证据的理由。

同一 hypothesis 在相同 discovery 数据上只允许一次主要网格；连续三次未通过 validation/OOS 就归档。不得通过不断调阈值、换窗口边界或删除失败日期来复用同一 holdout。

## 3. 研究员生命周期

每轮生成唯一 `researcher_id=si-<timestamp>`，然后：

```bash
backend/.venv/bin/python backend/scripts/self_improve_state.py prune-claimed
backend/.venv/bin/python backend/scripts/self_improve_state.py claim-direction \
  --researcher-id "$RESEARCHER_ID" --max-workers 3
backend/.venv/bin/python backend/scripts/self_improve_state.py setup-worktree \
  --researcher-id "$RESEARCHER_ID" --base-ref main
```

进入返回的独立 worktree，创建 `self-improve/si-<timestamp>` 分支。运行超过 15 分钟时启动 heartbeat daemon：

```bash
backend/.venv/bin/python backend/scripts/self_improve_state.py daemon-heartbeat \
  --researcher-id "$RESEARCHER_ID" --interval 900 --max-duration 10800 &
```

若 heartbeat 返回 `invalidated` 或 `stale_champion`，立即停止使用旧 champion；已有结果只能发布 FINDING。

无论结果如何，最后必须执行：

```bash
backend/.venv/bin/python backend/scripts/self_improve_state.py release-direction \
  --researcher-id "$RESEARCHER_ID"
backend/.venv/bin/python backend/scripts/self_improve_state.py release-worktree \
  --researcher-id "$RESEARCHER_ID" --remove
```

## 4. PRECHECK：先判断系统有没有活着

先运行：

```bash
backend/.venv/bin/python scripts/self_check.py
backend/.venv/bin/python backend/scripts/self_improve_state.py refresh-policy-health
```

零票必须分成四类，禁止统称“保护性静默”：

1. `reasonable_abstain`：有候选，但普通有效线未通过；
2. `no_opportunity`：没有达到基础分数线且通过硬性可成交检查的 pre-policy card；
3. `policy_blocked`：存在 opportunity，但 selection policy 全部拦截；
4. `pipeline_fault`：没有 completed run、未到 score line、输入/行情/执行流水线故障。

最近 5 个 **opportunity days** 全部零票时标记 `strategy_inactive=true`，冻结新的 Champion 发布。completed run 仍可保留，但不得称为“保护性静默”。优先做单规则恢复反事实和 pipeline 审计。

真实性任一项失败时，只允许 `MEASUREMENT_FIX`：未来函数、T+1/涨跌停/停牌语义错误、费用遗漏、历史补跑伪装 LIVE、实验写入 LIVE 表、同输入不能确定性重放、LLM/网络参与 rule-only replay、候选双臂不共享快照。

## 5. 每轮只选一个动作

优先级：

1. 修复真实性或正式测量工具；
2. 恢复失活策略：逐条审计 drop owner 和单规则反事实；
3. 亏损贡献最大的稳定结构；
4. 退出或仓位；
5. 与现有策略低相关的新策略；
6. 阻断上述工作的工程故障。

假设必须预注册：

> 如果 X 是原因，那么只改变 rule/parameter Y，在冻结候选和 ExecutionPolicy 上，purged OOS 的净收益/PF/回撤应满足发布条件，且 opportunity capture 与连续空仓满足 ReleasePolicy v2。

一次实验只允许一个业务 `rule_id` 或一个策略变量变化；版本、备注、experiment id 不算业务变化。selection policy 恢复实验优先依次测试：结构性强势 veto、震荡 veto、20cm-only、两个 bucket 排除；每次只能改一条，输出 FINDING 排名，不自动发布。

五项预注册恢复实验使用 `backend/scripts/run_policy_recovery_counterfactuals.py --candidate-snapshot-file <snapshot>`；它固定 `audit_only=true`、`auto_publish=false`。

## 6. 数据切分与候选冻结

Discovery、Validation、Untouched Holdout 必须严格隔离：

- chronological 60/20/20；
- 两个边界都 purge/embargo；长度为 `entry_lookahead_days + horizon_days`；
- 任一分区为空，结论是 `INVALID_EVIDENCE`；
- 发布只看 `out_of_sample`；全窗口汇总仅作解释；
- untouched holdout 一旦被用于选择规则/阈值，就降级为 validation，后续实验必须使用新的未触碰 holdout。

selection policy 实验流程：

1. 使用 production scoring stages 的 rule-only replay；禁止 LLM、网络、行情写入和 LIVE 表写入；
2. 在 pool rules 之前冻结 `ScoredCandidateSnapshot`：完整 scored cards、market snapshot、策略版本、outcome bars 和输入 fingerprint；
3. Champion/Challenger 共享完全相同的 candidate snapshot fingerprint；
4. 仅 policy fingerprint 不同，且恰好一个 changed rule id；
5. 两臂调用同一个 production policy evaluator、成交模拟、费用模型和 vectorbt validator；
6. 所有记录只写 `backtest_*` 研究表。

运行示例：

```bash
backend/.venv/bin/python backend/scripts/run_canonical_backtest_experiment.py \
  --experiment-kind production-policy \
  --candidate-source production-rule-replay \
  --candidate-snapshot-file <frozen_snapshot.json> \
  --champion-policy-id production-selection \
  --challenger-policy-patch '{"structural_strong_hard_veto":false}' \
  --hypothesis '<预注册假设>'
```

策略自身参数实验使用 `--experiment-kind strategy`，不得用 PATH-A 的聚合结果冒充 canonical evidence。LLM prompt/model 变化只能用冻结 recorded-review snapshot 单独实验。

## 7. 正式 evidence 与发布判定

Evidence schema v2 至少包含：

- experiment kind、candidate snapshot fingerprint；
- champion/challenger policy fingerprints、changed rule id；
- split policy、decision scope；
- 每臂全窗口与 split metrics；
- `PortfolioCoverageEvidence`；
- rule verdict summary；
- vectorbt cross-validation；
- evidence fingerprint。

Opportunity day：至少一张 pre-policy card 达到基础分数线、通过硬性可成交检查。每臂报告 eligible days、opportunity days、active days、executed trades、opportunity capture rate、abstain days、最大连续 opportunity abstain。

Challenger 只有同时满足以下条件才可成为发布候选：

- 原收益/PF/回撤比较条件在 purged OOS 成立；
- OOS 至少一笔 executed trade，零票 challenger 一律拒绝；
- opportunity capture rate ≥ `max(20%, champion 的 50%)`；
- 最大连续 opportunity abstain ≤ 5 个交易日；
- 恰好一个 changed rule；
- 两臂 vectorbt truth 通过。

零票 champion 不阻止非零恢复 challenger；但 challenger 必须通过全部收益和 liveness gates。禁止用覆盖下降、abstain 增加或删除亏损交易来解释为“准确率提升”。任何 filter 必须报告删掉的盈利/亏损、drop owner、机会成本和跨 regime 表现。

决策值：

- `PRODUCTION_CANDIDATE`：canonical evidence 的 `should_publish=true`，但当前 audit-only 下仍只能记录 FINDING；
- `FALSIFIED`：任一收益或 liveness gate 不通过；
- `INVALID_EVIDENCE`：切分、快照、指纹、truth validator 或正式数据契约失败；
- `MEASUREMENT_FIX`：只修复测量，不声称收益改善；
- `FINDING`：有可信研究结果但不改变 champion；
- `BLOCKED`：数据与测量设施都不可用，且本轮修复失败。

## 8. 发布—部署—Canary—回滚状态机

不得从回测结果直接改 champion。正式流程是：

```text
draft -> evidence_verified -> canary_running -> canary_passed -> completed
                                      |-> rollback_required -> rolled_back
```

- `publish-champion` 必须传 `--canonical-evidence-id`；工具从数据库读取 evidence，不信任调用者拼装 JSON。
- evidence 的 champion/challenger fingerprints、single-rule gate、全部 liveness gates 和 `should_publish` 必须匹配。
- `deployment_confirmed` 必须带真实 `deployment_fingerprint`；没有部署 adapter/回执时停在 `evidence_verified`，不得伪造部署成功。
- Canary 监控推荐活性、错误率、policy fingerprint 和已结算 LIVE 指标；失败进入 `rollback_required`。
- selection policy 当前 audit-only，所以不得把状态推进到生产发布；只验证状态机与输出 FINDING。

状态机命令：

```bash
backend/.venv/bin/python backend/scripts/self_improve_state.py advance-release \
  --event verify_evidence --details '{"evidence_id":"...","policy_fingerprint":"..."}'
```

后续只有外部部署/Canary 有真实回执时才能推进相应事件。

## 9. 记录、提交与安全

- FINDING 用 `publish-finding`；不得通过 `commit` 改 `canonical_production`。
- selection policy 的 state 只保存 policy id/version/fingerprint，不复制规则。
- 记录 hypothesis id、input fingerprint、candidate snapshot fingerprint、evidence id/fingerprint、changed rule、OOS metrics、coverage、decision 和回滚点。
- 测量修复及测试可以合并；生产策略语义变更必须经过上述完整状态机。
- Python 测试使用项目 `.venv`；禁止删表、清空数据、修改 `.env`、force push、`git reset --hard`，禁止把 backtest/replay 写进 LIVE formal 表。
- LLM/网络调用默认 0；只有 prompt/model 研究允许有上限的 recorded canary。

## 10. 最终报告

只报告：

1. 唯一主假设和 changed rule/variable；
2. candidate snapshot 与 evidence fingerprint；
3. purged OOS 的净收益、胜率、PF、回撤差值；
4. opportunity/active/executed/capture/连续 abstain；
5. truth validation 与失败 gates；
6. `FINDING / FALSIFIED / INVALID_EVIDENCE / MEASUREMENT_FIX / BLOCKED`；audit-only 期间不得报告已发布 selection `PRODUCTION`；
7. 是否写入 `backtest_*`、是否有任何 LIVE/网络/LLM 写调用；
8. release-direction 和 worktree 清理结果。

代码行数、提交数、运行次数和“今天没有新 LIVE 样本”都不是进展指标。
