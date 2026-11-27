# 数据源能力矩阵

实测时间：2026-11-28T02:06:52+0800，最近交易日 20261127。 由 `scripts/probe_datasource_capabilities.py` 生成（每 provider×api 1 次小调用），勿手改。

图例：✅有数据 ⚪调用成功但空 ⛔无权限 ❌错误；格内为 行数/最新日期/字段完整度/延迟。

| api | promax | quantdata | teajoin |
|---|---|---|---|
| adj_factor | ❌ transient:network | ✅ 22/20261127/100%/611ms | ✅ 22/20261127/100%/192ms |
| anns_d | ❌ transient:network | ❌ unsupported:provider_error |  |
| block_trade | ❌ transient:network | ✅ 523/20261127/100%/950ms | ✅ 523/20261127/100%/207ms |
| broker_recommend | ❌ transient:network | ✅ 265/202611/100%/688ms | ✅ 265/202611/100%/185ms |
| cyq_perf | ❌ transient:network | ✅ 22/20261127/100%/679ms | ✅ 22/20261127/100%/189ms |
| daily | ❌ transient:network | ✅ 22/20261127/100%/1530ms | ✅ 22/20261127/100%/481ms |
| daily_basic | ❌ transient:network | ✅ 22/20261127/100%/1039ms | ✅ 22/20261127/100%/523ms |
| fina_indicator | ❌ transient:network | ✅ 100/20261024/100%/713ms | ✅ 236/20261024/100%/1399ms |
| fina_indicator_vip | ❌ transient:network | ✅ 8123/20261031/100%/15805ms | ✅ 8047/20260325/99%/1296ms |
| hk_hold | ❌ transient:network | ⚪ 0/-/100%/579ms | ⚪ 0/-/100%/194ms |
| hsgt_top10 | ❌ transient:network | ✅ 80/20261127/100%/570ms | ❌ unverified:invalid_request |
| index_daily | ❌ transient:network | ✅ 22/20261127/100%/647ms | ✅ 22/20261127/100%/213ms |
| limit_list_d | ❌ transient:network | ✅ 459/20261127/95%/825ms | ✅ 459/20261127/100%/248ms |
| margin | ❌ transient:network | ✅ 21/20261126/100%/580ms | ✅ 21/20261126/100%/184ms |
| margin_detail | ❌ transient:network | ✅ 21/20261126/91%/712ms | ✅ 21/20261126/100%/233ms |
| moneyflow | ❌ transient:network | ✅ 22/20261127/100%/637ms | ✅ 22/20261127/100%/270ms |
| moneyflow_hsgt | ❌ transient:network | ✅ 22/20261127/100%/944ms | ✅ 22/20261127/100%/198ms |
| namechange | ❌ transient:network | ✅ 4/20120120/100%/598ms | ✅ 4/20120802/100%/230ms |
| pledge_stat | ❌ transient:network | ✅ 654/20261120/100%/625ms | ✅ 654/20261120/100%/1021ms |
| repurchase | ❌ transient:network | ✅ 1438/20261127/100%/1424ms | ✅ 1221/20261127/100%/298ms |
| rt_k | ❌ transient:network | ❌ transient:http_rate_limit |  |
| rt_min | ❌ transient:network | ✅ 1/-/100%/596ms |  |
| share_float | ❌ transient:network | ✅ 106/20150520/100%/633ms | ✅ 109/20150520/100%/194ms |
| stk_auction | ❌ transient:network | ✅ 8/20261127/100%/1485ms | ✅ 8/20261127/100%/672ms |
| stk_factor | ❌ transient:network | ✅ 22/20261127/100%/704ms | ✅ 22/20261127/100%/325ms |
| stk_factor_pro | ❌ transient:network | ✅ 22/20261127/100%/1043ms | ✅ 22/20261127/100%/223ms |
| stk_holdernumber | ❌ transient:network | ✅ 150/20261024/100%/619ms | ✅ 156/20260815/100%/200ms |
| stk_holdertrade | ❌ transient:network | ✅ 1336/20261127/100%/827ms | ✅ 1198/20261127/100%/216ms |
| stk_limit | ❌ transient:network | ✅ 22/20261127/100%/586ms | ✅ 22/20261127/100%/197ms |
| stk_mins | ❌ transient:network | ❌ unsupported:provider_error | ❌ transient:circuit_open |
| stk_surv | ❌ transient:network | ✅ 400/-/90%/1127ms | ✅ 10000/-/100%/2366ms |
| stock_basic | ❌ transient:network | ✅ 1/-/100%/588ms | ✅ 1/-/100%/231ms |
| suspend_d | ❌ transient:network | ✅ 67/20261127/100%/595ms | ✅ 67/20261127/100%/197ms |
| ths_index | ❌ transient:network | ✅ 395/-/100%/704ms | ✅ 412/-/100%/199ms |
| ths_member | ❌ transient:network | ✅ 283/-/100%/716ms | ✅ 283/-/100%/218ms |
| top_inst | ❌ transient:network | ✅ 860/20261127/100%/622ms | ✅ 860/20261127/100%/248ms |
| top_list | ❌ transient:network | ✅ 86/20261127/100%/703ms | ✅ 86/20261127/100%/198ms |
| trade_cal | ❌ transient:network | ✅ 22/20261127/100%/623ms | ✅ 30/20261127/100%/208ms |

## 限速（配置值，次/分钟）

- promax: 180
- quantdata: 90
- teajoin: 480

## 与上次实测相比的漂移

- quantdata/adj_factor: error -> ok
- quantdata/block_trade: error -> ok
- quantdata/broker_recommend: error -> ok
- quantdata/cyq_perf: error -> ok
- quantdata/daily: error -> ok
- quantdata/daily_basic: error -> ok
- quantdata/fina_indicator: error -> ok
- quantdata/fina_indicator_vip: error -> ok
- quantdata/hk_hold: error -> empty
- quantdata/hsgt_top10: error -> ok
- quantdata/index_daily: error -> ok
- quantdata/limit_list_d: error -> ok
- quantdata/margin: error -> ok
- quantdata/margin_detail: error -> ok
- quantdata/moneyflow: error -> ok
- quantdata/moneyflow_hsgt: error -> ok
- quantdata/namechange: error -> ok
- quantdata/pledge_stat: error -> ok
- quantdata/repurchase: error -> ok
- quantdata/rt_min: error -> ok
- quantdata/share_float: error -> ok
- quantdata/stk_auction: error -> ok
- quantdata/stk_factor: error -> ok
- quantdata/stk_factor_pro: error -> ok
- quantdata/stk_holdernumber: error -> ok
- quantdata/stk_holdertrade: error -> ok
- quantdata/stk_limit: error -> ok
- quantdata/stk_surv: error -> ok
- quantdata/stock_basic: error -> ok
- quantdata/suspend_d: error -> ok
- quantdata/ths_index: error -> ok
- quantdata/ths_member: error -> ok
- quantdata/top_inst: error -> ok
- quantdata/top_list: error -> ok
- quantdata/trade_cal: error -> ok
