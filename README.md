# A股交易系统（ashare-shortline-system）

本地运行的 A 股选股与复盘系统，分短线、中线、长线三个周期。每个策略每天盘后独立出票，次日开盘买入，按各自规则持有、结算和统计。

> 仅用于研究和复盘，不构成投资建议。

---

## 一、现役策略（2026-11-27）

上线不靠感觉：先在 `docs/strategy-research.md` 预注册规则，回测扣成本后证据充分才上线，不达标就进淘汰列表，不事后放宽。

| 周期 | 持有 | 现役策略 | 代码位置 |
|---|---|---|---|
| 短线 | T+1 起按策略离场 | 炒股养家、超跌资金反包、主线相对强度龙头、恐慌出清 RSI6+QVIX（观察），共 4 个 | `backend/skills/*/strategy.yaml`，参与名单与退役名单见 `backend/app/services/standalone_strategies.py` |
| 中线 | 多为 20 日（弱反应/深跌类 5–10 日） | 44 个：小市值系 5 个周度策略（含小盘高股息）、回购预案弱反应（持 20 与持 5）、回购×激励深跌、大比例解禁落地×解禁前已跌（L-241）、业绩加速放量、业绩加速深跌、预喜合集、预告弱反应及预增弱反应放量、预增放量、扭亏、略增及其放量/弱反应版、期权激励及放量版、大宗溢价、销售费用率下降及放量版、财务费用率放量、分红增加及放量版、分红预案深跌、流动资产占比放量、业绩加速×营业周期放量、净现金×营业周期放量、有形资产与权益/负债双改善放量、SUE 盈余加速基线及十条质量轴变体 | `backend/app/services/midterm_service.py` 的 `STRATEGIES` |
| 长线 | 60 日 | 红利低波 | 同上 |

- 策略上线与去留按全期扣费净收益（减 0.07% 往返税费）、同期同持有期全市场等权基准的超额、跨月符号一致性和样本量综合判断，没有固定数字门槛。
- 被拒/退役策略：中线退役定义在 `midterm_service.py` 的 `RETIRED_STRATEGIES`（模块加载时从现役字典移出，定义与历史选股全部保留），长线/中线被拒清单见 `backend/app/api/midterm.py` 的 `MID_REJECTED` / `LONG_REJECTED`；短线退役名单是 `standalone_strategies.py` 的 `RETIRED_STRATEGY_IDS`。退役策略的目录和代码保留，便于复现。
- 新上线策略会自动回补近 3 个月的回放记录，标记 `origin=replay`，不和实盘（`live_forward`）混在一起统计。
- 长线研究目前暂停，已上线的长线策略照常运行。

### 每策略每日推荐上限

- 每个策略每天最多取 **前 5 只**（`ASHARE_MAX_DAILY_PICKS_PER_STRATEGY`，默认 5，0 = 不限）。
- 同分顺延：和第 5 名同分的一起出。例：100,100,90,90,90,80 → 5 只；10 只全满分 → 10 只。
- 中长线的实盘出票和历史回放重建都套这个上限；短线只套在实盘 formal run，短线回测回放不受影响。
- 这是当天新推荐数，不是持仓上限。已经落盘的旧回放文件不会自动变少，重跑步骤见 `docs/ops/daily-pick-cap-20261026.md`。
- 短线还有一道更早的出票上限：每个策略每次 run 最多 3 张（`DEFAULT_OUTPUT_COUNT`，高潮日减半；恐慌出清按研究口径一次 10 张）。
- 详见 `docs/ops/daily-pick-cap-20261026.md`，实现在 `backend/app/services/daily_pick_cap.py`。

---

## 二、每日流程

业务任务由 Prefect 调度（工作日，北京时间），守护类任务由 launchd 直接定时拉起。

### Prefect 任务

