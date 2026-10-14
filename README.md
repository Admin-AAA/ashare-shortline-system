# AShare Shortline System

A股短线交易驾驶舱，支持多数据源行情采集、多技能策略打分、本地行情池缓存、LLM Agent 审查、可解释推荐、结果回溯统计、自动化策略优化与数据健康监控。

**核心目标：**
- 用真实行情源生成候选与分数，正式推荐不使用样例数据
- 用 Skill（技能）做结构化选股与风控，保留每次审查的来源和 hash
- 每条推荐落库并自动回填结果，支持 T+1 结算与跨日持仓
- 自动化 self-improve 循环，持续优化策略参数
- 前端默认进入短线交易驾驶舱，聚合市场温度、主线强度、龙头候选、风险雷达、推荐运行、数据池健康、调度和复盘反馈
- 驾驶舱支持黑色/白色主题切换，主题选择保存在浏览器本地

**免责声明：** 本系统仅用于研究和复盘，不构成投资建议。市场有风险，决策需自担。

---

## 技术栈

| 层 | 技术 |
|---|------|
| 后端框架 | Python 3.12 + FastAPI 0.141 |
| 数据库 | MySQL + SQLAlchemy 2.0 + Alembic |
| 前端 | React 19 + TypeScript 5.8 + Vite 8 |
| 状态管理 | Zustand 5 |
| UI 图标 | lucide-react 0.508 |
| 可视化 | three.js 0.185（WebGL 动态背景） |
| 调度 | Prefect 3.8 |
| LLM | DeepSeek OpenAI 兼容接口 |
| 数据源 | TuShare（主源）+ AkShare + 东方财富 |
| 回测 | vectorbt 0.28 + pandera 0.32 |
| 测试 | pytest 9.0 |

---

## Demo Canonical-only 治理决策

本仓库当前是**本地 demo/研究复盘系统**，不是已发布的生产交易系统。自 2026-08-05 起，demo 采用以下明确决策：

- **唯一 canonical read/write path**：正式推荐、结果计算、历史查询和 dashboard 只使用当前 canonical schema、Canonical Execution Core 和 provenance-aware selectors。
- **不保留历史兼容承诺**：旧 projection、旧 replay 兼容分支、旧 execution 实现和旧结果不再作为 demo 的兼容目标；允许在本地 demo 数据库中清理旧推荐/旧 outcome 结果后重建。
- **清理范围**：只清理 demo 的推荐运行、推荐、旧 outcome/current projection 和相关运行审计结果；行情池、日 K、策略定义、治理 CAS evidence、migration 状态和配置审计不清理。
- **canonical-only flags**：六个 hardening runtime flags 在 demo 中可以直接进入 `ENFORCED`，并以治理对象记录 `ACTIVE`；这不等同于真实生产发布。
- **不宣称生产有效性**：即使 demo flags 全部激活，也不宣称策略具有正期望、不构成投资建议，也不代表完成真实生产环境发布。
- **生产验收仍独立**：真实 MySQL/Prefect evidence、Shadow Threshold、Projection Cutover 和 production-ready 判定保留为治理证据；demo canonical-only 切换不得伪造 Layer 2 ACCEPTED。

因此，demo 的目标是验证当前 canonical 系统能否干净运行，而不是继续兼容旧历史结果。

---

## 目录结构

