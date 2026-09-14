# 数据源可行性地地图（2026-09-14 实测）

本文件回答一个具体问题：**某个信息轴到底能不能在三个窗口
（W1 2025-01-15~2026-01-16 / W2 2026-01-17~07-30 / W3 2026-08-01~09-13）上被检验**。
所有结论都是本机实测，不是文档推测；复现方式见文末。

> 背景：会话中我曾把"本地表 0 行"当成"数据源不可用"，据此把若干方向判为"不可测"。
> 复核后发现其中一部分是**没回填**，不是**拿不到**。这份地图用来避免同类误判。

## 1. Tushare 系（provider：promax / quantdata / tushare_official）

| 接口 | 历史可取 | 实测证据 | 备注 |
| --- | --- | --- | --- |
| `stk_auction`（集合竞价） | 可，但不稳定 | 2025-03-03 得 6514 行；2025-06-19 得 8007 行 | 实际只有 promax 服务（official 恒 business_rate_limit）；promax 间歇 http_server/timeout |
| `moneyflow`（资金流） | 可 | 2025-03-03 得 5117 行；2026-09-11 得 5550 行 | 方向已被 L-064 封禁（全市场反向），与能否取数无关 |
| `top_inst`（机构席位） | 可 | 2025-03-03 得 930 行（含"机构专用"） | 龙虎榜族在选中池触发率仅 1.8%/1.0%/0.0% → 过不了样本门槛 |
| `top_list`（龙虎榜） | 本地已全窗 | dragon_tiger 表 244/128/30 天 | L-078 已测，选中池无咬口 |
| `news` / `anns_d` | 否 | official 无权限 | 见下节东财替代 |
| `ths_index` / `ths_member`（同花顺概念） | 否，三源全失败 | official 无权限、promax http_server、quantdata provider_error（尽管 supports() 回 True） | 概念轴当前不可测 |

## 2. 东方财富（HTTP，无需 Tushare 权限）

| 接口 | 历史可取 | 实测证据 |
| --- | --- | --- |
| np-anotice-stock.eastmoney.com 的 /api/security/ann | **支持日期区间** | 带 begin_time/end_time 返回 2025-03-19 与 2025-03-05 公告；不带日期只返回最新 15 条 |

**生产代码的调用姿势才是问题**：`market_auxiliary._fetch_eastmoney_news`
只调不带日期参数的版本，所以看起来"只有近期数据"。回填历史必须显式传
`begin_time`/`end_time`。这解释了 W1 的 `news_announcement` 为什么是 0 行。

## 3. xiaoshi v3（独立源，非 Tushare 协议）

`/api/v3/quant-data/catalog` 共 17 个数据集，关键的几个：

| 数据集 | 覆盖 | 结论 |
| --- | --- | --- |
| `sector_boards`（行业与概念板块日快照，含 mainline_score / continuity_score / rotation_score） | history_available_from = 2026-08-07 | **只覆盖 W3**，无法三窗检验 |
| `sector_constituents`（板块成分关系，80505 条） | 下载 manifest 里 sector-constituents 仅 **1 个文件（2026-08-07~08-12）** | **概念/题材轴彻底不可测**（L-081 当年正因概念表缺失才改用 stocks.industry 替代） |
| `event-timeline`（事件时间线；列含 event_id / event_type / event_time / **reported_available_at** / **available_at** / source / **importance_score** / payload_json） | first=2025-05-14，last=2026-09-12，923 文件 | **W2/W3 全覆盖**；W1 覆盖后 8 个月（缺 2025-01-15~05-13）。**唯一还开着的、带 PIT 可用时间戳的新信息轴** |
| `cn-minute`（分钟线） | 2020-01-02~2026-09-11，5810 文件 | 日内维度；但 L-079 已把 pm_ret 尾盘追高在第三真前瞻窗证伪 |
| `cn-daily` / `adjustment-factors` / `financial-*` | 2020/2021 起 | 本地已有等价表 |

## 4. 结论与下一步

- **概念/题材轴**：需要外部换源。xiaoshi 只到 2026-08-07，Tushare 三源全失败。
- **集合竞价轴**：本地覆盖率已从 43/86/18 提到 109/244、94/128、24/30；
  但 promax 对 2025-06-19 之后的日子持续失败，W1 剩余 135 天当前补不动。
  **已按 L-078 规则②用现有覆盖完成 v4×三窗复核，结论维持证伪**
  （`backend/scripts/research_auction_three_window_v4.py`）。
- **事件轴（event-timeline）**：唯一还开着的候选。它自带 `available_at` /
  `reported_available_at`，天然满足"T 决策时可得"的时点约束；且 W2/W3 全覆盖。
  下一步应先看 `payload_json` 的事件类型与重要性分布，再在选中池上做三窗检验
  （W1 只用后 8 个月，并明确标注覆盖缺口）。

## 5. 复现

三个探针分别落在：

- Tushare 三接口可达性：`backend/scripts/backfill_auction_direct.py` 的 `backfill_day` 与
  `.venv/bin/python -c` 直接调 `get_pro().<api>(trade_date='20250303')`。
- 东财公告日期区间：见本节第 2 点表格里的 URL 参数（begin_time / end_time）。
- xiaoshi 数据集覆盖：`XiaoshiClient.get_quant_catalog()`、`get_quant_data(ds)`、
  `get_history_manifest()` 三者返回的 `history_available_from/to` 与 `coverage`。