| 时间 | 任务 |
|---|---|
| 08:03 | 大宗交易盘前备份（补上一交易日） |
| 08:10 / 17:10 | 股票身份数据刷新（名称、ST、上市状态，三方对账） |
| 08:30 | 正式推荐安全网：检查最近 5 个交易日的出票缺口并补跑 |
| 08:50 / 22:50 | 数据质量巡检（只读）；08:55 健康分次日复核 |
| 09:20 | 盘中锁利监控建立 |
| 17:40 | 锁利结果回填 |
| 19:40 | 策略画像补跑 |
| 21:00 | 晚间补数（当日日K、指数、涨停板等到齐） |
| 21:10 | QVIX 增量同步 |
| 21:30 | 盘后主流程：健康闸门 → 结算 → 每个策略各自出票（短线 + 中长线） |
| 21:50 | 数据新鲜度巡检 |
| 22:30 | 现役策略近 12 个月指标滚动重算（每月首个交易日实际执行） |
| 22:55 | 数据健康分每日快照 |
| 23:30 | 开盘啦涨停榜研究缓存日更 |
| 周六 10:00 | 中长线周度基本面快照 |

### launchd 守护任务

| 时间 | 任务 |
|---|---|
| 工作日 04:00 / 07:50 / 16:35 | 后端安全重启（先检查无在跑任务，全部避开交易与出票窗口） |
| 22:15 | 盘后出票 watchdog：回收 21:00 后卡住的 run（`backend/scripts/daily_run_watchdog.py`） |
| 周六 10:00 | 数据源能力矩阵探测（`probe_datasource_capabilities.py`） |
| 周日 03:30 | 日志轮转（`scripts/launchd/rotate_ashare_logs.sh`） |

时间常量都在 `backend/app/services/scheduler_pure.py`，launchd 模板在 `deploy/launchd/`。

运维窗口：09:15–15:05（交易时段）和 21:00–23:35（晚间补数与出票）不要重启服务。

---

## 三、数据

### 数据网关

所有取数都走统一网关，不允许绕过它直连数据源。

- 网关：`backend/app/services/datasource/gateway.py`，路由表 `routing_table.json`。
- 内置能力：按源/按接口限流、超时、重试、熔断（跳过已熔断的源，约 5 分钟放一个探测请求）、多源负载均衡、缓存、校验、监控。
- 熔断状态跨进程共享：`backend/app/core/shared_breaker.py`；额度：`provider_quota.py`。
- Tushare 协议的上游统一经 `TushareProviderRouter`（`backend/app/services/tushare_transport.py`）。
- 实盘行情优先于批量补数；交易时段允许限速补数，不能挤占实时 `rt_k`。
- 数据源开关可在前端「系统 → 配置」页切换。

### 数据源

| 数据源 | 用途 | 备注 |
|---|---|---|
| teajoin | 日线、基本面、涨停榜、竞价、筹码、资金流等 Tushare 协议接口 | 主源；480 次/分钟；不支持 rt_k、实时分钟、公告新闻。见 `docs/datasource-teajoin.md` |
| promax | 实时 `rt_k` 主源，其他接口备源 | 限流窗口短（5 分钟 / 30 分钟 / 1 小时重置）；2026-11-25 实测全线网络不可达 |
| quantdata | 实时 `rt_min`，Tushare 协议备源 | 有日额度；2026-11-25 实测额度耗尽 |
| 同花顺 thsdk | 实时行情、板块广度、市场快讯 | |
| Baostock、东方财富、新浪、腾讯、通达信、巨潮、AkShare | 历史分钟、公告新闻、实时行情兜底、QVIX | 兜底 |

QVIX（`index_daily` 的 QVIX50.SH / QVIX300.SH）：AkShare 读取的 optbbs 上游 2026-11-19 起停止更新，此后的交易日由 21:10 日更用 teajoin `opt_daily` 结算价按 CBOE VIX 方法计算补上（`datasource/qvix_calc.py`，L-239）。

能力矩阵 `backend/docs/datasource_capability_matrix.md` 由 `backend/scripts/probe_datasource_capabilities.py` 生成。小石（xiaoshi）2026-11-15 永久退役（`datasource/retired.py`），不要重新接入。

补数范围：最近 3 年，不补 2023 年 10 月以前。

### 本地数据

- MySQL：本机 Homebrew MySQL 9.6，`127.0.0.1:3306`，库 `ashare_workbench`；测试用 `ashare_test`。
- 中长线 parquet 数据：`backend/data/midterm/`（预告、财报、事件、分红等）。
- 运行时状态：`backend/data/runtime/`；策略指标 `backend/data/strategy_metrics/`；研究缓存 `backend/data/research_cache/`。

### 密钥

所有 Key 只放 `backend/.env`（已在 `.gitignore`），不写进代码、文档或提交。

---

## 四、目录结构

