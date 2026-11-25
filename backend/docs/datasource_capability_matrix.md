# 数据源能力矩阵

实测时间：2026-11-25T12:25:38+0800，最近交易日 20261124。 由 `scripts/probe_datasource_capabilities.py` 生成（每 provider×api 1 次小调用），勿手改。

图例：✅有数据 ⚪调用成功但空 ⛔无权限 ❌错误；格内为 行数/最新日期/字段完整度/延迟。

| api | promax | quantdata | teajoin |
|---|---|---|---|
| adj_factor | ❌ transient:network | ❌ transient:quota_exhausted | ✅ 22/20261124/100%/237ms |
| anns_d | ❌ transient:network | ❌ transient:quota_exhausted |  |
| block_trade | ❌ transient:network | ❌ transient:quota_exhausted | ✅ 428/20261124/100%/289ms |
| broker_recommend | ❌ transient:network | ❌ transient:quota_exhausted | ✅ 265/202611/100%/251ms |
| cyq_perf | ❌ transient:network | ❌ transient:quota_exhausted | ✅ 22/20261124/100%/243ms |
| daily | ❌ transient:network | ❌ transient:quota_exhausted | ✅ 22/20261124/100%/623ms |
| daily_basic | ❌ transient:network | ❌ transient:quota_exhausted | ✅ 22/20261124/100%/565ms |
| fina_indicator | ❌ transient:network | ❌ transient:quota_exhausted | ✅ 236/20261024/100%/1340ms |
| fina_indicator_vip | ❌ transient:network | ❌ transient:quota_exhausted | ✅ 8047/20260325/100%/8816ms |
| hk_hold | ❌ transient:network | ❌ transient:quota_exhausted | ⚪ 0/-/100%/243ms |
| hsgt_top10 | ❌ transient:network | ❌ transient:quota_exhausted | ❌ unverified:invalid_request |
| index_daily | ❌ transient:network | ❌ transient:quota_exhausted | ✅ 22/20261124/100%/1476ms |
| limit_list_d | ❌ transient:network | ❌ transient:quota_exhausted | ✅ 125/20261124/100%/236ms |
| margin | ❌ transient:network | ❌ transient:quota_exhausted | ✅ 21/20261124/100%/229ms |
| margin_detail | ❌ transient:network | ❌ transient:quota_exhausted | ✅ 22/20261124/100%/251ms |
| moneyflow | ❌ transient:network | ❌ transient:quota_exhausted | ✅ 22/20261124/100%/306ms |
| moneyflow_hsgt | ❌ transient:network | ❌ transient:quota_exhausted | ✅ 22/20261124/100%/240ms |
| namechange | ❌ transient:network | ❌ transient:quota_exhausted | ✅ 4/20120802/100%/230ms |
| pledge_stat | ❌ transient:network | ❌ transient:quota_exhausted | ✅ 652/20261106/100%/1075ms |
| repurchase | ❌ transient:network | ❌ transient:quota_exhausted | ✅ 1027/20261124/100%/504ms |
| rt_k | ❌ transient:network | ❌ transient:quota_exhausted |  |
| rt_min | ❌ transient:network | ❌ transient:quota_exhausted |  |
| share_float | ❌ transient:network | ❌ transient:quota_exhausted | ✅ 109/20150520/100%/375ms |
| stk_auction | ❌ transient:network | ❌ transient:quota_exhausted | ✅ 7/20261124/100%/249ms |
| stk_factor | ❌ transient:network | ❌ transient:quota_exhausted | ✅ 22/20261124/100%/384ms |
| stk_factor_pro | ❌ transient:network | ❌ transient:quota_exhausted | ✅ 22/20261124/100%/1502ms |
| stk_holdernumber | ❌ transient:network | ❌ transient:quota_exhausted | ✅ 156/20260815/100%/707ms |
| stk_holdertrade | ❌ transient:network | ❌ transient:quota_exhausted | ✅ 1180/20261124/100%/532ms |
| stk_limit | ❌ transient:network | ❌ transient:quota_exhausted | ✅ 22/20261124/100%/234ms |
| stk_mins | ❌ transient:network | ❌ transient:quota_exhausted | ❌ unverified:provider_error |
| stk_surv | ❌ transient:network | ❌ transient:quota_exhausted | ✅ 10000/-/100%/7184ms |
| stock_basic | ❌ transient:network | ❌ transient:quota_exhausted | ✅ 1/-/100%/246ms |
| suspend_d | ❌ transient:network | ❌ transient:quota_exhausted | ✅ 63/20261124/100%/229ms |
| ths_index | ❌ transient:network | ❌ transient:quota_exhausted | ✅ 412/-/100%/416ms |
| ths_member | ❌ transient:network | ❌ transient:quota_exhausted | ✅ 282/-/100%/260ms |
| top_inst | ❌ transient:network | ❌ transient:quota_exhausted | ✅ 720/20261124/100%/405ms |
| top_list | ❌ transient:network | ❌ transient:quota_exhausted | ✅ 72/20261124/100%/247ms |
| trade_cal | ❌ transient:network | ❌ transient:quota_exhausted | ✅ 30/20261124/100%/244ms |

## 限速（配置值，次/分钟）

- promax: 180
- quantdata: 90
- teajoin: 480

## 与上次实测相比的漂移

- quantdata/adj_factor: ok -> error
- quantdata/block_trade: ok -> error
- quantdata/broker_recommend: ok -> error
- quantdata/cyq_perf: ok -> error
- quantdata/daily: ok -> error
- quantdata/daily_basic: ok -> error
- quantdata/fina_indicator: ok -> error
- quantdata/fina_indicator_vip: ok -> error
- quantdata/hk_hold: empty -> error
- quantdata/hsgt_top10: ok -> error
- quantdata/index_daily: ok -> error
- quantdata/limit_list_d: ok -> error
- quantdata/margin: ok -> error
- quantdata/margin_detail: ok -> error
- quantdata/moneyflow: ok -> error
- quantdata/moneyflow_hsgt: ok -> error
- quantdata/namechange: ok -> error
- quantdata/pledge_stat: ok -> error
- quantdata/repurchase: ok -> error
- quantdata/rt_min: ok -> error
- quantdata/share_float: ok -> error
- quantdata/stk_auction: ok -> error
- quantdata/stk_factor: ok -> error
- quantdata/stk_factor_pro: ok -> error
- quantdata/stk_holdernumber: ok -> error
- quantdata/stk_holdertrade: ok -> error
- quantdata/stk_limit: ok -> error
- quantdata/stk_surv: ok -> error
- quantdata/stock_basic: ok -> error
- quantdata/suspend_d: ok -> error
- quantdata/ths_index: ok -> error
- quantdata/ths_member: ok -> error
- quantdata/top_inst: ok -> error
- quantdata/top_list: ok -> error
- quantdata/trade_cal: ok -> error