```
ashare-shortline-system/
├── README.md
├── start.sh                     # 一键启动（FastAPI + Prefect）
├── ashare                       # CLI 入口
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 入口
│   │   ├── core/
│   │   │   ├── config.py        # 配置管理（ASHARE_ 前缀）
│   │   │   └── logger.py        # 日志配置
│   │   ├── db/
│   │   │   ├── session.py       # SQLAlchemy 引擎
│   │   │   └── migrations/     # Alembic 迁移
│   │   ├── models/
│   │   │   └── tables.py        # ORM 模型（约 50 张表）
│   │   ├── api/                 # FastAPI 路由
│   │   │   ├── home.py          # 首页 bootstrap
│   │   │   ├── runs.py          # 推荐运行
│   │   │   ├── recommendations.py
│   │   │   ├── skills.py        # 技能管理
│   │   │   ├── presets.py       # 预设组合
│   │   │   ├── data_sources.py  # 数据源管理
│   │   │   ├── stats.py         # 统计数据
│   │   │   ├── outcomes.py      # 结果标注
│   │   │   ├── metrics.py       # 指标查询
│   │   │   ├── scheduler.py     # Prefect 调度
│   │   │   ├── backtest.py      # 回测
│   │   │   ├── self_improve.py  # 自动化优化
│   │   │   └── llm_review.py    # LLM 审查
│   │   └── services/            # 业务逻辑层
│   │       ├── workbench_service.py      # 核心编排
│   │       ├── market_data.py            # 行情数据采集
│   │       ├── market_pool/              # 本地行情池
│   │       ├── tushare_client.py          # Tushare 主源
│   │       ├── scheduler_service.py      # 调度服务（Prefect）
│   │       ├── outcome_service.py         # 结果标注结算
│   │       ├── outcome_simulation.py      # 结果模拟推演
│   │       ├── calibrator.py              # 权重校准
│   │       ├── data_health_service.py    # 数据健康监控
│   │       ├── data_gap_remediation.py   # 数据补齐
│   │       ├── backtest_service.py       # 回测服务
│   │       ├── capital_flow_factor_evidence.py  # 资金流因子
│   │       └── ...
│   ├── skills/                 # 技能仓库（11 个目录 + 2 个研究原型，2026-10-13）
│   │   ├── chao-gu-yang-jia-skill/       # 炒股养家大局观（正式，默认策略）
│   │   ├── chen-xiaoqun-skill/           # 陈小群龙头战法（正式）
│   │   ├── sector-relative-strength-leader/ # 主线相对强度龙头（正式）
│   │   ├── high-range-breakout-momentum/ # 阶段新高放量突破（正式）
│   │   ├── chip-single-peak-breakout/    # 筹码单峰突破（正式）
│   │   ├── high-position-volume-stall-warning/ # 高位放量滞涨（风险提示，非选股）
│   │   ├── dragon-tiger-reaccumulation/  # 龙虎榜整理再启动（研究，未启用）
│   │   ├── oversold-capital-reversal/    # 超跌资金反包（研究原型，未启用）
│   │   └── daily-stock-analysis-skill/ trend-pullback-continuation/ overnight-holding-method/
│   │                                     # 2026-10-13 串行消融后已下线（enabled=false，保留定义供复现）
│   ├── tests/
│   ├── scripts/                # 运维/研究脚本；一次性脚本已归档到 scripts/archive/
│   │   ├── ashare_cli.py        # CLI 底层入口
│   │   ├── run_prefect_server.py
│   │   └── run_prefect_worker.py
│   └── logs/                    # 运行时日志
├── frontend-v2/
│   ├── src/
│   │   ├── App.tsx              # 主应用入口
│   │   ├── main.tsx             # React 入口
│   │   ├── types.ts             # TypeScript 类型
│   │   ├── app/                 # 驾驶舱 shell、鉴权、导航、API
│   │   ├── components/         # 通用组件（含 WaveBackground）
│   │   ├── pages/               # 页面视图
│   │   ├── shared/theme/        # 主题系统
│   │   ├── api/                 # API 客户端
│   │   └── styles/              # CSS 样式
│   ├── vite.config.ts           # 端口 5174
│   └── package.json
└── scripts/                     # 辅助脚本
```

---

## 快速开始

### 一键启动

```bash
./start.sh
```

会启动：
- FastAPI 服务（端口 8765）
- Prefect Server（端口 4200）
- Prefect Worker（后台执行调度任务）

### 手动启动

后端：

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8765
```

Prefect 调度（另开终端）：

```bash
cd backend
.venv/bin/python -m scripts.run_prefect_server
.venv/bin/python -m scripts.run_prefect_worker
```

前端：

```bash
cd frontend-v2
npm install
npm run dev
```

访问：
- 前端：`http://localhost:5174`
- 后端 API：`http://localhost:8765`
- API 文档：`http://localhost:8765/docs`
- Prefect Dashboard：`http://localhost:4200`

### CLI 工具