```
ashare-shortline-system/
├── ashare                      # CLI 入口
├── dev.sh                      # 前后端服务：status / start / stop / restart / logs
├── prefect.yaml                # Prefect 部署定义
├── deploy/                     # launchd 模板与 syslog 配置
├── scripts/                    # 架构检查、因子研究、launchd 运维脚本
├── backend/
│   ├── app/
│   │   ├── main.py             # FastAPI 入口（端口 8765）
│   │   ├── api/                # 路由：runs、recommendations、strategies、horizons（/api/horizons）、metrics、records、data_sources、admin ...
│   │   ├── core/               # 配置（ASHARE_ 前缀）、日志、告警、熔断、额度
│   │   ├── engine/             # 短线打分、聚合、选股政策
│   │   ├── policies/           # 版本化选股政策 JSON
│   │   ├── orchestration/      # Prefect flows / deployments
│   │   ├── governance/         # 生产治理：状态机、门控执行、注册与不可变产物
│   │   ├── services/
│   │   │   ├── datasource/     # 数据网关与各数据源适配
│   │   │   ├── runs/           # 短线正式 run 管线（stages/adapters.py）
│   │   │   ├── midterm_*.py    # 中线 / 长线策略与出票
│   │   │   ├── standalone_strategies.py  # 短线单策略参与名单与默认参数
│   │   │   ├── daily_pick_cap.py         # 每策略每日上限
│   │   │   ├── scheduler_service.py / scheduler_pure.py  # 调度
│   │   │   └── outcome_*.py    # 结算
│   │   ├── agents/             # 研究 agent、评审 runner、会话注册
│   │   ├── adapters/           # 市场源适配
│   │   ├── domain/             # 领域计算与因子分析
│   │   ├── skills_impl/        # 策略的 Python 实现（恐慌出清、滞涨预警）
│   │   ├── db/                 # 会话、迁移与生产加固
│   │   ├── schemas/            # Pydantic 模型
│   │   └── models/             # ORM 模型
│   ├── skills/                 # 短线策略（strategy.yaml + SKILL.md + manifest.json）
│   ├── research_skills/        # 研究原型，不参与出票
│   ├── scripts/                # 运维、补数、研究脚本（midterm/ 下是中长线研究）
│   ├── data/                   # 本地数据池（midterm parquet、runtime、指标与缓存）
│   ├── tests/
│   └── logs/                   # app.log、access.log
├── frontend-v2/                # React 19 + TypeScript + Vite（端口 5174）
└── docs/
    ├── strategy-research.md    # 策略研究与预注册记录（§编号）
    ├── lessons/LESSONS.md      # 教训注册表（L-### 编号）
    ├── ops/                    # 运维与产品规则说明
    ├── design/ specs/ research/  # 设计、规格与研究归档
    ├── bugs/ reviews/          # 缺陷证据与评审报告
    └── datasource-*.md         # 数据源评估与接入
```

---

## 五、启动与运维

前端 5174、后端 8765、Prefect server / worker 都由 launchd 托管，开机自启、崩溃自重启。

```bash
./dev.sh status       # 查看状态
./dev.sh restart      # 重启前后端（也可 start / stop）
./dev.sh logs backend # 看日志（frontend / backend）
```

后端安全重启（会先检查有没有正在跑的任务）：

```bash
backend/.venv/bin/python backend/scripts/active_work_check.py
backend/scripts/safe_backend_restart.sh
curl -sS http://127.0.0.1:8765/api/health
```

首次安装：

```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/alembic upgrade head
cd ../frontend-v2 && npm install
```

访问地址：

- 前端：http://localhost:5174
- 后端 API 文档：http://localhost:8765/docs
- Prefect：http://localhost:4200

### 前端页面

顶部切换短线 / 中线 / 长线，四个一级入口：

| 入口 | 子页面 |
|---|---|
| 今日 | 今日出票、持仓 / 待卖、驾驶舱（短线）、运行详情（短线） |
| 复盘 | 按批次、交易明细、运行记录 |
| 策略 | 策略列表（按综合分排序）、策略详情（含收益来源：去尾均值、中位数、稳健型/彩票型） |
| 系统 | 配置（数据源开关与熔断状态）、诊断 / 研究（中长线诊断含全市场等权基准 EWALL.A、超额/笔、按同推策略数分桶） |

### CLI

