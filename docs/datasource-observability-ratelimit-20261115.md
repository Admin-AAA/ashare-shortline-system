# 数据源可观测性与限频收口（P0/P1/P2 全 7 项，2026-11-15）

上一轮评估结论：架构无结构性缺陷，短板是「可观测性断层 + 跨进程限频未开」。本次把 7 项全部落地。

---

## 0. 一句话总览

| 项 | 级别 | 改动 | 落点 |
|---|---|---|---|
| ① 实时配额视图 | P0 | 新增 `GET /api/data-sources/limits` | `app/api/data_sources.py`、`app/services/datasource/quota_view.py` |
| ② 跨进程限频 | P0 | 真正启用 `.env` 的 `TUSHARE_SHARED_LIMITER_DB` | `app/core/runtime_env.py`、`tushare_transport.py` |
| ③ promax 双口径 | P0 | 声明值 vs 实际窗口对齐 + 告警 | `promax_window.py`（`window_table()`） |
| ④ 滑动窗口速率 | P1 | metrics 增加 60s/多窗口速率 | `app/core/metrics.py` |
| ⑤ 空返回率告警 | P1 | provider 级空返回率纳入巡检 | `data_quality_monitor.py` |
| ⑥ thsdk 间隔参数化 | P2 | `ths_min_interval_seconds` + 每分钟限频 | `config.py`、`ths_client.py` |
| ⑦ 熔断阈值分源 | P2 | 按 provider 覆盖 timeout/bad_data 阈值 | `shared_breaker.py`、`config.py` |

---

## 1. 根因：配置写了但没生效（②）

`Settings` 走 `env_prefix="ASHARE_"` 读 `.env`，**但不会把值写回 `os.environ`**。
而 `tushare_transport._make_limiter()` 是直接 `os.environ.get("TUSHARE_SHARED_LIMITER_DB")` 判断的，
于是「在 `.env` 里配了」和「进程内真启用」是两件事——这正是之前跨进程限频一直关闭的原因。

修法：新增 `app/core/runtime_env.py`

- `env_value(*names, default="")`：进程环境 → `.env` 文件依次取值，支持 `ASHARE_` 前缀别名、剥注释与引号
- `env_flag()`：布尔解析
- `dotenv_value()`：**只读 `.env` 文件**，不看 `os.environ`

`shared_limiter_db_path()` 统一走它，并把相对路径按 **backend 根目录**解析——
避免 backend 与 Prefect worker 因 CWD 不同各建一个库，"共享"变成两份配额。

> 附带修的坑：`scripts/` 下的回测脚本会 `load_dotenv()` 把 `.env` 灌进 `os.environ`，
> 导致 pytest 进程分不清值是"进程给的"还是"从 .env 泄露来的"，进而把**生产限频库**当测试库写。
> 现在 pytest 下对"与 `.env` 值相同"的 `os.environ` 一律不采信（视作泄漏），测试与线上库彻底隔离。

---

## 2. 配额视图 `GET /api/data-sources/limits`

```
GET /api/data-sources/limits?hours=24      # hours 限幅 1~720
```

返回段：

- `shared_limiter`：是否启用、库路径（这是判断 ② 是否真的生效的唯一可靠口径）
- `limiters[]`：每个限频器的 `used / limit / window / scope(in_process|shared) / alive / headroom_pct`
  - `alive=false` 时 `used=None` 并给 `note`——**不能把它显示成「用量 0」**，那会把"没启用"误读成"没用额度"
- `daily`：promax/quantdata/teajoin 当日用量与日额度水位
- `promax`：`declared_rpm`（config 的 180）vs `effective_windows`（真实硬窗口）与 `declared_vs_effective_ratio`
- `breakers`：当前熔断状态
- `direct_sources`：sina/tdx/akshare/baostock 直连源的 RPM 常量
- `rates`：metrics 的滑动窗口速率
- `quality`：provider 级成功率/空返回率
- `warnings`：自动诊断（跨进程限频关闭、声明口径高于实际等）

实现要点：`inspect` 不到/DB 不全时**降级返回带 `error` 的空结构而不是 500**——
观测接口自己 500 是最糟的情况。limiter 用量靠 `app/core/limiter_registry.py` 弱引用登记，
provider 实例被回收后自然标记 `alive=false`。

---

## 3. promax 双口径（③）

`app/core/promax_window.py` 的真实硬窗口是

```python
DEFAULT_WINDOWS = ((300.0, 300, 100), (3600.0, 2400, 900))
#                   window秒, 非盘中上限, 盘中上限
```

即盘中 **100 次/5min + 900 次/1h**，而 `config.tushare_promax_rate_limit_per_minute=180` 是估算值，两者差约 4.5 倍。

处理方式：不篡改 config（很多地方拿它做粗估），而是

1. `window_table()` 读 `ASHARE_PROMAX_WINDOW_TABLE`（JSON）允许不改代码调整，解析失败回落默认
2. `research_acquire()` 改用它，`WINDOWS` 保留为 `DEFAULT_WINDOWS` 别名
3. `/limits` 里并列声明 `declared_rpm` 与 `effective_windows`，差值超阈值直接出 warning

**决策请以 `effective_windows` 为准**，`declared_rpm` 仅供粗估。

---

## 4. 滑动窗口速率（④）

