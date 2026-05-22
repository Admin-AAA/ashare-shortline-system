# AShare Shortline System

A 股短线推荐研究工作台，支持多数据源行情采集、多技能策略打分、本地行情池缓存、结果回溯统计。

核心目标：
- 用真实行情源生成候选与分数，正式推荐不使用样例数据
- 用 Skill（技能）做结构化选股与风控，保留每次审查的来源和 hash
- 每条推荐落库并自动回填结果
- 前端支持今日机会池、历史推荐、单票复盘、Skill 中心、统计看板
- 内置技术评分层，覆盖趋势、乖离、量能、支撑、MACD、RSI 等指标
- 统计按 Skill、行情状态、行业、买点拆分，便于实验、晋级和淘汰

免责声明：本系统仅用于研究和复盘，不构成投资建议。市场有风险，决策需自担。

## 技术栈

| 层 | 技术 |
|---|------|
| 后端框架 | Python 3.12 + FastAPI |
| 数据库 | MySQL + SQLAlchemy ORM |
| 前端 | React 19 + TypeScript + Vite |
| 图表 | ECharts 5 |
| 状态管理 | Zustand 5 |
| 行情数据 | THS SDK / 腾讯 / 东方财富 / AkShare / 新浪 / BaoStock |

## 目录结构

```
ashare-shortline-system/
├── start.sh                    # 一键启动脚本
├── README.md
├── backend/
│   ├── app/
│   │   ├── main.py             # FastAPI 入口，路由注册
│   │   ├── core/
│   │   │   ├── config.py       # 配置管理（环境变量 ASHARE_ 前缀）
│   │   │   └── logger.py       # 日志配置（文件轮转 + 控制台）
│   │   ├── db/
│   │   │   ├── session.py      # SQLAlchemy 引擎与连接
│   │   │   └── init_db.py      # 建表
│   │   ├── models/
│   │   │   └── tables.py       # 所有 ORM 模型（22 张表）
│   │   ├── api/                # FastAPI 路由层
│   │   │   ├── home.py         # 首页 bootstrap
│   │   │   ├── runs.py         # 推荐运行
│   │   │   ├── recommendations.py
│   │   │   ├── skills.py       # 技能管理与安装
│   │   │   ├── presets.py      # 预设组合
│   │   │   ├── data_sources.py # 数据源管理
│   │   │   ├── stats.py        # 统计数据
│   │   │   ├── outcomes.py     # 结果标注
│   │   │   └── metrics.py      # 指标查询
│   │   ├── services/           # 业务逻辑层
│   │   │   ├── market_data.py  # 行情数据采集（多源）
│   │   │   ├── market_pool.py  # 本地统一行情池（缓存 + 刷新）
│   │   │   ├── workbench_service.py  # 核心编排
│   │   │   ├── feature_builder.py    # 特征工程
│   │   │   ├── indicator_computer.py # 技术指标计算
│   │   │   ├── research_context.py   # Agent 审查上下文
│   │   │   ├── data_sources.py # 数据源配置管理
│   │   │   ├── outcome_service.py    # 结果标注
│   │   │   ├── stats_service.py      # 统计聚合
│   │   │   └── calibrator.py  # 权重校准
│   │   ├── engine/             # 策略引擎
│   │   │   ├── skill_loader.py # 技能加载（SKILL.md + strategy.yaml）
│   │   │   ├── strategy_runner.py    # 数值策略执行
│   │   │   ├── agent_skill_runner.py # LLM Agent 技能执行
│   │   │   ├── aggregator.py  # 多技能信号聚合
│   │   │   └── evaluator.py    # 安全表达式求值
│   │   └── agents/
│   │       └── research_graph.py  # 多角色研究 Agent
│   ├── skills/                 # 技能仓库
│   │   ├── chen-xiaoqun-skill/       # 陈小群龙头战法
│   │   ├── chao-gu-yang-jia-skill/   # 炒股养家大局观
│   │   └── daily-stock-analysis-skill/  # 技术评分
│   ├── tests/
│   └── logs/                   # 运行日志（自动创建，Git 忽略）
├── frontend/
│   ├── src/
│   │   ├── App.tsx             # 主应用（SPA 单文件）
│   │   ├── main.tsx            # React 入口
│   │   ├── types.ts            # TypeScript 类型
│   │   ├── api/client.ts       # API 客户端
│   │   └── styles/app.css      # 全局样式
│   ├── vite.config.ts
│   └── package.json
└── docs/
```

## 快速开始

### 一键启动

```bash
./start.sh
```

### 手动启动