```bash
./ashare runs --limit 10 --pretty
./ashare recommendations <run_id> --pretty
./ashare explain <recommendation_id> --pretty   # 分数、技能审查、漏斗轨迹、交易计划
./ashare funnel <run_id> --pretty               # 候选过滤审计
./ashare data-health --pretty
./ashare data-sources --pretty
./ashare alerts --pretty
./ashare readiness --pretty
./ashare pool-status --summary-only --pretty
```

### 测试

```bash
cd backend && .venv/bin/python -m pytest -q tests/<某个测试文件>.py
```

- 测试自动连 `ashare_test` 库，不会碰生产库。
- 全量回归 `cd backend && .venv/bin/python -m pytest -q tests`，最近一次 4328 passed / 25 skipped（约 4300 个用例）。
- 提交前至少跑一次 `.venv/bin/python -c "import app.main"`。

---

## 六、主要配置

环境变量前缀 `ASHARE_`，写在 `backend/.env`，定义在 `backend/app/core/config.py`。

| 变量 | 默认 | 说明 |
|---|---|---|
| `ASHARE_DATABASE_URL` | 本机 MySQL `ashare_workbench` | 数据库 |
| `ASHARE_MAX_DAILY_PICKS_PER_STRATEGY` | `5` | 每策略每日前几只（第 N 名同分顺延），0 = 不限 |
| `ASHARE_TUSHARE_TEAJOIN_TOKEN` | 空 | teajoin Key |
| `ASHARE_TUSHARE_TEAJOIN_RATE_LIMIT_PER_MINUTE` | `480` | teajoin 限速（实测软顶 550） |
| `ASHARE_TUSHARE_TEAJOIN_LEAD` | `true` | teajoin 做主源；关掉则只做末位兜底 |
| `ASHARE_TUSHARE_PROMAX_API_KEY` | 空 | promax Key |
| `ASHARE_TUSHARE_PROMAX_RATE_LIMIT_PER_MINUTE` | `180` | promax 限速 |
| `ASHARE_TUSHARE_QUANTDATA_TOKEN` | 空 | quantdata Key |
| `ASHARE_LOG_LEVEL` | `INFO` | 日志级别 |

---

## 七、研究纪律

- 先预注册再回测，规则写死后不改；结果好坏都如实记录在 `docs/strategy-research.md`。
- 样本外从 2026-07-01 开始，见 `docs/holdout-protocol.md`。
- 决策时刻可见性：只用信号日收盘后真实能看到的数据（低频披露按公告日判定，不按期末日）。
- 用户给的个股只当例子，从全市场统计找规律，避免过拟合。
- 发现底层数据坏了，先修数据再下结论。
- 可复用的教训写进 `docs/lessons/LESSONS.md`，格式见 `docs/lessons/README.md`。

## 八、日志

- 后端：`backend/logs/app.log`、`backend/logs/access.log`
- 告警：`grep '\[ALERT:' backend/logs/app.log`
- launchd：`~/Library/Logs/ashare-backend.err.log`、`~/Library/Logs/ashare-frontend.err.log`

---

## 九、持续集成（CI）

仓库为 GitHub 公开仓库（公开仓库 Actions 免费、不限量）。

推送到 `main` 或向 `main` 发 PR 时，自动跑 `.github/workflows/verify.yml`：

| 任务 | 做什么 |
|---|---|
| Python lint | Ruff 检查与异常吞没门禁 |
| Python compile + import | 编译 `app` 并 `import app.main` 冒烟 |
| Python unit tests | `backend/app/` 下单测（不连 MySQL） |
| Python regression (no db) | `backend/tests/` 非 `integration` 用例，`sqlite://` |
| Python MySQL integration | 起 MySQL 8.0 服务，`alembic upgrade head` 后跑少量 utf8mb4 / 幂等 / 序列化冒烟 |
| Architecture check | `scripts/check_architecture.py` 依赖方向检查 |
| Governance store check | `backend/scripts/check_governance_store.py` 不可变治理产物布局 |
| Git diff check | `git diff --check` 查行尾/冲突标记 |
| Frontend v2 build | `npm ci && npm run build` |

仓库另配 pre-commit（`.pre-commit-config.yaml`），提交前自动跑 Ruff 等检查。

本地想先验证，不必等 CI：

```bash
cd backend && .venv/bin/python -m compileall app -q && .venv/bin/python -c "import app.main"
cd ../frontend-v2 && npm run build
```
