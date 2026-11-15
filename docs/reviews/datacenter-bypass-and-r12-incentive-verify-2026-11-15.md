# 数据中心旁路审计与 R12 激励通道修复验证（2026-11-15）

本轮起因：数据健康巡检里 `margin_detail` 11-13 只 2019 行（正常 4485），
`formal:rolling_r12` 报「mid-incentive-option / mid-incentive-vol 各 271 天回放取数失败」。
顺着这两条红黄项查出的是同一类架构缺陷：**取数链路绕过数据中心，且把「半量」当成功**。

## 1. 根因（按严重度）

| # | 缺陷 | 证据 | 后果 |
| --- | --- | --- | --- |
| D1 | 补数脚本硬编码单一 provider | `margin_detail_fill._default_fetch` 只调东财；`data_source_config` 的启停/priority、`routing_table.json` 链序、熔断限流全部绕开 | 换源策略随脚本作者变化，路由表调整对补数无效 |
| D2 | 网关只在「异常/空」换源，「只回半个市场」算成功 | 11-13 实测 promax 2019、quantdata 2019、teajoin 2019、东财 2019、akshare-SSE 2000/SZSE 0，**全部只有沪市**；`fetch_first` 拿到 2019 行即返回 | 缺口被记成「已取到」，红项挂着无人能补 |
| D3 | 分钟线覆盖只判「该日有没有行」 | `_recent_trade_day_coverage` 仅对 `daily_kbar` / `capital_flow` 做不完整判定；`minute_kbar` 走 `_present_dates` | 2026-09-04 仅 12 只、09-23 仅 3 只、10-12 仅 19 只也被视为「已覆盖」，自愈永不补 |
| D4 | 两融退避纯读 provenance | `_backoff_active` 连续 4 次 incomplete 且行数不变即退避 12h，期间不碰上游 | 上游第 5 小时补发也要白等到窗口结束 |
| D5 | 死源静默 | `api.shizixi.com` → DNS `28.0.0.137`，`/api/v3/manifest`、`/api/v3/history/catalog` 全部连不通；本地 `data/xiaoshi_min1` 最后写入 2026-09-18；`data/xiaoshi_min_all/sync.log` 每日 `fatal` | xiaoshi 仍是 `bar.minute` 链首 provider，每次取分钟线先空耗一次失败请求；min_all 数据陈旧至 2026-10-12 |
| D6 | 激励公告回放通道 | 回放每天走 cninfo 在线网关查一年历史区间，网关对历史区间持续取不到数 | 两个现役激励策略 271 天无数据，指标被 `trade_stats(n=0)` 伪装成「样本不足」 |

D6 上轮已修（本地 parquet 存档 + 禁止回写实盘事件库）；本轮补的是性能与全窗口实证：
每天 108 次 parquet 遍历（3 万次读）改成进程内一次性建索引 + 二分，307 天回放从「跑不完」降到 20.3 秒。

## 2. 本轮改动

1. **新增 `app/services/datasource/complete_rows.py`**：按数据中心 `_ordered_providers` 的真实链序
   逐个 `request_pinned`，按主键合并去重，达到 `expected_rows` 即停，否则穷尽所有启用源。
   不新增凭据、不改路由表，复用已有启停/priority/leaders/熔断。单源失败只记 `errors`，不中断链。
2. **`margin_detail_fill` 接入数据中心**：`_default_fetch` 改为 `fetch_complete_trade_date("margin_detail", ...)`
   （排除 promax，主表数据本就来自它），东财退化为链尾补充源；provenance 记录整条链的
   `attempted / by_source / errors`，不再让人以为「只试了一家就放弃」。
3. **退避可感知上游恢复**：退避期间做一次轻量探测（不算 promax、不写库），行数一旦超过库内行数
   立即解除退避并补齐；探测失败/仍半量继续退避。退避判定改为只认 provenance，不再依赖待补清单文件。
