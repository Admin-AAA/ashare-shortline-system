# A股交易系统（ashare-shortline-system）

本地运行的 A 股选股与复盘系统，分短线、中线、长线三个周期。每个策略每天盘后独立出票，次日开盘买入，按各自规则持有、结算和统计。

> 仅用于研究和复盘，不构成投资建议。

---

## 一、现役策略（2026-10-27）

上线不靠感觉：先在 `docs/strategy-research.md` 预注册规则，回测扣成本后达到上线标准才上线，不达标就进淘汰列表，不事后放宽。

| 周期 | 持有 | 现役策略 | 代码位置 |
|---|---|---|---|
| 短线 | T+1 起按策略离场 | 炒股养家、陈小群（观察）、超跌资金反包、主线相对强度龙头 | `backend/skills/*/strategy.yaml`，参与名单见 `backend/app/services/standalone_strategies.py` |
| 中线 | 20 日 | 小市值周度、低价小市值周度、小盘高股息周度、小盘低市销率周度、小盘超跌反转周度、回购预案、业绩加速、业绩预告预喜、期权激励、大宗溢价 | `backend/app/services/midterm_service.py` 的 `STRATEGIES` |
| 长线 | 60 日 | 低换手低波、红利低波、连续 4 期双增长、净负债转净现金 | 同上 |

- 上线标准（2026-10-25）：近 12 个月（R12）和扩展窗口（N3）扣成本后每笔为正、各至少 30 笔；PF ≥ 1.3；最大回撤 短线 ≤ 35% / 中线 ≤ 25% / 长线 ≤ 20%；8 个阶段至少 6 段为正（短线 4 窗口至少 3 段）；熊市段每笔 ≥ -5%。
- 被拒策略：中线/长线写在 `backend/app/api/midterm.py` 的 `MID_REJECTED` / `LONG_REJECTED`，短线退役名单是 `standalone_strategies.py` 的 `RETIRED_STRATEGY_IDS`。退役策略的目录和代码保留，便于复现。
- 新上线策略会自动回补近 3 个月的回放记录，标记 `origin=replay`，不和实盘（`live_forward`）混在一起统计。
- 长线研究目前暂停，已上线的长线策略照常运行。

### 每策略每日推荐上限

- 每个策略每天最多取 **前 5 个分数**（`ASHARE_MAX_DAILY_PICKS_PER_STRATEGY`，默认 5，0 = 不限）。
- 同分顺延：10 只同为满分就 10 只都出；分数各不相同就是前 5 只。
- 只作用于新生成的实盘推荐，回放、回测、历史数据不受影响。
- 短线还有一道更早的出票上限：每个策略每次 run 最多 3 张（`DEFAULT_OUTPUT_COUNT`，高潮日减半）。
- 详见 `docs/ops/daily-pick-cap-20261026.md`，实现在 `backend/app/services/daily_pick_cap.py`。

---

## 二、每日流程

所有任务由 Prefect 调度（工作日，北京时间）：

| 时间 | 任务 |
|---|---|
| 08:03 | 大宗交易盘前备份（补上一交易日） |
| 08:10 / 17:10 | 股票身份数据刷新（名称、ST、上市状态，三方对账） |
| 08:30 | 正式推荐安全网：检查最近 5 个交易日的出票缺口并补跑 |
| 09:20 | 盘中锁利监控建立 |
| 17:35 | 盘后主流程：同步当日数据 → 健康闸门 → 结算 → 每个策略各自出票（短线 + 中长线） |
| 17:40 | 锁利结果回填 |
| 18:00 | 数据新鲜度巡检（只读） |
| 19:40 | 策略画像补跑 |
| 21:00 | 晚到数据修复 |

时间常量都在 `backend/app/services/scheduler_pure.py`。

运维窗口：09:15–15:05（交易时段）和 16:35–18:35（日更）不要重启服务。

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
| teajoin | 日线、基本面、涨停榜、竞价、筹码、资金流、历史分钟等（约 204 个接口） | 优先级最高；420 次/分钟；不支持 rt_k、实时分钟、公告新闻。见 `docs/datasource-teajoin.md` |
| promax | 实时 `rt_k` / `rt_min` 主源，其他接口备源 | 限流窗口短（5 分钟 / 30 分钟 / 1 小时重置） |
| quantdata | Tushare 协议备源 | 有日额度 |
| 小石 xiaoshi | 全市场实时快照、分钟数据、公告 | 独立 market source |
| 东方财富、新浪、巨潮、AkShare、Baostock | 实时行情、公告新闻、备用日线/分钟 | 兜底 |

