# 按策略离场配置（exit profile）设计 —— 未启用

状态：设计稿（2026-10-15）。三窗证据（2025-01~10、2025-10~2026-04、2026-05~10）齐之前不启用。
动机：round1（持有 7 天、trailing 激活 2%）只在 chao-gu（及小样本 high-range）两窗一致改善；chen 两窗变差，sector/oversold 方向相反，不能全局切换。

## 现状
- 执行合约 `ExecutionPolicy`（app/engine/execution_policy.py）是全局唯一，指纹进入证据身份。
- 按策略只差异化了**入场**：`execution_policy_with_entry_mode(policy, entry_mode)` 只替换 `entry`；入场方式在生成时写进推荐 `feature_snapshot.entry_mode`，结算（outcome_simulation）按快照选入场。
- 结算的离场参数来自模块级常量（`_EXECUTION_RUNTIME` → HORIZON/TRAILING_*），对所有推荐一致。

## 设计
1. **注册表**：execution_policy.py 新增 `EXIT_PROFILES: Mapping[str, ExitOverrides]`，`default`（= 当前 v5.0.0：5d/0.8%）与 `round1_7d_trail2`（horizon_days=7, trailing.activation_gain_pct=2.0，其余不变）。`execution_policy_with_exit_profile(policy, profile_id)` 用 `dataclasses.replace` 生成新合约（新指纹，证据天然隔离）。未知 id → 抛 `ExecutionPolicyNotFoundError`。
2. **声明**：策略 manifest `contract.default_exit_profile`（可选，缺省 `default`），与 `default_entry_mode` 并列；不进 strategy.yaml 打分逻辑。
3. **时点固化（PIT）**：生成推荐时把 `exit_profile` 与 `entry_mode` 一起写入 `feature_snapshot`；结算只读快照，不读当前 manifest → 改配置不会追溯改写已发推荐的结算。旧快照无字段 → `default`，历史结果逐位不变。
4. **结算**：outcome_simulation 把模块常量改为从 `execution_policy_with_exit_profile(DEFAULT, snapshot.exit_profile)` 取 horizon/trailing；outcome 行记录 `exit_profile_id`。
5. **回测/策略档案**：strategy_profile.current_targets、policy_experiment、ablation_fast 用 `entry_mode + exit_profile` 组合合约；单策略指标页按各自合约。
6. **开关**：仅当某策略 manifest 声明非 default 时生效；上线 = 在 chao-gu manifest 加一行（heredoc 重写 manifest）。回滚 = 删该行（已发推荐按快照仍用原合约结算）。

## 上线门槛
- 三窗（含 2023–2024 回填后的更长窗）chao-gu round1 均值 ≥ 基线且胜率不降 >2pp；n≥100/窗。
- dry-run 10-09/10-13/10-14 选股不变（离场不影响选股）；结算单测：旧快照逐位一致、新快照 7d/2% 生效。
