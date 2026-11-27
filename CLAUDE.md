# 语言偏好

优先使用中文回复。

# 项目开发策略

当前项目按最合适的架构来做，不保留向历史兼容；不为了旧接口、旧文件结构或临时实现额外迁就。

- 缺什么数据就补什么数据；本地数据池按增量方式维护，可以为完成系统目标主动补充、更新、校验本地数据。可以直接使用当前数据源重建数据。
- 代码结构不合理时可以直接重构，不需要为了保留旧实现而绕路。
- 允许主动修改代码、调整模块边界、重排文件结构、改造数据流和内部契约，只要这样更符合系统长期架构。
- 先解决最痛的问题；发现的每个问题不论大小都要修。
- 项目尚未发布生产版本，不需要向前兼容，但基础设施必须按生产级做；不接券商交易接口。

# 架构原则

**策略优先架构：** 推荐管线的过滤逻辑由各策略（strategy.yaml）自主定义，不由全局一刀切控制。

- 全局只保留 ST/停牌/无数据 等硬性不可交易过滤
- 成交额/换手率/回撤等阈值由各策略的 `universe.include/exclude` 定义
- 宽召回 (`market_broad_recall_count`) 为 6000，让全部可交易股票进入策略层
- AST 规则表达式已缓存（`evaluator.py`）；新特征必须加入 evaluator 的 `ALLOWED_NAMES` 白名单，否则规则表达式静默返回 False

# 策略治理

- 正式推荐按单策略独立运行（`standalone_strategies.py`），每个现役策略各自一个 formal run。「全策略组合」预设 `preset_formal_opening` 已于 2026-10-19 删除，不要恢复，定时任务也不得依赖它。
- 策略上线与去留由 Agent 自行判断，没有固定的数字门槛。判断依据：全期扣费净收益（`executed_return` 减 0.07% 往返税费）、同期同持有期全市场等权基准的超额、跨月符号一致性、样本量与信号日集中度；不能只看近期窗口，也不能只看绝对收益。
- 新上线策略自动做近 3 个月 replay 回补（`origin=replay`），统计时与 `live_forward` 分开；历史重建（`historical_replay`）不计入实盘表现。
- 用户给的个股只当例子，从全市场统计找特征，不围绕个股调规则。
- LLM 风险点评只在出票后挂载备注，不参与评分、不阻塞出票。
- 比较不同周期策略时看资金效率（每持有日收益）；提出更高收益的策略时必须同时说明回撤等风险。
- 已被大样本证伪、不要重新挖的方向：纯技术面因子、相对强度、持有期调整（L-221）；龙虎榜、两融、竞价在精选池口径（L-078）；资金流（L-064）；恐慌日换篮子、回购预案期内急跌、次新股（L-222）；研报盈利预测上调、券商金股共荐、大股东解押、冷门首次覆盖（L-223）；短线按单策略调止损距离与 trailing 开关（L-226）。

# 数据库策略

**本地 MySQL。**

- 本地 Homebrew MySQL 9.6 @ 127.0.0.1:3306，库 `ashare_workbench`；测试库 `ashare_test`
- 用户名/密码：`aowang` / `${ASHARE_DB_PASSWORD}`
- 数据同步、推荐管道的重读写操作全部在本地执行
- 本机不对外网开放：不做安全加固，不做 MySQL 备份（用户决定）

# 前端体验

- 不考虑移动端兼容，优先覆盖桌面端和平板/窄桌面宽度
- 默认黑色主题，用户切换后保存到本地
- 宽屏下主内容吃满可用空间，驾驶舱首页保持 3 栏布局
- 普通操作不做弹窗确认，用按钮 loading / 状态条 / 摘要反馈
- 删除正式方案只需一次明确确认（提示定时任务可能失败），不直接禁止

# 数据同步策略

**批量优先，逐股票调用只用于必须的场景。**

- 日K OHLCV：`pro.daily(trade_date=)` 一次拉全市场 5500+ 只
- 技术指标：`pro.stk_factor(trade_date=)` 一次拉全市场
- 筹码分布：`pro.cyq_perf(trade_date=)` 一次拉全市场
- 本地指标（MA/量比/乖离/突破/回撤）：`backfill_indicators.py` 批量计算
- 逐股票调用只在必须时（如 ths_member）使用，且需限速 `--rate-limit 3`；逐股票循环的 sleep 必须放在循环顶部，失败路径同样限速
- 交易时段（09:15–15:05）允许批量取数，仍要限速，不得挤占实时行情
- 补数范围：最近 3 年，不补 2023-10 以前

# 数据源策略