项目根目录提供 `./ashare` 命令：

```bash
./ashare presets --active-only --pretty                 # 查看可执行预设
./ashare execute-preset <preset_id> --wait --pretty     # 同步执行预设
./ashare runs --limit 10 --pretty                       # 查看最近运行
./ashare recommendations <run_id> --limit 20 --pretty   # 查看推荐
./ashare data-health --pretty                           # 查看数据健康
./ashare self-improve status --pretty                   # 查看优化状态
./ashare matrix-run --table --pretty                    # 执行矩阵任务
```

全局安装：

```bash
ln -sf /Users/wangao/Desktop/ashare-shortline-system/ashare ~/.local/bin/ashare
```

---

## 架构

### 推荐运行流程

```
用户触发 / 定时调度
    │
    ▼
┌─────────────────────────────┐
│  本地统一行情池 (market_pool)  │
│  ┌─────────────────────────┐ │
│  │ 报价缓存 (StockQuote)   │ │  ← TTL 默认 300 秒
│  │ 日K缓存 (DailyKbar)     │ │  ← TTL 默认 20 小时
│  │ 辅助数据缓存            │ │  ← 融资融券/龙虎榜/资金流
│  └─────────────────────────┘ │
└─────────────────────────────┘
    │
    ▼
  候选股票筛选（宽召回 6000 只，全量进入策略层）
    │
    ▼
  日K补齐与特征构建（默认 90 日）
    │
    ▼
┌─────────────────────────────┐
│  策略引擎                    │
│  ┌─────────────────────────┐ │
│  │ Skill × 14              │ │  ← rule / agent / dual 模式
│  │ 因子计算（MA/量比/乖离/ │ │
│  │   RSI/MACD/资金流/筹码   │ │
│  └─────────────────────────┘ │
└─────────────────────────────┘
    │
    ▼
  信号聚合 (aggregator)
    │
    ▼
  权重校准 (calibrator)
    │
    ▼
  分组 + 排序 → 推荐结果入库
    │
    ▼
  LLM 审查（可选）
    │
    ▼
  T+1 结算 (outcome_service)
```

### 调度（Prefect）

- **定时任务**：盘前（09:00）、盘中（11:30/13:00/14:30）、盘后（15:30/18:20）
- **任务类型**：推荐扫描、数据同步、健康检查、结果结算、自优化
- **Dashboard**：`http://localhost:4200`

### 本地行情池

三种模式：
- `local_pool`：仅读本地，不请求外部
- `local_pool_preferred`：先读本地，不足时刷新外部
- `realtime`：直接访问外部源

---

## 技能系统

每个技能是独立目录，包含：

```
skill-name/
├── SKILL.md          # Agent 审查指令（可选）
└── strategy.yaml     # 数值策略规则（可选）
```

运行模式：
- **rule**：仅执行数值策略
- **agent**：仅执行 LLM 审查
- **dual**：数值策略 + LLM 审查双通道

### 内置技能（14 个）

| 技能 | 说明 |
|------|------|
| `chen-xiaoqun-skill` | 陈小群龙头战法 |
| `chao-gu-yang-jia-skill` | 炒股养家大局观 |
| `daily-stock-analysis-skill` | 技术指标综合评分 |
| `dragon-tiger-smart-money` | 龙虎榜聪明钱接力 |
| `high-range-breakout-momentum` | 阶段新高放量突破 |
| `overnight-holding-method` | 隔夜持股法 |
| `sector-relative-strength-leader` | 主线相对强度龙头 |
| `trend-pullback-continuation` | 强趋势缩量回踩 |
| `chip-single-peak-breakout` | 芯片单峰突破 |
| `high-position-volume-stall-warning` | 高位放量滞涨 |
| `small-cap-thematic-momentum` | 小盘题材动量 |
| `weak-market-strong-stock-reversal` | 弱市强股反转 |

---

