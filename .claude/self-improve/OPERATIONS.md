# Self-improve 操作手册（接口契约）

研究员执行 `/loop 30m /self-improve` 时按需查阅本文件。主命令只讲行为规则，本文件是系统接口契约：子命令、状态字段、发布流程、记录格式。

## 路径

- 后端：`backend/`（Python 3.12、FastAPI、MySQL）
- 前端：`frontend-v2/`
- 自检：`backend/.venv/bin/python scripts/self_check.py`
- 唯一生产判定器：`backend/app/services/backtest/challenger_decision.py`
- 串行重放：`backend/scripts/si_sequential_champion_replay.py`
- 原子状态工具：`backend/scripts/self_improve_state.py`
- 教训注册表（全局唯一真源）：`~/.claude/projects/-Users-wangao/memory/self-improve-LESSONS.md`
- 运行状态：`/Users/wangao/.claude/projects/-Users-wangao/memory/self-improve-state.json`
- 结构化记录：`/Users/wangao/.claude/projects/-Users-wangao/memory/self-improve-runs.jsonl`
- 旧日志：`/Users/wangao/.claude/projects/-Users-wangao/memory/self-improve-log.md`（只读历史档案，不再追加）

## 状态工具子命令

统一入口：`backend/.venv/bin/python backend/scripts/self_improve_state.py <子命令>`

| 子命令 | 用途 | 关键参数 |
|---|---|---|
| `read` | 读当前状态 | 无 |
| `claim-direction` | 原子 claim 一个方向 | `--researcher-id`（必填）、`--max-workers`、`--preferred-direction`、`--force-stale` |
| `release-direction` | 释放方向 + 终止心跳 daemon | `--researcher-id` |
| `prune-claimed` | 清理失活研究员残留方向 | 无 |
| `setup-worktree` | 建独立 worktree 并注册 | `--researcher-id`、`--base-ref`（默认 main） |
| `release-worktree` | 清理 worktree | `--researcher-id`、`--remove` |
| `daemon-heartbeat` | 后台心跳 daemon（阻塞） | `--researcher-id`、`--interval`、`--max-duration` |
| `stop-daemon` | 终止心跳 daemon | `--pid` |
| `heartbeat` | 单次心跳 | `--researcher-id` |
| `publish-champion` | CAS 发布新 champion | `--researcher-id`、`--expected-champion-commit`、`--state-patch`、`--run-record` |
| `publish-finding` | 追加 finding | `--researcher-id`、`--state-patch`、`--finding`、`--run-record` |
| `with-publish-lock` | 发布锁下执行命令 | `--cmd`（单个引用参数）、`--timeout` |

### 常用工作流

**开局**（每轮必做）：
```
python self_improve_state.py prune-claimed
python self_improve_state.py claim-direction --researcher-id si-<时间戳> --max-workers 3
python self_improve_state.py setup-worktree --researcher-id si-<时间戳> --base-ref main
python self_improve_state.py daemon-heartbeat --researcher-id si-<时间戳> --interval 900 --max-duration 10800 &
cd <worktree_path>
```

**收尾**（每轮必做）：
```
python self_improve_state.py publish-champion / publish-finding   # 发布
python self_improve_state.py release-direction --researcher-id si-<时间戳>
python self_improve_state.py release-worktree --researcher-id si-<时间戳> --remove
```

**发布锁合并**（PRODUCTION 时）：
```
python self_improve_state.py with-publish-lock --cmd 'git -C <主仓库> pull --ff-only origin main && git -C <主仓库> merge --no-ff <branch> && git -C <主仓库> push origin main'
```

### worktree 内用 venv

`.venv` 不随 worktree 复制。worktree 内 Python 一律用主仓库绝对路径：
```
/Users/wangao/Desktop/ashare-shortline-system/backend/.venv/bin/python scripts/self_check.py
```
或 `export VENV_PY=/Users/wangao/Desktop/ashare-shortline-system/backend/.venv/bin/python`。

## 状态文件字段

`self-improve-state.json` 顶层：
- `schema_version`：布局版本（当前 2）
- `canonical_production`：当前 champion（commit、metrics{n,win_rate_pct,average_net_return_pct,profit_factor}、provisional、rules）
- `researcher_registry.active_researchers`：活跃研究员（researcher_id、claimed_direction、last_heartbeat、heartbeat_daemon_pid、worktree_path、status）
- `researcher_registry.claimed_directions`：已被 claim 的方向
- `archived_falsified_directions`：已归档否证方向
- `scanned_grids`：已扫描参数网格
- `published_findings`：已发布 findings
- `blocked_until`、`next_event`：LIVE 事件窗口

## 记录格式（self-improve-runs.jsonl）

每轮追加一条合法 JSON（`run_id`、`researcher_id`、`claimed_direction`、`started_at`、`input_fingerprint`、`champion`、`hypothesis`、`data_source`、`windows`、`metrics`、`decision`、`commit`、`rollback_point`、`lesson_ids`、`blocked_live_until`、`next_live_event`）。状态和运行记录必须通过 `publish-champion`/`publish-finding` 在同一文件锁下更新，禁止直接写状态文件。

## 提交信息格式

候选被否证时不改生产代码。代码、测试和验证放同一提交：
```text
self-improve: T{编号} {简短描述}

- 主假设与改动
- 验证结果及核心指标对比
```

## 安全纪律

- 禁止 force push、禁止 `git reset --hard`
- 不得删除表、清空数据、修改 `.env`、在 formal 运行时重启后端
- 测试用 `backend/.venv`
