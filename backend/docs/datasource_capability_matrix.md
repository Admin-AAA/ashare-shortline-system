# 数据源能力矩阵

实测时间：2026-10-31T10:05:30+0800，最近交易日 20261030。 由 `scripts/probe_datasource_capabilities.py` 生成（每 provider×api 1 次小调用），勿手改。

图例：✅有数据 ⚪调用成功但空 ⛔无权限 ❌错误；格内为 行数/最新日期/字段完整度/延迟。

| api | promax | quantdata | teajoin |
|---|---|---|---|
| adj_factor | ✅ 17/20261030/100%/596ms | ✅ 17/20261030/100%/412ms | ✅ 17/20261030/100%/2992ms |
| anns_d | ❌ unverified:http_client | ❌ unsupported:provider_error | ❌ unverified:provider_error |
| block_trade | ✅ 650/20261030/100%/813ms | ✅ 718/20261030/100%/400ms | ✅ 718/20261030/100%/11376ms |
| broker_recommend | ❌ unverified:http_client | ✅ 237/202610/100%/874ms | ✅ 237/202610/100%/7268ms |
| cyq_perf | ✅ 17/20261030/100%/500ms | ✅ 17/20261030/100%/451ms | ✅ 17/20261030/100%/2937ms |
| daily | ✅ 17/20261030/100%/1248ms | ✅ 17/20261030/100%/2230ms | ✅ 17/20261030/100%/2919ms |
| daily_basic | ✅ 17/20261030/100%/175ms | ✅ 17/20261030/100%/377ms | ✅ 17/20261030/100%/3772ms |
| fina_indicator | ✅ 158/20261024/100%/3529ms | ✅ 100/20261024/100%/890ms | ✅ 236/20261024/100%/17859ms |
| fina_indicator_vip | ❌ unsupported:http_client | ✅ 8118/20261030/100%/9653ms | ✅ 8047/20260325/99%/33310ms |
| hk_hold | ⚪ 0/-/100%/118ms | ⚪ 0/-/100%/644ms | ⚪ 0/-/100%/2084ms |
| hsgt_top10 | ✅ 80/20261030/100%/482ms | ✅ 80/20261030/100%/365ms | ❌ unverified:invalid_request |
| index_daily | ✅ 17/20261030/100%/3601ms | ✅ 17/20261030/100%/338ms | ✅ 17/20261030/100%/17180ms |
| limit_list_d | ✅ 160/20261030/95%/872ms | ✅ 160/20261030/95%/196ms | ✅ 160/20261030/100%/7228ms |
| margin | ❌ unverified:http_client | ✅ 22/20261030/100%/423ms | ✅ 21/20261029/100%/9429ms |
| margin_detail | ✅ 16/20261029/91%/89ms | ✅ 16/20261029/91%/747ms | ✅ 16/20261029/100%/3436ms |
| moneyflow | ✅ 17/20261030/100%/835ms | ✅ 17/20261030/100%/404ms | ✅ 17/20261030/100%/2876ms |
| moneyflow_hsgt | ✅ 16/20261030/100%/168ms | ✅ 16/20261030/100%/2100ms | ✅ 15/20261029/100%/1636ms |
| namechange | ✅ 4/20120120/100%/116ms | ✅ 4/20120120/100%/350ms | ✅ 4/20120802/100%/925ms |
| pledge_stat | ✅ 650/20261023/100%/62ms | ✅ 650/20261023/100%/431ms | ✅ 649/20261016/100%/19227ms |
| repurchase | ⛔ unsupported:permission | ✅ 755/20261030/100%/703ms | ✅ 625/20261030/100%/5983ms |
| rt_k | ✅ 1/-/100%/52ms | ❌ unsupported:provider_error | ❌ unverified:provider_error |
| rt_min | ✅ 1/-/100%/469ms | ✅ 1/-/89%/598ms | ❌ unverified:provider_error |
| share_float | ✅ 106/20150520/100%/606ms | ✅ 106/20150520/100%/409ms | ✅ 109/20150520/100%/3675ms |
| stk_auction | ✅ 8/20261030/100%/978ms | ✅ 8/20261030/100%/471ms | ✅ 8/20261030/100%/6226ms |
| stk_factor | ✅ 17/20261030/100%/891ms | ✅ 17/20261030/100%/1425ms | ✅ 17/20261030/100%/1896ms |
| stk_factor_pro | ✅ 17/20261030/100%/711ms | ✅ 17/20261030/100%/457ms | ✅ 17/20261030/100%/4735ms |
| stk_holdernumber | ❌ transient:business_rate_limit | ✅ 150/20261024/100%/326ms | ✅ 156/20260815/100%/5286ms |
| stk_holdertrade | ✅ 1669/20261030/100%/6473ms | ✅ 1726/20261030/100%/556ms | ✅ 1971/20261030/100%/2856ms |
| stk_limit | ✅ 17/20261030/100%/1139ms | ✅ 17/20261030/100%/398ms | ✅ 17/20261030/100%/9631ms |
| stk_mins | ✅ 13/-/100%/315ms | ❌ unsupported:provider_error | ❌ unverified:provider_error |
| stk_surv | ✅ 400/-/90%/1072ms | ✅ 400/-/90%/365ms | ✅ 10000/-/100%/20007ms |
| stock_basic | ✅ 1/-/100%/613ms | ✅ 1/-/67%/370ms | ✅ 1/-/67%/340ms |
| suspend_d | ✅ 60/20261030/100%/43ms | ✅ 60/20261030/100%/400ms | ✅ 60/20261030/100%/3532ms |
| ths_index | ❌ unverified:http_client | ✅ 395/-/100%/176ms | ✅ 412/-/100%/1503ms |
| ths_member | ✅ 281/-/100%/361ms | ✅ 281/-/100%/440ms | ✅ 280/-/100%/10318ms |
| top_inst | ✅ 960/20261030/100%/2215ms | ✅ 960/20261030/100%/265ms | ✅ 960/20261030/100%/7028ms |
| top_list | ✅ 96/20261030/100%/573ms | ✅ 96/20261030/100%/629ms | ✅ 96/20261030/100%/5052ms |
| trade_cal | ✅ 30/20261030/100%/664ms | ✅ 22/20261030/100%/229ms | ✅ 30/20261030/100%/4994ms |

## 限速（配置值，次/分钟）

- promax: 180
- quantdata: 120
- teajoin: 420

## 与上次实测相比的漂移

- promax/hsgt_top10: no_permission -> ok
- promax/moneyflow_hsgt: no_permission -> ok
- promax/stk_holdernumber: ok -> error
