# 同花顺 thsdk 接入（3 个 P0 数据源，2026-11-15）

承接 `docs/datasource-coverage-compare-20261026.md` 的结论：不买 tick/Level2/盘口，优先级是
**主通道稳定 > 实时冗余 > 其他**。本次只接入评估出的 3 个 P0，定位是**备源/冗余**，不是替换现有主源。

## 1. 接入清单

| P0 | 数据集 | Provider | 链位置 | 解决的问题 |
|---|---|---|---|---|
| 实时行情冗余 | `quote.realtime` | `ths_realtime` | `tushare_rt_k` → **`ths_realtime`** → `sina_realtime` → ... | `rt_k` 只有 promax 一家 Tushare 协议源，额度耗尽后仅剩新浪爬虫 |
| 板块广度 | `aux.sector_breadth`（新增） | `ths_block` | 独立数据集，`fetch_first` | 板块广度类特征缺数据源：promax `ths_index` 回落只返 5 条、quantdata 条数不稳 |
| 市场级快讯 | `aux.news` 的 `ths` 分支 | `_fetch_ths_news` | 独立分支，**未并入默认链** | 公告新闻全靠免费爬虫，需要一条独立快讯源 |

## 2. 是否需要注册账号

**不需要。** 用官方临时游客账户即可跑通全部三条，实测均 OK。注册同花顺账号（`ASHARE_THS_USERNAME/PASSWORD`）
只提升稳定性与限频额度，属于可选优化：

```bash
ASHARE_THS_ENABLED=true          # 默认 true；false 则三条接入点同时失效
ASHARE_THS_USERNAME=            # 留空 = 游客账户
ASHARE_THS_PASSWORD=
ASHARE_THS_MAC=                  # 账号绑定设备时必填
ASHARE_THS_MIN_INTERVAL_SECONDS=0.06
ASHARE_THS_TIMEOUT_SECONDS=5.0
```

⚠️ 官方对游客账户的警告：「临时账户可能随时失效，不适合生产环境使用」。因此三条链路都按**可选源**处理：
任何异常（含账户失效）都由网关按 `DISABLED/TIMEOUT/BAD_DATA` 处理并熔断，后续 provider 继续兜底，不影响主流程。

## 3. 落地文件

新增：

- `backend/app/services/datasource/ths_client.py` —— 统一封装：延迟建连、全局 60ms 节流、异常→`DataSourceError`、
  哨兵 `4294967295` 归一、`venue_for()` 市场前缀推导、`probe()` 自检。
- `backend/app/services/datasource/sector_breadth.py` —— `aux.sector_breadth` 数据集；`market_block("URFI")`
  枚举 630 个板块目录并缓存 1 小时，逐板块取 `market_data_block(code, "基础数据")`。
- `backend/tests/test_ths_p0_20261115.py` —— 10 条单测，全部打桩不联网。

修改：

- `quote_realtime.py` —— 新增 `ThsRealtimeProvider`，按市场分组调用（**混市场会直接报错**：「一次性查询多支股票必须市场代码相同」）。
- `market_auxiliary.py` —— `_fetch_news` 增加 `ths` 分支 + `_fetch_ths_news`。
- `wrap.py` —— 注册 `ths_news` provider 限速桶（限频由 `ths_client` 全局节流承担，不重复套桶）。
- `topology.py` —— 注册 `aux.sector_breadth` 数据集与三个源的显示名、开关键 `("ths","concept_sector"/"quote"/"news")`。
- `core/config.py` —— 上述 `ths_*` 配置项。
- `backend/requirements.txt` —— `thsdk==1.7.18`（此前已装但未声明）。

## 4. 两个必须记住的坑

1. **`clean_number` 不能用「大于等于哨兵」判断**。实测半导体板块 `总金额=2.3e11`、`流通市值=6.4e12`，
   全部大于哨兵 `4294967295`；早期实现把它们误杀成 `None`。必须**精确等值**判断 `== SENTINEL or == -SENTINEL`。
2. **`thsdk.news(code=)` 不过滤个股**。实测查 600519 与 300033 返回的 20 条完全相同（都是「同花顺7x24快讯」
   宏观新闻）。所以它**不能**替代东财个股公告，也**没有**并入 `news_sources` 默认链（默认仍是 `tushare,eastmoney`），
   否则会污染 `news_risk_count` / `announcement_negative_count` 这类排除项。需要时显式配置：
   `ASHARE_NEWS_SOURCES=tushare,eastmoney,ths`。

## 5. 验收结果（2026-11-15）

```
probe(): enabled=True client='guest'  market_data_cn/block/news 三项 ok
quote.realtime 链: ['tushare_rt_k','ths_realtime','sina_realtime','tdx_quote','tencent_qt','akshare_snapshot','tushare_rt_min']
  600519/000001/300033 混合市场 0.46s → 3 行，全 source=ths_realtime，validate_rows 通过
aux.sector_breadth: 单板块 0.08s（半导体 trade_date=2026-11-13 up=14 down=175 amount=2.32e11 float_mv=6.42e12）
  行业前 5 0.41s；板块目录 all=630 industry=90
aux.news(_fetch_ths_news): 20 条，字段齐全（source/article_id/announce_date/title/summary/sentiment/is_risk_warning）
pytest: 4161 passed, 23 skipped（全量，含新增 10 条 ths 单测；3 个锁定 quote.realtime 链顺序的旧测试已同步更新）
```

三个接入点都受「系统 → 配置」开关控制：`("ths","quote")` / `("ths","concept_sector")` / `("ths","news")`，
关掉后 provider 直接跳过（`ths_enabled=false` 则三条同时失效）。

## 6. 未接入（评估后放弃）

`depth`/`order_book`/`big_order_flow`/`tick_level1`/`tick_super_level1`（研究证据 §326/§327：对收益无增益；
龙头 80% 一字买不进）、`klines` 分钟线、`min_snapshot`、`wencai_nlp`、`index_list`、`ths_industry`/`ths_concept`
（无行情数据）、`corporate_action`、港美外汇期货 ETF/IPO。

`call_auction_anomaly`（775 行 13 类异动）与 `block_constituents`（190 只成分股）列为 P1 候选，
本次不动，等板块广度特征跑出稳定信号后再评估。
