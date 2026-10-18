# 数据源能力矩阵

实测时间：2026-10-18T21:20:56+0800，最近交易日 20261016。 由 `scripts/probe_datasource_capabilities.py` 生成（每 provider×api 1 次小调用），勿手改。

图例：✅有数据 ⚪调用成功但空 ⛔无权限 ❌错误；格内为 行数/最新日期/字段完整度/延迟。

| api | promax | quantdata |
|---|---|---|
| adj_factor | ✅ 15/20261016/100%/565ms | ✅ 15/20261016/100%/1289ms |
| anns_d | ❌ unverified:http_client | ❌ unsupported:provider_error |
| block_trade | ✅ 586/20261016/100%/3107ms | ✅ 628/20261016/100%/534ms |
| broker_recommend | ❌ unverified:http_client | ✅ 237/202610/100%/2059ms |
| cyq_perf | ✅ 15/20261016/100%/1007ms | ✅ 15/20261016/100%/1578ms |
| daily | ✅ 15/20261016/100%/30ms | ✅ 15/20261016/100%/1503ms |
| daily_basic | ✅ 15/20261016/100%/5819ms | ✅ 15/20261016/100%/572ms |
| fina_indicator | ✅ 56/20260815/8%/21ms | ✅ 100/20260815/100%/895ms |
| fina_indicator_vip | ❌ unsupported:http_client | ✅ 8115/20260902/100%/14949ms |
| hk_hold | ✅ 1/20260930/100%/478ms | ✅ 1/20260930/100%/604ms |
| hsgt_top10 | ✅ 70/20261016/100%/665ms | ✅ 70/20261016/100%/1082ms |
| index_daily | ✅ 15/20261016/100%/1015ms | ✅ 15/20261016/100%/610ms |
| limit_list_d | ✅ 120/20261016/100%/1894ms | ✅ 120/20261016/100%/865ms |
| margin | ❌ unverified:http_client | ✅ 19/20261016/100%/557ms |
| margin_detail | ✅ 14/20261015/100%/81ms | ✅ 14/20261015/100%/389ms |
| moneyflow | ✅ 15/20261016/100%/677ms | ✅ 15/20261016/100%/616ms |
| moneyflow_hsgt | ✅ 15/20261016/100%/193ms | ✅ 15/20261016/100%/544ms |
| namechange | ✅ 4/20120120/100%/916ms | ✅ 4/20120120/100%/567ms |
| pledge_stat | ❌ transient:http_rate_limit | ✅ 649/20261016/100%/1397ms |
| repurchase | ✅ 588/20261017/100%/564ms | ✅ 588/20261017/100%/1613ms |
| rt_k | ❌ transient:http_rate_limit | ❌ unsupported:provider_error |
| rt_min | ✅ 1/-/100%/44ms | ✅ 1/-/89%/427ms |
| share_float | ✅ 106/20150520/100%/157ms | ✅ 106/20150520/100%/824ms |
| stk_auction | ❌ transient:http_rate_limit | ✅ 7/20261016/100%/819ms |
| stk_factor | ❌ transient:http_rate_limit | ✅ 15/20261016/100%/1850ms |
| stk_factor_pro | ✅ 15/20261016/8%/5095ms | ✅ 15/20261016/100%/864ms |
| stk_holdernumber | ✅ 149/20260815/100%/74ms | ✅ 149/20260815/100%/1157ms |
| stk_holdertrade | ✅ 2391/20261017/100%/28714ms | ✅ 2447/20261017/100%/2539ms |
| stk_limit | ✅ 15/20261016/100%/542ms | ✅ 15/20261016/100%/566ms |
| stk_mins | ⚪ 0/-/100%/50ms | ❌ unsupported:provider_error |
| stock_basic | ✅ 1/-/100%/27ms | ✅ 1/-/67%/1053ms |
| suspend_d | ❌ transient:http_rate_limit | ✅ 96/20261016/100%/546ms |
| ths_index | ❌ unverified:http_client | ✅ 395/-/100%/745ms |
| ths_member | ✅ 280/-/100%/959ms | ✅ 280/-/100%/700ms |
| top_inst | ✅ 660/20261016/100%/5824ms | ✅ 660/20261016/100%/372ms |
| top_list | ✅ 61/20261016/100%/15095ms | ✅ 61/20261016/100%/645ms |
| trade_cal | ✅ 30/20261017/100%/262ms | ✅ 30/20261017/100%/300ms |

## 限速（配置值，次/分钟）

- promax: 180
- quantdata: 120