- 所有取数经统一数据网关（`backend/app/services/datasource/`，路由表 `routing_table.json`）：按源、按接口限流、重试、超时、熔断（跨进程共享，约 5 分钟放一个探测请求）、缓存、校验。禁止绕过网关直连上游。
- Tushare 协议接口（日线/基本面/辅助数据）统一走 `TushareProviderRouter`，顺序由路由表决定：teajoin（480 RPM）为主源，promax（180 RPM）与 quantdata（90 RPM）作回退。能力矩阵 `backend/docs/datasource_capability_matrix.md` 由 `scripts/probe_datasource_capabilities.py` 生成，勿手改。
- teajoin 的 key 只放 `backend/.env`，不写进文档、提交或记忆。
- 实时行情 `quote.realtime` 链：Promax rt_k → 同花顺 → 新浪 → 通达信 → 腾讯 → AKShare → QuantData rt_min；仅对候选股按需调用；实盘行情优先于批量补数。
- 历史分钟 `bar.minute` 链：Baostock → Tushare `stk_mins`（Promax 门禁：HTTP 202 视为生成中并回退；200 响应须通过整分钟/交易时段/时间唯一性校验）→ 新浪/腾讯备份（默认关闭）。
- 新闻公告：`news_sources` 默认 tushare,eastmoney；公告补数走东方财富/巨潮；同花顺快讯是市场级，不并入个股新闻链。
- 概念成分股：Tushare `ths_member`；筹码分布：`cyq_perf`；板块广度：同花顺。
- 小石（xiaoshi）2026-11-15 永久退役，`datasource/retired.py` 统一判定，不要重新接入。
- hk_hold 北向持股 2024-08 以后已停止披露，上游返回港股数据，不可恢复。

# 运行与部署

- 后端 8765、前端 5174、Prefect server/worker 由 launchd 托管；`./dev.sh status|restart|logs`。
- Prefect worker 每个任务新开子进程并从磁盘主工作树加载代码：回放/重建期间不要对主工作树做 fast-forward。
- 09:15–15:05（交易时段）与 21:00–23:35（21:00 晚间补数、21:30 盘后出票流水线、22:30 R12 重算、23:30 研究缓存）不重启后端、不切换代码；重启前跑 `backend/scripts/active_work_check.py`。时间常量在 `scheduler_pure.py`。
- 手动触发 formal run 必须带 `skip_aux_refresh=True`，否则会卡在辅助数据刷新。
- 日志：`backend/logs/app.log`、`backend/logs/access.log`；告警 `grep '\[ALERT:' backend/logs/app.log`。

# Python 测试

优先使用项目虚拟环境 `backend/.venv`。允许直接使用生产 MySQL 做验证测试，禁止清库/删表/重建等不可恢复操作。全量回归可以跑，当前约 4300 个用例。

# 教训与经验沉淀

- 项目中可跨轮复用、会改变后续决策的教训，唯一写入 `docs/lessons/LESSONS.md`。
- `docs/bugs/`、`docs/reviews/`、根目录诊断报告、`output/` 和回测报告只保存证据档案；从中提炼出的长期规则必须回写教训注册表，并回链证据。
- 新教训必须使用唯一 `L-###` 编号，包含状态、适用范围、证据、永久规则和吸收位置；单次实验数字不要升级成长期教训。
- 中间结果与研究脚本放 `output/`（已 gitignore），不要放 /tmp。

# 凭据规则

- 不要把服务器密码、API Key、数据库密码写入 Git 跟踪文件
- .env 文件不上传 GitHub

# Git 提交规则

**完成一批改动后主动提交并推送**，不要把改动积压在工作区。

- 按改动主题切分 commit：单一主题一个 commit，跨主题分多个；不为分而分，也不机械全塞一个。
- commit message 首行 `type: 简述`（fix/feat/chore/refactor/docs/perf/test），空行后中文正文，末尾加 `Co-Authored-By: Claude Code <noreply@anthropic.com>`。
- 提交前扫一遍未跟踪新文件，命中 `password|secret|api_key|token|aowang` 则停下问用户，不要提交凭据。
- 本机 22 端口被阻断，推送用 `GIT_SSH_COMMAND="ssh -o Port=443 -o HostName=ssh.github.com" git push origin <分支>`。
- **如果在 worktree 里**（`git rev-parse --git-common-dir` 返回绝对路径）：当前分支提交 + push 后，切到主仓库 main、`git pull --ff-only origin main`、`git merge --no-ff <当前分支>`、`git push origin main`，把改动合并回 main。冲突不要硬解，停下问用户。
- 禁止 `git push --force`、`git reset --hard`、`git stash`、删 worktree，除非用户明确要求。
