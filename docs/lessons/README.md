# 教训与经验沉淀规范

本目录是项目中“可跨轮复用、会改变后续决策”的教训唯一存放地点。

## 唯一事实源

- [LESSONS.md](./LESSONS.md)：唯一的教训注册表。新教训、状态变化和规范化后的长期经验都写这里。
- [VALIDATION.md](./VALIDATION.md)：教训可靠性审计。记录当前代码、测试、数据库与历史证据的交叉核验结果；它不是第二份教训库。
- `.workbuddy/memory/MEMORY.md`：本地工作记忆的导航页，只记录本规范和高频提醒，不承载另一份教训副本。
- `.claude/self-improve/OPERATIONS.md`：自动优化流程的接口契约，引用本注册表，但不复制教训内容。

项目外的 `~/.claude/projects/.../memory/self-improve-LESSONS.md` 已降级为历史快照；后续不再从那里新增或修改教训。

## 内容边界

### 应写入 `LESSONS.md`

满足以下条件的结论才进入注册表：

1. 有明确证据，能回指报告、脚本、测试、commit 或数据库查询。
2. 结论可以跨一次运行复用，并会改变研究、开发、验证或发布决策。
3. 能写成一条可执行的永久规则，而不只是一次性的样本数字。
4. 已说明当前状态：`active`、`encoded` 或 `superseded`。

### 不直接写入 `LESSONS.md`

以下内容保留在各自的证据档案中，需要时在教训条目中回链：

| 内容 | 存放位置 |
| --- | --- |
| 单次 bug 的根因、修复过程和影响 | `docs/bugs/`、`backend/docs/bugs/` |
| 代码审查、架构审查和诊断报告 | `docs/reviews/`、根目录报告、`output/` |
| 回测原始结果和生成报告 | `backend/scripts/*_report.md`、`output/` |
| 需求、设计和实施计划 | `docs/specs/`、`docs/superpowers/specs/`、`.kiro/specs/` |
| 策略专属的当前规则与参数说明 | 对应 `backend/skills/*/strategy.yaml` 或 `SKILL.md` |
| 单轮工作日志、运行状态和 JSONL 记录 | `.workbuddy/memory/`、项目外 self-improve state/run records |

如果某份证据产生了跨项目可复用的规则，应在 `LESSONS.md` 新增条目，并在“证据”字段链接原始文件；不要把同一条永久规则复制到多个报告或脚本注释中。

## 条目格式

每条教训使用唯一的 `L-###` 编号，并按以下字段组织：

```markdown
## L-### 标题

- 状态：active | encoded | superseded
- 适用：适用的流程、模块或决策范围
- 证据：可复核的文件、测试、commit、查询或实验结果
- 永久规则：下一次遇到同类问题时必须遵守的动作
- 自动护栏：已有代码、测试或检查（没有则省略）
- 吸收位置：规则已经进入的提示词、代码、测试或操作手册
- 相关：相关教训或证据（没有则省略）
```

编号只能分配一次。新条目追加到文件末尾；修订旧条目时保留原编号，并在状态或正文中说明变更原因。被更准确结论取代、但原证据合格的条目标记为 `superseded`。经可靠性审计确认“证据不合格/无法复现”并获用户确认的条目可从注册表删除，但编号永久退役，不得复用；原文只通过 Git 历史追溯。

## 工作流

```text
发现问题/反例
    ↓
在证据档案中记录复现与结论
    ↓
提炼为一条可执行规则，追加到 LESSONS.md
    ↓
把规则吸收到代码、测试、命令或操作手册
    ↓
验证后将状态更新为 encoded；被替代时标记 superseded
```

新增教训前先搜索已有编号、标题和相关关键词，避免重复登记；一次运行的临时数字写入结构化运行记录，不把运行日志膨胀成长期教训。

可靠性审计与条目状态分开看：`encoded` 只表示规则已经进入代码/测试/流程，不表示原始收益数字永远有效；具体可信度以 [`VALIDATION.md`](./VALIDATION.md) 为准。

## 查询入口

```bash
rg -n '^## L-' docs/lessons/LESSONS.md
rg -n '教训|经验|踩坑|根因|永久规则' docs/lessons/LESSONS.md docs/bugs docs/reviews
```