后端：

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8765
```

前端：

```bash
cd frontend
npm install
npm run dev
```

- 前端：`http://localhost:5173`
- 后端 API：`http://localhost:8765`
- API 文档（Swagger）：`http://localhost:8765/docs`

## 架构

### 推荐运行流程

```
用户触发推荐扫描
    │
    ▼
┌─────────────────────────────┐
│  本地统一行情池 (market_pool)  │
│  ┌─────────────────────────┐ │
│  │ 行情缓存 (UnifiedQuote)  │ │  ← 8 小时 TTL，过期才刷新
│  │ K线缓存 (DailyBar)      │ │  ← 20 小时 TTL
│  └─────────────────────────┘ │
└─────────────────────────────┘
    │
    ▼
  候选股票筛选（默认使用全部合格股票；`ASHARE_MARKET_CANDIDATE_COUNT=0`）
    │
    ▼
  日K补齐与特征构建（默认 90 日）
    │
    ▼
┌─────────────────────────────┐
│  策略引擎                    │
│  ┌─────────────────────────┐ │
│  │ Skill 1: 陈小群龙头战法   │ │  numeric + agent review
│  │ Skill 2: 炒股养家大局观   │ │
│  │ Skill 3: 技术评分        │ │
│  └─────────────────────────┘ │
└─────────────────────────────┘
    │
    ▼
  信号聚合 (aggregator)
    │
    ▼
  分组 + 排序 → 推荐结果入库
```

### 本地行情池机制

系统内置本地统一行情池（`market_pool.py`），运行逻辑：

1. **仅本地池**：选择 `local_pool` 时只从 MySQL 本地池的 `UnifiedQuote` 和 `DailyBar` 读取，不自动请求外部行情源；本地报价或日K覆盖不足时直接停止
2. **本地池优先**：选择 `local_pool_preferred` 时先读本地池，本地池不足或日K未覆盖报价日期时才自动刷新外部源并写回本地池
3. **仅实时数据源**：选择 `realtime` 时直接访问 THS/腾讯/东方财富等外部真实源，不依赖本地池缓存
4. **显式补池**：本地数据池页面的“补齐本地池/拉取外部源”会主动刷新外部源；系统不会使用模拟数据

### API 概览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| GET | `/api/home/bootstrap` | 首页状态（行情快照 + 技能 + 最近运行） |
| GET | `/api/presets` | 预设组合列表 |
| POST | `/api/presets` | 创建预设 |
| PATCH | `/api/presets/{id}` | 更新预设 |
| GET | `/api/skills` | 技能列表 |
| POST | `/api/skills/install` | 安装技能 |
| GET | `/api/skills/{id}` | 技能详情 |
| POST | `/api/skills/{id}/mode` | 设置技能模式（formal/experiment/disabled） |
| POST | `/api/skills/{id}/backtest` | 技能回测 |
| GET | `/api/runs` | 历史运行列表 |
| POST | `/api/runs` | 触发推荐扫描 |
| GET | `/api/runs/{id}` | 运行详情 |
| GET | `/api/runs/{id}/recommendations` | 运行推荐明细 |
| GET | `/api/recommendations/{id}` | 单条推荐详情 |
| POST | `/api/recommendations/{id}/watch` | 设置关注状态 |
| POST | `/api/recommendations/{id}/review` | 添加复盘笔记 |
| GET | `/api/data-sources` | 数据源列表 |
| GET | `/api/data-sources/pool` | 本地数据池状态 |
| POST | `/api/data-sources/pool/refresh` | 显式拉取外部源刷新本地数据池 |
| POST | `/api/data-sources/{type}/{source}/enabled` | 启用/禁用数据源 |
| GET | `/api/stats/overview` | 统计概览 |
| GET | `/api/stats/by-skill` | 按技能统计 |
| GET | `/api/stats/by-preset` | 按预设统计 |
| POST | `/api/outcomes/{id}/label` | 标注单条结果 |
| POST | `/api/outcomes/label` | 批量标注结果 |
| GET | `/api/metrics/overview` | 指标概览 |

## 配置项

