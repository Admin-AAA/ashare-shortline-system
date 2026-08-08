# 近期变更质量审查报告（2026-08-08）

## 结论

本轮审查覆盖基线 `3f90a734664a39e4e0d3414e4ab9a448f8f6c75d`（2026-07-31 23:58:45）至近期提交，并核对了 `origin/main`（`04427aa69ed26e936620966e2e8ebb31e4bccff1`）及现有修复分支。近期变更数量较大，主要是生产证据、历史回放、指标页和治理产物；代码门禁可通过，但发现了一个测试生成器错误、一个研究回测口径错误和一处历史辅助数据未来泄漏风险，均已修复或合入当前修复分支。

当前修复分支包含以下已验证改动：

- 研究回测输入快照拒绝未知字段；研究回测复用生产短线因子编排，并使用信号日全市场宽度，避免目标股票子集改变市场情绪结论。
- 历史回放的质押、股东户数等辅助状态按 `as_of_date` 截断，并将截断后的视图传入特征构建，阻断未来数据进入历史特征。
- 修正证据属性测试对非法历史重建组合的生成逻辑；非法组合现在明确断言生产校验抛出 `EvidenceIdentityError`。
- 为因子构建测试增加模块级自动 bootstrap，使该测试文件脱离完整测试套件单独运行时仍然确定。
- 清理仓库中无效且带有不安全自动化指令的旧 Kiro autopilot 文件，并忽略运行时 `.kiro/settings/`。

## 验证证据

### 标准/工程门禁

以下检查已通过：

```text
backend/.venv/bin/python -m compileall backend/app -q       PASS
backend/.venv/bin/python scripts/check_architecture.py     PASS
RUN_REAL_MYSQL_S6=0 pytest backend/app/ -q                 68 passed, 1 skipped
ASHARE_DATABASE_URL=sqlite:// pytest backend/tests/ -m "not integration"  2482 passed, 175 skipped, 43 deselected
MySQL integration smoke suite                              6 passed
governance store layout check                              670 objects valid
frontend-v2: npm test                                      39 passed
frontend-v2: npm run build                                 PASS
git diff --check                                           PASS
```

首轮完整后端回归为 `2687 passed, 5 skipped, 1 failed`；唯一失败是 Hypothesis 生成了不满足生产前置条件的历史重建输入，已修正测试并通过。针对本轮改动的隔离回归最终为 `81 passed, 10 warnings`。

### 规范/需求核对

审查参考了 `.kiro/specs/production-evidence-risk-hardening/` 的 requirements、design、tasks、traceability，以及 `.kiro/specs/repo-survey-adoption-2026-08/` 的采用记录。与 #17 相关的发布批次、证据来源、历史重建和消费者门禁已在主分支已有实现；本轮修复继续沿用 `as_of_date`、已验证快照和代码版本约束。项目没有一份覆盖“最近几天全部提交”的单一规格，因此对无直接需求映射的治理/文档提交只做工程质量评估，不宣称完成额外业务需求。

## 发现与处置

| 等级 | 发现 | 处置 |
| --- | --- | --- |
| P0（已修复） | 证据属性 Hypothesis 测试会生成未来数据、未验证快照或未验证 revision 的非法历史重建组合，导致完整回归假失败。 | 测试对非法组合显式断言 `EvidenceIdentityError`，合法组合继续验证证据分类。 |
| P0/P1（已修复） | 研究回测私有因子实现从目标代码子集计算涨跌停/宽度，可能把同一信号日的市场 regime 改写成子集 regime，且与生产因子编排分叉。 | 复用生产 `apply_shared_shortline_factors`，保留信号日全市场统计；增加回归测试。 |
| P0/P1（已修复） | 历史回放辅助状态未统一按信号日截断，质押/股东户数等数据可能使用未来观测。 | application、state adapter、run stages 和 feature builder 全链路传递 `as_of_date`/截断视图，并补充回归测试。 |
| P1（已修复） | `.github/workflows/verify.yml` 的 Python 单元任务仅执行 `pytest app/`（约 68 个测试），没有覆盖 `backend/tests/` 的大部分回归；完整套件依赖 MySQL 锁和真实数据配置，CI 当前没有对应服务。 | 新增 SQLite 无数据库回归 job（覆盖 2482 个测试）和 MySQL 8 集成 smoke job；数据库依赖测试统一注册 `integration` 标记。 |
| P1（已修复） | 完整回归产生约 1491 条 warning，主要是 `datetime.utcnow()` 弃用、FastAPI/Starlette/httpx 弃用提示和未注册的 `integration` mark。 | 统一 `utcnow_naive()`，注册 `integration` mark，改用 `httpx2`，移除已弃用 ORJSON 默认 response，注册 SQLite 显式 datetime adapter，并移除旧回测写入路径的运行时弃用噪声。当前两套门禁均无 warning。 |
| P1（已修复） | 基线以来新增治理证据/运行产物较多（约 1069 个文件、+87k 行），审查成本和仓库体积明显上升。 | 保留 670 个不可变审计对象；新增治理产物策略和 `check_governance_store.py` CI 校验，限制对象命名、JSON 完整性、单对象 256 KiB 上限和临时文件进入。 |

## 分支整合决策

| 分支/引用 | 决策 | 理由 |
| --- | --- | --- |
| `fix/imperative-strategy-profile` | 合入主分支 | 包含本报告列出的回测、证据测试和未来泄漏修复，以及此前 imperative profile、replay 去重、horizon 查询修复。 |
| `fix/metrics-history-replay-scope` | 删除 | 内容已由 `04427aa` 合并进主分支。 |
| `https-origin/fix/show-replay-in-records` | 清理过期本地远程跟踪引用 | `git ls-remote` 未发现对应远程头，不是仍在服务器上的有效分支。 |
| `worktree-agent-a126ff14e100828f1` | 删除分支，保留其已合入提交 | `8ff6603` 已 cherry-pick 到修复分支。 |
| `worktree-agent-a6a6d8a4edeab5855` | 删除分支，不合入 | 其 `42fa2f7` 的五分钟回填改动被主分支较新的 `ca545f5` 完整覆盖，直接合入会回退市场 selector/汇总逻辑。 |
| `self-improve/si-20260808-165902` | 保留 | 当前仍由独立 worktree 检出，内容与主分支一致，不能据此判定为废弃。 |

删除分支前会先确认工作树无未提交改动、主分支已更新并完成远程推送；不会删除独立 worktree 目录，也不会改动用户已有的未跟踪文件 `系统深度诊断报告.md`。

## 后续建议（已降为 P2）

1. 按数据准备成本逐步扩大 MySQL CI smoke 覆盖，不把依赖本地生产样本的全量回测强行塞入每次 PR。
2. 继续将运行时锁、临时快照和大批量生成证据与长期审计证据分层管理，减少主仓库噪声。