## API 概览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| GET | `/api/home/bootstrap` | 首页状态 |
| GET | `/api/dashboard/overview` | 驾驶舱聚合 |
| GET/POST/PATCH | `/api/presets` | 预设管理 |
| GET | `/api/skills` | 技能列表 |
| POST | `/api/skills/install` | 安装技能 |
| POST | `/api/skills/{id}/mode` | 设置模式 |
| POST | `/api/skills/{id}/backtest` | 技能回测 |
| GET/POST | `/api/runs` | 推荐运行 |
| GET | `/api/runs/{id}` | 运行详情 |
| GET | `/api/recommendations/{id}` | 推荐详情 |
| POST | `/api/recommendations/{id}/watch` | 关注/取消关注 |
| POST | `/api/recommendations/{id}/review` | 复盘笔记 |
| GET/POST | `/api/outcomes` | 结果标注 |
| GET | `/api/stats/*` | 统计数据 |
| GET | `/api/backtest/*` | 回测 |
| GET/POST | `/api/self-improve/*` | 自优化 |
| GET | `/api/llm-review/*` | LLM 审查 |
| GET | `/api/scheduler/*` | 调度管理 |
| GET | `/api/data-health/*` | 数据健康 |
| GET/POST | `/api/data-sources/*` | 数据源管理 |
| GET | `/api/metrics/*` | 指标查询 |

完整文档见 `/api/docs`。

---

## 配置项

所有配置通过环境变量设置，前缀 `ASHARE_`。可在 `backend/.env` 中配置。

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `ASHARE_DATABASE_URL` | `mysql+pymysql://aowang:${ASHARE_DB_PASSWORD}@127.0.0.1:3306/ashare_workbench` | 数据库连接 |
| `ASHARE_QUOTE_SOURCES` | `tushare,akshare,eastmoney,tencent,sina` | 报价源优先级 |
| `ASHARE_KLINE_SOURCES` | `tushare,tencent,eastmoney,akshare` | K线源优先级 |
| `ASHARE_MARKET_CANDIDATE_COUNT` | `0` | 候选数量上限，`0`=全量召回 |
| `ASHARE_MARKET_KLINE_DAYS` | `90` | K线天数 |
| `ASHARE_MARKET_QUOTE_CACHE_TTL_SECONDS` | `300` | 报价缓存 TTL |
| `ASHARE_MARKET_KLINE_CACHE_TTL_HOURS` | `20` | K线缓存 TTL |
| `ASHARE_TUSHARE_PROMAX_API_KEY` | - | Promax 聚合源 API Key（仅放 `backend/.env`） |
| `ASHARE_TUSHARE_PROMAX_RATE_LIMIT_PER_MINUTE` | `180` | Promax 客户端限速 |
| `ASHARE_AGENT_SKILL_API_KEY` | - | DeepSeek API Key |
| `ASHARE_AGENT_SKILL_MODEL` | `deepseek-chat` | LLM 模型 |
| `ASHARE_LOG_LEVEL` | `INFO` | 日志级别 |

---

## 数据源

- **TuShare Router**：Promax 等多 Provider 自动路由与容灾，提供日K、技术指标、筹码分布、融资融券、大宗交易、北向资金
- **AkShare**（补充）：行情数据备用
- **东方财富**：实时行情备用

数据源可在前端或 API 中启用/禁用。所有源失败时返回错误，不使用模拟数据。

---

## 自动化 Self-Improve

系统支持自动化策略优化循环：

```bash
./ashare self-improve status --pretty    # 查看优化状态
./ashare self-improve launch --pretty    # 发起新轮次
./ashare self-improve results --pretty   # 查看优化结果
```

优化内容包括：
- 因子权重校准
- 入场/出场参数
- 过滤规则
- 板块轮动

## 教训与经验沉淀

项目中可跨轮复用、会改变后续决策的教训，统一存放在 [`docs/lessons/LESSONS.md`](docs/lessons/LESSONS.md)，具体格式和边界见 [`docs/lessons/README.md`](docs/lessons/README.md)；可靠性核验见 [`docs/lessons/VALIDATION.md`](docs/lessons/VALIDATION.md)。

存放边界如下：