原来 metrics 只有进程累计计数器，重启归零、也没有"当前每秒多少请求"。
现在 `app/core/metrics.py` 增加 `_recent`（130 个秒级桶的 deque）+ `rates(window_seconds)`，
`inc()` 同步 bump；`/api/metrics/runtime` 与 `/api/data-sources/limits` 都带 `rates`。

注意：仍是**进程内**统计，速率反映当前进程流量，重启后从零开始（累计值另有 `processes` 段跨进程聚合）。

---

## 5. 空返回率告警（⑤）

`data_quality_monitor` 原来只管数据集新鲜度，不管请求层成功率。
新增两项巡检（均不抛异常、各自 `_rollback`）：

- `gateway:provider_empty_rate`：按 provider 算 success/empty/error 率，
  `≥60%` 红、`≥30%` 黄、样本 `<50` 判 `insufficient_sample` 不告警
- `gateway:quota_limits`：跨进程限频开关、熔断、日额度水位（≥80% 黄、耗尽红）

判据来自实测：`eastmoney` 空返回率 79.9%（实际废源）、`tushare_stk_mins` 12.5%，
按源分别看才定位得到，聚合到"数据集"层会被平均掉。

> 本次修掉的静默失败：`quality_by_provider()` 里 import 了**不存在**的 `TraceRow`（真名是
> `DataSourceRequestTrace`），`except Exception: return []` 把 ImportError 吞了，
> 结果 quality 段长期恒空、`provider_empty_rate` 实际从未生效。已加防回归测试（建真表 + 插入 60 行验证）。

---

## 6. thsdk 限频参数化（⑥）

- 原来只有 `_throttle()` 的进程内最小间隔 0.06s（理论 1000/min，但全局单锁串行实际约 16.7 QPS）
- 新增 `ths_rate_limit_per_minute`（默认 0 = 关闭），>0 时先过共享限频器再走最小间隔
- `ths_min_interval_seconds` 仍是唯一的间隔来源，不再散落魔数

---

## 7. 熔断阈值分源（⑦）

`kind_threshold(kind, key=None)` 支持 provider 覆盖：

```env
ASHARE_BREAKER_THRESHOLD_OVERRIDES={"teajoin":{"timeout":4},"promax":{"timeout":1,"bad_data":2},"eastmoney":{"bad_data":1},"quantdata":{"timeout":3}}
```

取值逻辑：`key` 按 `:` 分段匹配 provider 命名空间（兼容 `gw:promax:rt_k` 这类），未命中回落全局默认
（timeout=2、bad_data=3）。`rate_limit` 仍是立即熔断（上游已明确拒绝，重试只会更糟）。

依据：promax 是实时行情主源，超时应立刻切换（阈值收紧到 1）；teajoin 覆盖 205 个接口、
偶发超时不代表不可用（放宽到 4），避免频繁熔断导致大面积降级。

---

## 8. 本次新增配置（`backend/.env`）

```env
TUSHARE_SHARED_LIMITER_DB=data/runtime/tushare_ratelimit.sqlite
ASHARE_TUSHARE_SHARED_LIMITER_DB=data/runtime/tushare_ratelimit.sqlite   # 前缀别名
ASHARE_BREAKER_THRESHOLD_OVERRIDES={"teajoin":{"timeout":4},...}
ASHARE_THS_MIN_INTERVAL_SECONDS=0.06
ASHARE_THS_RATE_LIMIT_PER_MINUTE=900
# ASHARE_PROMAX_WINDOW_TABLE=[[300,300,100],[3600,2400,900]]
```

改前已备份为 `.env.bak.<时间戳>`。**backend 与 Prefect worker 都必须重启**才吃到新值
（两者都靠 `runtime_env` 在进程启动时读）。

---

## 9. 验证

```bash
curl -s 'http://127.0.0.1:8765/api/data-sources/limits?hours=72' | jq '.shared_limiter, .quality, .warnings'
curl -s 'http://127.0.0.1:8765/api/admin/data-quality?refresh=true' | jq '.items[]|select(.key|startswith("gateway:"))'
```

实测（2026-11-15 收盘后）：

- `shared_limiter.enabled = true`，库路径解析为绝对路径
- 6 个限频器可见：`teajoin(420, shared)`、`promax(180, shared)`、`quantdata(120, shared)`、
  `sina_realtime(600, in_process)`、`baostock(17, in_process)`、`xiaoshi_min1(alive=false, 已退役)`
- `quality`：teajoin 4946 次空返回率 0%；`warnings` 仅剩 promax 口径差 4.5 倍一条
- 巡检 total 71 → 73，新增两项 green
- 全量测试 **4281 passed, 24 skipped**

---

## 10. 仍未做（明确取舍）

- **promax 卖家真实额度**：`declared_rpm=180` 与硬窗口差 4.5 倍这件事，代码只能"暴露"不能"消除"，
  仍需找卖家确认口径；`ASHARE_PROMAX_WINDOW_TABLE` 留了不改代码的调整入口。
- **不引入 Prometheus**：当前规模用 SQLite + 自建视图够用，加 exporter 是新的运维面，
  等真要多机聚合再上。
- **thsdk 跨进程限频**：同一进程内全局锁串行已把并发压到 16.7 QPS，跨进程共享优先级低。
