# 语言偏好

优先使用中文回复。

# 项目开发策略

当前项目按最合适的架构来做，不保留向历史兼容；不为了旧接口、旧文件结构或临时实现额外迁就。

- 缺什么数据就补什么数据；本地数据池按增量方式维护，可以为完成系统目标主动补充、更新、校验本地数据。
- 代码结构不合理时可以直接重构，不需要为了保留旧实现而绕路。
- 允许主动修改代码、调整模块边界、重排文件结构、改造数据流和内部契约，只要这样更符合系统长期架构。

# 架构原则

**策略优先架构：** 推荐管线的过滤逻辑应由各策略（strategy.yaml）自主定义，不由全局一刀切控制。

- 全局只保留 ST/停牌/无数据 等硬性不可交易过滤
- 成交额/换手率/回撤等阈值由各策略的 `universe.include/exclude` 定义
- 宽召回 (`market_broad_recall_count`) 设为 6000，让全部可交易股票进入策略层
- AST 规则表达式已缓存（`evaluator.py`），避免重复解析

# 数据库策略

**本地 MySQL。**

- 本地 Homebrew MySQL 9.6 @ 127.0.0.1:3306，库 `ashare_workbench`
- 用户名/密码：`aowang` / `${ASHARE_DB_PASSWORD}`
- 数据同步、推荐管道的重读写操作全部在本地执行

# 前端体验

- 不考虑移动端兼容，优先覆盖桌面端和平板/窄桌面宽度
- 默认黑色主题，用户切换后保存到本地
- 宽屏下主内容吃满可用空间，驾驶舱首页保持 3 栏布局
- 普通操作不做弹窗确认，用按钮 loading / 状态条 / 摘要反馈

# 数据同步策略

**批量优先，逐股票调用只用于必须的场景。**

- 日K OHLCV：`pro.daily(trade_date=)` 一次拉全市场 5500+ 只
- 技术指标：`pro.stk_factor(trade_date=)` 一次拉全市场
- 筹码分布：`pro.cyq_perf(trade_date=)` 一次拉全市场
- 本地指标（MA/量比/乖离/突破/回撤）：`backfill_indicators.py` 批量计算
- 逐股票调用只在必须时（如 ths_member）使用，且需限速 `--rate-limit 3`
- Tushare 代理服务器 `https://fastapic.stockai888.top/`，不限速；全市场 `stock_basic` 查询延迟波动较大（2~50s），客户端读超时已设为 120s

# 数据源策略

- K线/基本面/辅助数据优先使用 Tushare
- 概念成分股：Tushare `ths_member`（akshare 有 py_mini_racer 兼容问题）
- 新闻公告：Tushare `anns_d` + eastmoney `np-anotice` 双源
- 筹码分布：Tushare `cyq_perf`（若接口不可用则标记"数据源不可用"）

# Python 测试

优先使用项目虚拟环境 `.venv`。允许直接使用生产 MySQL 做验证测试，禁止清库/删表/重建等不可恢复操作。

# 教训与经验沉淀

- 项目中可跨轮复用、会改变后续决策的教训，唯一写入 `docs/lessons/LESSONS.md`。
- `docs/bugs/`、`docs/reviews/`、根目录诊断报告、`output/` 和回测报告只保存证据档案；从中提炼出的长期规则必须回写教训注册表，并回链证据。
- `.workbuddy/memory/MEMORY.md` 是本地记忆导航，不维护第二份教训副本；项目外 self-improve state/run records 只保存运行状态和结构化记录。
- 新教训必须使用唯一 `L-###` 编号，包含状态、适用范围、证据、永久规则和吸收位置；单次实验数字不要升级成长期教训。

# 日志位置

- 本地：`backend/logs/app.log`、`backend/logs/access.log`

# 凭据规则

- 不要把服务器密码、API Key、数据库密码写入 Git 跟踪文件
- .env 文件不上传 GitHub

# Git 提交规则

**完成一批改动后主动提交并推送**，不要把改动积压在工作区。

- 按改动主题切分 commit：单一主题一个 commit，跨主题分多个；不为分而分，也不机械全塞一个。
- commit message 首行 `type: 简述`（fix/feat/chore/refactor/docs），空行后中文正文，末尾加 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`。
- 提交前扫一遍未跟踪新文件，命中 `password|secret|api_key|token|${ASHARE_DB_PASSWORD}|aowang` 则停下问用户，不要提交凭据。
- 在 main 分支上有新改动时，先开 feature 分支再提交，不直接往 main 提交。
- push 用 `git push -u origin <分支>`（首次设 upstream）。
- **如果在 worktree 里**（`git rev-parse --git-common-dir` 返回绝对路径）：当前分支提交 + push 后，切到主仓库 main、`git pull --ff-only origin main`、`git merge --no-ff <当前分支>`、`git push origin main`，把改动合并回 main。冲突不要硬解，停下问用户。
- 禁止 `git push --force`、`git reset --hard`、删 worktree，除非用户明确要求。