所有配置通过环境变量设置，前缀 `ASHARE_`。可在 `backend/.env` 文件中配置。

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `ASHARE_DATABASE_URL` | `mysql+pymysql://user:password@host:3306/ashare_workbench?charset=utf8mb4` | 数据库连接（仅支持 MySQL） |
| `ASHARE_QUOTE_SOURCES` | `thsdk,eastmoney,akshare,tencent,sina` | 报价数据源优先级 |
| `ASHARE_KLINE_SOURCES` | `tencent,thsdk,eastmoney,akshare` | K线数据源优先级 |
| `ASHARE_MARKET_CANDIDATE_COUNT` | `300` | 候选股票数量上限；`0` 表示使用全部合格股票 |
| `ASHARE_MARKET_KLINE_DAYS` | `90` | K线数据天数 |
| `ASHARE_MARKET_QUOTE_CACHE_TTL_SECONDS` | `28800` | 行情缓存有效期（秒），默认 8 小时 |
| `ASHARE_MARKET_KLINE_CACHE_TTL_HOURS` | `20` | K线缓存有效期（小时） |
| `ASHARE_MARKET_POOL_MIN_SOURCE_COUNT` | `2` | 行情多源交叉验证最少来源数 |
| `ASHARE_MARKET_REQUEST_TIMEOUT_SECONDS` | `10.0` | 外部 API 请求超时 |
| `ASHARE_THSDK_USERNAME` | `""` | THS SDK 用户名（留空用游客账号） |
| `ASHARE_THSDK_PASSWORD` | `""` | THS SDK 密码 |
| `ASHARE_THSDK_MAC` | `""` | THS SDK 设备 MAC |
| `ASHARE_AGENT_SKILL_RUNTIME` | `llm` | Agent 运行模式；必须为 `llm`，不支持本地 fallback |
| `ASHARE_AGENT_SKILL_API_KEY` | `""` | DeepSeek API Key，未配置会停止 Agent 运行 |
| `ASHARE_AGENT_SKILL_BASE_URL` | `https://api.deepseek.com` | DeepSeek OpenAI 兼容 API 地址 |
| `ASHARE_AGENT_SKILL_MODEL` | `deepseek-v4-pro` | LLM 模型 |
| `ASHARE_AGENT_SKILL_TIMEOUT_SECONDS` | `300.0` | Agno Skill Agent 单次审查超时（秒） |
| `ASHARE_MARKET_SKILL_REVIEW_CONCURRENCY` | `2` | Agno Skill 深审候选并发数 |
| `ASHARE_LOG_LEVEL` | `INFO` | 日志级别 |
| `ASHARE_LOG_DIR` | `backend/logs/` | 日志目录 |

## 数据源

正式运行使用真实行情源。当前支持：

- **THS SDK**：默认主源，支持全市场列表和实时行情；未配置账号时使用游客账号
- **腾讯**：默认报价和日K来源
- **新浪**：报价补充源
- **东方财富**：报价和日K增强源
- **AkShare**：补充源
- **BaoStock**：可选日K兜底源，需加入 `ASHARE_KLINE_SOURCES`

数据源可在前端界面或 API 中启用/禁用。如果所有数据源都失败，系统返回错误，不会回退模拟数据。推荐运行支持 `local_pool`、`local_pool_preferred`、`realtime` 三档；本地数据池页面会显示当前是否缺失数据，以及能否只靠本地池推荐股票。默认配置下会先筛出全部合格股票（`ASHARE_MARKET_CANDIDATE_COUNT=0`），再结合日K可用性、去重和预设 `output_count` 产出最终推荐结果。

## 技能系统

每个技能是一个独立目录，包含：

```
skill-name/
├── SKILL.md          # Agent 审查指令（可选）
└── strategy.yaml     # 数值策略规则（可选）
```

技能运行时模式：
- **rule**：仅执行 `strategy.yaml` 数值策略
- **agent**：仅执行 `SKILL.md` LLM 审查
- **dual**：数值策略 + LLM 审查双通道

Agent 审查强制使用真实 LLM：缺少 `ASHARE_AGENT_SKILL_API_KEY`、DeepSeek 请求失败或模型未按 schema 返回 JSON，都会使本次运行失败。

### 内置技能

| 技能 | 说明 |
|------|------|
| 陈小群龙头战法 | 聚焦龙头辨识、量价确认和情绪周期 |
| 炒股养家大局观 | 侧重宏观环境、市场结构和板块轮动 |
| Daily Stock Analysis | 技术指标综合评分（MACD/RSI/均线/量价） |

## 日志

系统使用 Python logging，运行时日志输出到：
- **文件**：`backend/logs/app.log`（自动创建，10MB 轮转，保留 5 个备份）
- **控制台**：stderr

错误和完整 traceback 会同时记录到文件和控制台。日志目录已加入 `.gitignore`。

## 参考

本地实现参考了以下开源项目的架构思想，但没有直接引入其完整代码：

- TradingAgents-CN：多角色研究、反方、风控、交易员的审查链路
- fin-agent：Tushare 数据接入、技术指标与自然语言金融工具
- daily_stock_analysis：多数据源行情、技术评分和本地仪表盘工作流
- FinRobot：面向金融任务的可审计 Agent/报告式输出