- `docs/lessons/LESSONS.md`：唯一教训注册表，只收录有证据、可复用、能改变行动的规则。
- `docs/bugs/`、`docs/reviews/`、根目录诊断报告、`output/`、回测报告：证据档案；教训条目从这里回链，不在这些位置另建长期规则。
- `backend/skills/*/strategy.yaml`、`SKILL.md`：策略自身的当前规则；跨策略或跨轮的经验仍回写教训注册表。
- `.workbuddy/memory/`：本地工作记忆和日志，不是第二个教训库；self-improve 的状态与运行记录继续按操作手册存放。

新增教训前先搜索已有条目，使用唯一 `L-###` 编号，并记录“状态、适用、证据、永久规则、吸收位置”。单次运行数字和普通实验结果不要直接升级为长期教训。

---

## 回测

```bash
./ashare backtest run --skill <skill_id> --start 2024-01-01 --end 2025-07-30 --pretty
./ashare backtest compare --pretty
```

使用 vectorbt 进行事件驱动回测，支持：
- 单技能回测
- 多技能组合
- 参数网格搜索
- 组合绩效分析

---

## 日志

- 文件：`backend/logs/app.log`（10MB 轮转，保留 5 个）
- 控制台：stderr
- Prefect：`backend/logs/prefect.log`

---

## 常见运维

### 重启后端服务

```bash
launchctl kickstart -k gui/$(id -u)/com.wangao.ashare-backend
```

### 检查健康

```bash
curl -sS http://127.0.0.1:8765/api/health
./ashare readiness --pretty
```

### 数据健康

```bash
./ashare data-health --pretty
./ashare pool-status --summary-only --pretty
```

### 治理与证据（仓库调研采纳项）

以下 CLI 复用 immutable governance store（append-only index + 内容寻址对象），只读或幂等写入，审计留痕。

```bash
# P0-1 横截面因子评估（IC/RankIC/分层收益/单调性，只读精选 canonical outcome）
PYTHONPATH=backend backend/.venv/bin/python backend/scripts/analyze_factor_cross_section.py \
  --factor pos20 --start-date 2026-07-01 --end-date 2026-08-06 --horizon-days 5 \
  --quantiles 5 --min-cross-section-size 5 --actor <name> --code-revision <sha> \
  --output output/factor_analysis/pos20.json
# 样本不足时 evidence_status=insufficient（不强行给结论）；同 request 重跑幂等 reused=true。

# P0-3 策略/preset 注册遗漏诊断（只读；formal run 前 preflight 复用同一 validator，fatal 即 fail-closed）
./ashare registry-diagnostics --pretty
./ashare registry-diagnostics --preset-id preset_formal_opening --pretty   # 针对单个 preset 校验可执行性

# P1-1 贝叶斯收缩策略权重建议（shadow-only，不写 YAML、不改 Signal.weight/排序）
PYTHONPATH=backend backend/.venv/bin/python backend/scripts/produce_strategy_weight_shadow.py \
  --created-by <name> --code-revision <sha>
# 样本不足如实标记 insufficient，不放宽门槛。

# P1-4 数据源 fallback trace（统一 append-only trace；复用现有 Provider Router，无第二套）
curl -sS http://127.0.0.1:8765/api/data-sources/traces --pretty
curl -sS http://127.0.0.1:8765/api/data-sources/traces/fallback-chains --pretty
curl -sS http://127.0.0.1:8765/api/data-sources/traces/health --pretty

# P2-5 版本化 architecture decision（Markdown 仅投影，权威源是 store）
PYTHONPATH=backend backend/.venv/bin/python backend/scripts/decision_record.py \
  --actor <name> --code-revision <sha> --created-at <ISO-8601 带时区> list --status accepted
PYTHONPATH=backend backend/.venv/bin/python backend/scripts/decision_record.py \
  --actor <name> --code-revision <sha> --created-at <ts> show <object_id>
PYTHONPATH=backend backend/.venv/bin/python backend/scripts/decision_record.py \
  --actor <name> --code-revision <sha> --created-at <ts> verify <object_id>
PYTHONPATH=backend backend/.venv/bin/python backend/scripts/decision_record.py \
  --actor <name> --code-revision <sha> --created-at <ts> render <object_id> --output decision.md
```

### 策略半年回测画像（/strategies 页面数据源）

