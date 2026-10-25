# 数据源能力矩阵

实测时间：2026-10-24T11:32:40+0800，最近交易日 20261023。 由 `scripts/probe_datasource_capabilities.py` 生成（每 provider×api 1 次小调用），勿手改。

图例：✅有数据 ⚪调用成功但空 ⛔无权限 ❌错误；格内为 行数/最新日期/字段完整度/延迟。

| api | promax | quantdata |
|---|---|---|
| adj_factor | ✅ 16/20261023/100%/1162ms | ✅ 16/20261023/100%/394ms |
| anns_d | ❌ unverified:http_client | ❌ unsupported:provider_error |
| block_trade | ✅ 706/20261023/100%/1666ms | ✅ 787/20261023/100%/3717ms |
| broker_recommend | ❌ unverified:http_client | ✅ 237/202610/100%/1475ms |
| cyq_perf | ✅ 16/20261023/100%/99ms | ✅ 16/20261023/100%/583ms |
| daily | ✅ 16/20261023/100%/999ms | ✅ 16/20261023/100%/1326ms |
| daily_basic | ✅ 16/20261023/100%/2077ms | ✅ 16/20261023/100%/443ms |
| fina_indicator | ✅ 235/20261024/47%/2025ms | ✅ 100/20261024/100%/648ms |
| fina_indicator_vip | ❌ unsupported:http_client | ✅ 8117/20260902/100%/135449ms |
| hk_hold | ✅ 1/20260930/100%/71ms | ✅ 1/20260930/100%/622ms |
| hsgt_top10 | ⛔ unsupported:permission | ✅ 70/20261023/100%/1577ms |
| index_daily | ✅ 16/20261023/100%/1625ms | ✅ 16/20261023/100%/2314ms |
| limit_list_d | ✅ 100/20261023/100%/101ms | ✅ 100/20261023/100%/514ms |
| margin | ❌ unverified:http_client | ✅ 22/20261023/100%/712ms |
| margin_detail | ✅ 15/20261022/100%/86ms | ✅ 15/20261022/100%/620ms |
| moneyflow | ✅ 16/20261023/100%/429ms | ✅ 16/20261023/100%/863ms |
| moneyflow_hsgt | ⛔ unsupported:permission | ✅ 15/20261023/100%/1166ms |
| namechange | ✅ 4/20120120/100%/67ms | ✅ 4/20120120/100%/419ms |
| pledge_stat | ✅ 649/20261016/100%/5547ms | ✅ 649/20261016/100%/411ms |
| repurchase | ❌ transient:http_rate_limit | ✅ 633/20261023/100%/403ms |
| rt_k | ✅ 1/-/100%/142ms | ❌ unsupported:provider_error |
| rt_min | ✅ 1/-/100%/314ms | ✅ 1/-/89%/909ms |
| share_float | ✅ 106/20150520/100%/1057ms | ✅ 106/20150520/100%/450ms |
| stk_auction | ✅ 8/20261023/100%/2805ms | ✅ 8/20261023/100%/882ms |
| stk_factor | ✅ 16/20261023/100%/111ms | ✅ 16/20261023/100%/535ms |
| stk_factor_pro | ✅ 16/20261023/100%/1470ms | ✅ 16/20261023/100%/510ms |
| stk_holdernumber | ✅ 150/20261024/100%/641ms | ✅ 150/20261024/100%/1178ms |
| stk_holdertrade | ✅ 1823/20261023/100%/2213ms | ✅ 1879/20261023/100%/626ms |
| stk_limit | ✅ 16/20261023/100%/1208ms | ✅ 16/20261023/100%/559ms |
| stk_mins | ✅ 13/-/100%/187ms | ❌ unsupported:provider_error |
| stock_basic | ✅ 1/-/100%/165ms | ✅ 1/-/67%/476ms |
| suspend_d | ✅ 84/20261023/100%/89ms | ✅ 84/20261023/100%/733ms |
| ths_index | ❌ unverified:http_client | ✅ 395/-/100%/596ms |
| ths_member | ✅ 280/-/100%/475ms | ✅ 280/-/100%/593ms |
| top_inst | ✅ 1010/20261023/100%/1512ms | ✅ 1010/20261023/100%/390ms |
| top_list | ✅ 101/20261023/100%/331ms | ✅ 101/20261023/100%/595ms |
| trade_cal | ✅ 30/20261023/100%/146ms | ✅ 22/20261023/100%/329ms |

## 限速（配置值，次/分钟）

- promax: 180
- quantdata: 120

## 与上次实测相比的漂移

- promax/hsgt_top10: ok -> no_permission
- promax/moneyflow_hsgt: ok -> no_permission
- promax/pledge_stat: error -> ok
- promax/repurchase: ok -> error
- promax/rt_k: error -> ok
- promax/stk_auction: error -> ok
- promax/stk_factor: error -> ok
- promax/suspend_d: error -> ok