补数范围：最近 3 年，不补 2023 年 10 月以前。

### 本地数据

- MySQL：本机 `127.0.0.1:3306`，库 `ashare_workbench`；测试用 `ashare_test`。
- 中长线 parquet 数据：`backend/data/midterm/`（预告、财报、事件、分红等）。
- 运行时状态：`backend/data/runtime/`。

### 密钥

所有 Key 只放 `backend/.env`（已在 `.gitignore`），不写进代码、文档或提交。

---

## 四、目录结构

```
ashare-shortline-system/
├── ashare                      # CLI 入口
├── dev.sh                      # 前后端服务：重启 / 状态 / 停止 / 日志
├── prefect.yaml                # Prefect 部署定义
├── backend/
│   ├── app/
│   │   ├── main.py             # FastAPI 入口（端口 8765）
│   │   ├── api/                # 路由：runs、recommendations、strategies、midterm（/api/horizons）、metrics、records、data_sources、admin ...
│   │   ├── core/               # 配置（ASHARE_ 前缀）、日志、告警、熔断、额度
│   │   ├── engine/             # 短线打分、聚合、选股政策
│   │   ├── policies/           # 版本化选股政策 JSON
│   │   ├── orchestration/      # Prefect flows / deployments
│   │   ├── services/
│   │   │   ├── datasource/     # 数据网关与各数据源适配
│   │   │   ├── runs/           # 短线正式 run 管线（stages/adapters.py）
│   │   │   ├── midterm_*.py    # 中线 / 长线策略与出票
│   │   │   ├── standalone_strategies.py  # 短线单策略参与名单与默认参数
│   │   │   ├── daily_pick_cap.py         # 每策略每日上限
│   │   │   ├── scheduler_service.py / scheduler_pure.py  # 调度
│   │   │   └── outcome_*.py    # 结算
│   │   └── models/             # ORM 模型
│   ├── skills/                 # 短线策略（strategy.yaml + SKILL.md + manifest.json）
│   ├── research_skills/        # 研究原型，不参与出票
│   ├── scripts/                # 运维、补数、研究脚本（midterm/ 下是中长线研究）
│   ├── tests/
│   └── logs/                   # app.log、access.log
├── frontend-v2/                # React 19 + TypeScript + Vite（端口 5174）
└── docs/
    ├── strategy-research.md    # 策略研究与预注册记录（§编号）
    ├── lessons/LESSONS.md      # 教训注册表（L-### 编号）
    ├── ops/                    # 运维与产品规则说明
    └── datasource-*.md         # 数据源评估与接入
```

---

## 五、启动与运维

前端 5174、后端 8765、Prefect server / worker 都由 launchd 托管，开机自启、崩溃自重启。

```bash
./dev.sh status      # 查看状态
./dev.sh restart     # 重启前后端
./dev.sh logs backend   # 看日志（frontend / backend）
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
| 系统 | 配置（数据源开关与熔断状态）、诊断 / 研究 |

### CLI

```bash
./ashare runs --limit 10 --pretty
./ashare recommendations <run_id> --pretty
./ashare data-health --pretty
./ashare readiness --pretty
./ashare pool-status --summary-only --pretty
./ashare registry-diagnostics --pretty
```

### 测试

```bash
cd backend && .venv/bin/python -m pytest -q tests/<某个测试文件>.py
```

- 测试自动连 `ashare_test` 库，不会碰生产库。
- 本机内存 16GB，只跑相关的小范围测试，不跑全量。
- 提交前至少跑一次 `.venv/bin/python -c "import app.main"`。

---

## 六、主要配置

环境变量前缀 `ASHARE_`，写在 `backend/.env`，定义在 `backend/app/core/config.py`。

| 变量 | 默认 | 说明 |
|---|---|---|
| `ASHARE_DATABASE_URL` | 本机 MySQL `ashare_workbench` | 数据库 |
| `ASHARE_MAX_DAILY_PICKS_PER_STRATEGY` | `5` | 每策略每日前几个分数，0 = 不限 |
| `ASHARE_TUSHARE_TEAJOIN_TOKEN` | 空 | teajoin Key |
| `ASHARE_TUSHARE_TEAJOIN_RATE_LIMIT_PER_MINUTE` | `420` | teajoin 限速 |
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
