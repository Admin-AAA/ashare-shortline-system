# Canonical Backtest Experiment 设计规格

日期：2026-08-02

状态：已实现（首个单策略纵向切片）

范围：正式研究回测、self-improve Champion/Challenger 对照实验

## 1. 问题

系统已有不可变行情快照、单策略 Research Runner、`backtest_*` 隔离表、vectorbt 校验和唯一生产判定器，但这些能力没有收敛到一个正式实验 interface：

- Champion 与 Challenger 由脚本分别运行，共享输入依赖调用方自律；
- `BacktestExperiment` 只冻结计划，不执行双臂实验；
- runner 的返回值将正式结果放在 `result` 内，旧比较工具却从顶层读取指标和日期；
- 不同 `si_*.py` 脚本重复拼装指标、判定和输出，evidence artifact 没有统一 schema；
- vectorbt adapter 用已经包含执行滑点的成交价重放时，又把滑点传给 vectorbt，形成双重扣减。

这些问题会让自动优化任务得到不可比较、不可稳定重放或被测量误差污染的结论。

## 2. 决策

新增一个深 module：`CanonicalBacktestExperiment`。它只暴露一个主要 interface：

```python
artifact = CanonicalBacktestExperiment(repository, runner).run(spec)
```

该 module 负责以下完整策略：

1. 解析并冻结 Champion、Challenger 的策略版本与参数指纹；
2. 只捕获一次 Champion 快照；
3. Challenger 通过 runner 的正式 `run_on_snapshot` interface 读取同一快照；
4. 在候选执行前冻结 `BacktestExperiment` 计划；
5. 统一计算全窗口及 train/validation/out-of-sample 时间切分指标；
6. 汇总 runner 内置的 vectorbt adapter 校验结果；
7. 真实性校验通过后，只调用 `evaluate_challenger()` 作生产判定；
8. 持久化并返回一个 canonical evidence artifact 及其稳定 SHA-256 指纹。

实验 module 是高 leverage 的正式入口；快照捕获、策略执行、A 股成交语义、持久化和生产判定仍留在各自已有 module 中。新增代码只编排这些深 interface，不复制实现。

## 3. 不变量

### 3.1 同输入

两个 arm 必须具有相同的：

- 日期选择器；
- 股票范围；
- 本地只读数据 policy；
- 成交、成本和持有期 policy。

允许变化的只有策略 id/version 和策略声明的 `parameter_set`。任何输入或执行 policy 漂移在候选执行前失败。

### 3.2 无未来函数与不可变重放

- Champion 通过现有 snapshot builder 从本地数据捕获内容寻址快照；
- Challenger 只能通过 `run_on_snapshot(request, snapshot_id)` 读取已持久化 artifact；
- `run_on_snapshot` 不读取可变行情源，并验证请求日期选择器与快照日期兼容；
- evidence 指纹不包含 attempt id、experiment id、时间戳等非语义字段，因此相同输入和结果得到相同指纹。

### 3.3 唯一生产判定

- 真实性校验失败：实验状态为 `failed`，不得进入发布判定；
- 真实性校验通过：使用全窗口统一汇总指标调用 `evaluate_challenger()`；
- train/validation/OOS 指标用于展示稳定性和定位过拟合，不增加样本量、CI、覆盖率或 shadow 发布门槛；
- regime 分层仍是解释工具，不成为第二个发布判定器。

### 3.4 vectorbt adapter 语义

domain engine 已产出包含滑点的实际 entry/exit price。vectorbt adapter 使用这组已实现成交价时：

- 只重算费用；
- `slippage=0`，禁止再次扣减滑点；
- T+1、涨跌停、不可成交和路径依赖退出继续由 domain engine 拥有，并在 `semantic_exclusions` 中声明。

RQAlpha 暂不进入运行时依赖。后续只有在需要第二套完整事件驱动撮合交叉验证时，才新增 RQAlpha adapter；A 股 production 语义仍以 domain engine 为准。RQAlpha 的 GPL 许可和部署依赖必须单独评审。

## 4. Module 与 seam