4. **分钟线覆盖判定**：新增 `_incomplete_minute_kbar_dates`（当日覆盖股票数 < 240 即不完整，
   目标池 300），并让 `data_gap_remediation_service` 的分钟线计划同时消费 `incomplete_dates`。

## 3. R12 全窗口实证（同一窗口、同一口径）

回放参数：`replay_midlong(2025-07-25 → 2026-11-02, [mid-incentive-option, mid-incentive-vol])`，
`apply_daily_cap=True`，`isolate_errors=True`。结果：**307 个交易日，`no_data={}`、`errors={}`、
`crashed={}`，20.3 秒**，暂存 158 个批次文件（option 115 批 / 161 只，vol 43 批 / 48 只）。

指标窗口与已发布文件一致（信号 2025-11-02 ~ 2026-11-02，`rolling_r12_latest.json`）：

| 策略 | 已发布（11-02） | 本轮修复后 | 判定变化 |
| --- | --- | --- | --- |
| mid-incentive-option | n=17，avg 7.22%，win 58.8% → `insufficient_sample` | **n=124，avg 4.02%，median 0.29%，win 53.2%，PF 1.78，trim5 2.90%，持仓中 2** | `insufficient_sample` → **positive** |
| mid-incentive-vol | n=2，avg 7.97%，win 100% → `insufficient_sample` | **n=37，avg 7.19%，median 2.81%，win 56.8%，PF 2.81，trim5 7.00%** | `insufficient_sample` → **positive** |

必须同时说清的三件事：

* 旧值（n=17 / n=2）不是「样本偏少」，是回放通道坏了 —— 271 天取不到数，只漏出零星几笔。
* 旧 avg 7.22% 是**幸存者偏差**：补全后 avg 降到 4.02%，去极值（trim5）只剩 2.90%，
  说明 option 策略的收益对右尾依赖较重；vol 策略的相对质量优于 option（trim5 7.00%、PF 2.81）。
* 口径仍是**历史重建**（replay），不是 `live_forward`。按 L-096/L-089，这份结果足以推翻
  「指标不可得」，但不能直接当 canonical 发布依据；下一交易日 22:30 的 `rolling_r12_monthly`
  会用修好的通道重算并写入正式文件。

## 4. 仍未解决 / 需人工决策

1. **两融 11-13 深市缺口**：五家源（promax / quantdata / teajoin / 东财 / akshare 交易所官方）
   实测都只有沪市 2019 行，深市确实未发布，本地无法补。系统现在会按待补清单 + 退避探测
   自动盯着，上游一补发就补齐；在那之前该项保持红。
2. **xiaoshi 不可达**：这是外部依赖故障（DNS 解析到 `28.0.0.137`，全部 `/api/v3/*` 失败），
   不是本仓库代码能修的。当前影响：`bar.minute` 链首空转、`xiaoshi_min_all` 停更。
   建议在其恢复前把 `bar.minute / xiaoshi_min1` 的数据项开关置 0，恢复后再打开（阈值、开关位置见
   `data_gateway_item_switch`）。本轮未擅自改，因为它是唯一提供完整 1min 历史且不吃 Tushare 额度的源。
3. **路由表陈旧**：`routing_table.json` 生成于 2026-10-18，`margin_detail` 的链序里没有 teajoin。
   `_ordered_providers` 会把「已启用但未列入」的源追加到链尾，所以不会丢源，但 priority 语义失真。
   重生成脚本会连带改变所有数据集的链序（policy fingerprint 变化），建议单独一轮做。

## 5. 复现

```bash
cd backend
NUMBA_CACHE_DIR=/tmp/nbcache_sirf ./.venv/bin/python -m pytest tests/test_datacenter_complete_rows.py \
  tests/test_margin_detail_fill.py tests/test_replay_offline_incentive.py -q
# 全窗口实证（约 20s，只跑两个激励策略）
NUMBA_CACHE_DIR=/tmp/nbcache_sirf ./.venv/bin/python - <<'PY'
# 见 docs/reviews 同名提交信息中的 replay_midlong 调用；输出目录用临时 staging，不覆盖已发布 R12
PY
```