页面读取 `StrategyBacktestProfiles`（experiment_id=`strategy-backtest-profile:v1`，与 LIVE formal 完全隔离，窗口=最近可完全结算信号日往前 180 天）。窗口随时间漂移，需幂等刷新以重新对齐：

```bash
# 幂等：已存在精确画像则 reuse；可 --strategy-id / --as-of / --max-runs 限定
PYTHONPATH=backend backend/.venv/bin/python backend/scripts/refresh_strategy_backtest_profiles.py
# 只写 BacktestRunV2/BacktestTrade/DataSnapshot 三表，不碰 recommendations/presets；
# 同窗口+同数据需求的策略共享 immutable snapshot。
```

---

## 参考项目

- TradingAgents-CN：多角色研究 Agent
- fin-agent：Tushare 数据接入
- FinRobot：金融任务 Agent


---

## 研究纪律与运维告警（2026-10-13）

- **Hold-out 协议**：2026-07-01 之后为样本外，调参只用之前的数据，样本外只评估一次，两段都通过 `evaluate_challenger` 才发布。见 `docs/holdout-protocol.md`。
- **决策时刻可见性**：龙虎榜、两融明细在 17:35 正式推荐时还没入库，实盘和回放统一读取信号日之前最近一个交易日的数据（L-163 / L-167）。
- **告警**：LLM 熔断、Tushare 全部上游失败、未配置官方 Tushare 兜底时，`backend/logs/app.log` 会记录 `[ALERT:<kind>]`，可用 `grep '\[ALERT:' backend/logs/app.log` 查看。官方兜底需在 `.env` 设置 `ASHARE_TUSHARE_OFFICIAL_TOKEN`。
- **组合回撤控制**：`app/engine/drawdown_control.py`（弱市限额 + 已结算亏损熔断），默认关闭：训练段过判、hold-out 段证伪，见 `docs/lessons/LESSONS.md` L-167。

## 单策略独立运行（2026-10-14 起，取代全策略组合）

- **不再有组合推荐**：盘后 17:35 Prefect 任务（`post_sync_formal_recommendation`）同步数据、过健康闸门、结算 outcome 之后，
  对每个有效策略各自派发一个 formal run（`single_skill_id`），每个策略有自己的推荐、T+1 结算和统计。
  分批并发 4 个，单个策略失败不影响其它策略；结果明细在任务 `result.strategy_runs`。
- **参与策略**：`app/services/standalone_strategies.py` 是唯一定义——enabled、可执行、非退役（基础短线 / 强趋势回踩 / 隔夜法 /
  龙虎榜再吸筹保持退役）、非风险提示技能。不再用组合启用/禁用闸门筛选新策略。
- **当前每日独立运行的 8 个策略（2026-10-14，L-170）**：炒股养家、陈小群、筹码单峰突破、高位区间突破、板块相对强势龙头、
  超跌资金反转、主线板块补涨、低MAX稳健趋势。已删除（近12月单策略均值为负、轻量调参无法转正）：涨停回封接力、低位首板、
  龙头二板接力、行业内短期反转、龙虎榜净买次日跟随、北向资金增持、机构调研关注。
- **每个策略的阈值/上限**：默认分数线 72（formal 地板）、每天最多 3 张；按策略覆盖写在 `STRATEGY_RUN_OVERRIDES`。
- **单策略政策**：在生产 v27 基础上关掉组合专属的共识闸门（弱势/震荡 ≥2 策略背书，对单策略恒不可达）和共享的 6 张上限；
  其余风险否决保留。
- **异步 LLM 风险备注（L-171）**：正式 run 管线内关闭 LLM，先出票；出票后对已推荐股票异步审查，结果写入 `agent_summary_json` / 风险标签，不改分数、不否决出票。
- **跨 run 去重只在同一策略内**：同日重复、近 7 日重复、行业集中只与同一策略的历史 run 比较，不同策略可以推同一只票。
- **看板与统计**：驾驶舱「单策略」面板显示最近交易日每个策略的 run 状态与出票；绩效页 `GET /api/metrics/by-strategy`
  按策略统计（每条推荐只属于一个策略）。安全网补跑同样按单策略派发。
- 多技能 run 仍可在工作台手动发起（研究/对照用），但不再由调度产生。