```text
CanonicalBacktestExperiment
  ├── ResearchBacktestRunner.run              # 捕获并执行 Champion
  ├── ResearchBacktestRunner.run_on_snapshot  # 同快照执行 Challenger
  ├── ResearchBacktestRepository              # 冻结计划、读取 trade、保存结论
  ├── compute_metrics                          # 全窗口与时间切分
  ├── vectorbt_validation adapter              # 独立数值交叉校验
  └── evaluate_challenger                      # 唯一发布判定
```

只新增一个必要 seam：runner 的 `run_on_snapshot`。它把“如何安全复用不可变输入”的复杂度封装在拥有快照知识的 module 内，调用方不能接触 `_load_snapshot_bundle` 等私有实现。

不为 RQAlpha、统计引擎或 artifact writer 预建抽象 interface；在第二个真实 implementation 出现前避免假扩展点。

## 5. Evidence artifact schema

```json
{
  "schema_version": 1,
  "experiment_id": "btexp_...",
  "status": "completed|failed",
  "hypothesis": "...",
  "shared_snapshot_id": "bts_...",
  "split": {
    "train": ["YYYY-MM-DD"],
    "validation": ["YYYY-MM-DD"],
    "out_of_sample": ["YYYY-MM-DD"]
  },
  "arms": {
    "champion": {
      "backtest_id": "btr_...",
      "strategy_id": "...",
      "strategy_version": "...",
      "configuration_fingerprint": "sha256",
      "metrics": {},
      "split_metrics": {},
      "cross_validation": {}
    },
    "challenger": {}
  },
  "truth_validation": {
    "passed": true,
    "validators": {"champion_vectorbt": true, "challenger_vectorbt": true}
  },
  "decision": {
    "decision": "PRODUCTION|FALSIFIED|INVALID_EVIDENCE",
    "should_publish": false,
    "deltas": {}
  },
  "evidence_fingerprint": "sha256"
}
```

`evidence_fingerprint` 覆盖共享快照、两个配置指纹、指标、切分结果、validator 结果和判定；不覆盖数据库 id 与展示文案。

## 6. 时间切分

首个 implementation 使用唯一的确定性 chronological 60/20/20 policy：

- 至少需要 3 个实际 signal date；
- 每段至少 1 天；
- 日期升序、互不重叠并完整覆盖快照 signal dates；
- 切分在 Champion 捕获快照后、Challenger 执行前冻结；
- 发布判定仍比较完整窗口，切分只作为诊断 evidence。

## 7. 正式入口

新增 `backend/scripts/run_canonical_backtest_experiment.py`：

- 接受日期范围、策略、Champion/Challenger 参数、成交 policy 和假设；
- 只调用 `CanonicalBacktestExperiment.run()`；
- stdout 输出完整 JSON evidence artifact；
- exit code `0` 表示实验成功执行，无论结论是 PRODUCTION 还是 FALSIFIED；真实性或执行失败返回非零。

`self-improve.md` 将该命令登记为正式双臂实验入口。旧 `si_*.py` 在迁移完成前仅作为特定研究 adapter 存在，不得再被视为 canonical evidence producer。

## 8. 验收标准

1. 测试证明 Champion 调用 `run`，Challenger 调用 `run_on_snapshot`，且 snapshot id 完全相同；
2. 测试证明实验计划在 Challenger 执行前已冻结；
3. 输入日期、股票范围、数据 policy 或 execution policy 漂移会在候选执行前失败；
4. 相同语义 evidence 在不同 experiment/attempt id 下产生相同指纹；
5. vectorbt 任一 arm 失败时输出 `INVALID_EVIDENCE`，且不调用生产发布判定；
6. 正常 evidence 只由 `evaluate_challenger()` 给出 PRODUCTION/FALSIFIED；
7. vectorbt 使用已实现成交价时不重复计入滑点；
8. CLI、针对性测试和现有 backtest 测试全部通过；
9. 不写 `Recommendation`、`RecommendationOutcome` 或任何 LIVE formal 事实表。

## 9. 后续迁移

本次实现完成 module、CLI、旧 score-line comparison adapter 接入和 self-improve 文档入口。后续按参数类型逐个删除 `si_*.py` 中重复的回测/指标/判定代码；不得一次性改写所有研究脚本，也不得在未验证 production 语义等价前切换。
