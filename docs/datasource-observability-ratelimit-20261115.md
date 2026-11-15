---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 1084f7c18024812a92491b2bbef914fe_e59c1829e0d811f18c5752540024e231
    ReservedCode1: Se8E569jAlFB5rGgppox9SVAfoagQuozDEr7r97rNVqbnZpLY1Am0Szjksy32rxevhO1dBv//OU+7hTw8owsC9MCmGIqiSmkdyBwQZqsGPtP9lhunws9fwVnfS9tbsoq5w5EK4VJKzbIziG+QVrwmYjsv9yhuGWs4yfypdya8PRtm2fUDE6TYVFIUjU=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 1084f7c18024812a92491b2bbef914fe_e59c1829e0d811f18c5752540024e231
    ReservedCode2: Se8E569jAlFB5rGgppox9SVAfoagQuozDEr7r97rNVqbnZpLY1Am0Szjksy32rxevhO1dBv//OU+7hTw8owsC9MCmGIqiSmkdyBwQZqsGPtP9lhunws9fwVnfS9tbsoq5w5EK4VJKzbIziG+QVrwmYjsv9yhuGWs4yfypdya8PRtm2fUDE6TYVFIUjU=
---

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
（timeout=2、bad_data=3）。~~`rate_limit` 仍是立即熔断~~ → **已被第 12 节取代：限频不再熔断，只暂停并降级**。

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

---

## 11. 上游速率峰值实测与限速/熔断重标定（2026-11-15，用户指令：只测峰值）

前提：不再区分付费版/免费版，不设日额度上限，直接并发加压到上游拒绝为止。探测脚本
`temp/probe_rate_concurrent.py`（多线程、只打 `trade_cal` / `rt_k` 最轻接口、绕开网关）。

### 11.1 实测峰值

| 源 | 并发 | 结果 | 首次拒绝 | 结论（真实峰值） |
| --- | --- | --- | --- | --- |
| promax | 12 线程 | 1685 次，ok 200 / bad 1485 | 4.36s | **200 次/分钟硬顶**（IP 维度），响应带 `请在 56 秒后重试` |
| teajoin | 8 线程 | 911 次，ok 550 / bad 361 | 23.86s | **约 550 次/分钟软顶**，两次独立压测都停在第 550 次 |
| quantdata | 64 线程 | 3592 次，ok 131 / bad 3461 | 2.63s | **无法靠加线程提升**：单请求约 5s，高并发直接 `50302 service temporarily unavailable` |

补充证据：
- promax 串行 130 次全 200、133/min；`X-Ratelimit-Ip-Limit: 200` 恒定，`Ip-Remaining` 190→126 递减后按分钟回补 → 滑动分钟窗。总额度 `X-Ratelimit-Limit: 20000`。
- teajoin 8 线程 25s（1110/min）492 次全绿；延长到 40s 在第 550 次被 `{"code":-1,"msg":"rate limit exceeded"}` 拒。
- quantdata 16 线程 294/min 全绿且无任何限频头；32 线程仍 294/min（受延迟而非限频约束）；64 线程 2.6s 即崩。

### 11.2 据此重标定

| 项 | 原值 | 新值 | 依据 |
| --- | --- | --- | --- |
| `teajoin` 限速 | 420/min | **480/min** | 实测 550 软顶，留 13% 余量 |
| `quantdata` 限速 | 120/min | **90/min** | 高并发即 503，属低并发高延迟源 |
| `promax` 限速 | 180/min | 180/min（不变） | 对 200 硬顶留 10% 余量 |
| `promax` research 硬窗口 | 5min 300 / 1h 2400（盘中 20/min） | **60s 160 / 1h 9600（盘中 160/min）** | 旧值比上游低约 10 倍，"5min 100 次"来自代理连接池耗尽，与 IP 计数限频是两件事 |
| 限频熔断阈值 | 1 次即熔断 | ~~连续 2 次~~ → **不再熔断**（第 12 节） | 免费额度下限频是常态噪音，单发停摆 5 分钟代价大于限频本身 |
| 限频熔断冷却 | 300s（无 retry-after 时） | ~~90s~~ → **暂停 60s**（有 retry-after 用上游值，第 12 节） | 上游窗口都是分钟级；promax 直接给 56s |
| 半开探测封顶 | 1200s | **600s** | 实测上游恢复都在 60~90s 量级 |

### 11.3 验证

- `.env` 三处显式覆盖同步更新（`QUANTDATA=90` / `TEAJOIN=480` / 窗口表注释）。
- 重启 `com.wangao.ashare-backend` 后 `GET /api/data-sources/limits`：`teajoin(480)`、`quantdata(90)`、`promax(180)`；`promax.tightest_effective_per_minute=160`、`declared_vs_effective_ratio=1.12`，**原"口径差 4.5 倍"告警已消失**。
- 全量测试 **4285 passed / 24 skipped**（新增 4 条限频熔断用例）。
## 12. 限频不再熔断：改为「暂停 + 降级」（2026-11-15 二次重设）

用户指令：**命中上游次数上限时不允许熔断该数据源，应立刻走下一个源；只有真故障才熔断并冷却。**
网关的职责是尽可能少熔断——熔断是故障兜底，不是限频的处理方式。

### 12.1 语义拆分

| 失败类型 | 旧行为 | 新行为 |
| --- | --- | --- |
| 分钟级限频（429 / `ip_rate_limited` / `rate limit exceeded` / 容量忙） | 达阈值开熔断，冷却 60~90s，期间源被整体停摆、进半开探测、降健康权重 | **只写暂停标记**（默认 60s，有 retry-after 用上游值），当次请求立即降级到下一个 provider；熔断状态保持 `closed` |
| 日额度耗尽（`42901`、`quota_exhausted`） | 与限频混在一起 | **单独 `quota_exhausted`**，真熔断 1h（`ASHARE_QUOTA_EXHAUSTED_SECONDS`） |
| 超时 / 坏数据 / 5xx / 无权限 | 熔断 | 不变（这才是熔断该管的事） |

实现要点（`app/core/shared_breaker.py`）：

- 新增 `PAUSE_KEY_SUFFIX="|pause"`、`PAUSE_BASE_SECONDS`（`ASHARE_RATE_LIMIT_PAUSE_SECONDS`，默认 60）、
  `PAUSE_MAX_SECONDS=300`、`QUOTA_EXHAUSTED_SECONDS`（默认 3600）。
- `record_failure(kind="rate_limit")` **不再调用 `_open`**，只写跨进程暂停行；`allow_request()` 先查暂停，
  命中即返回 False（等价"这一次跳过本源"）。
- 暂停不写探针记录、不累加 `probe_streak`、不改健康权重、不进半开状态机；`record_success()` 顺带清除。
- `shared_breaker_states()` 每行带 `kind: pause|breaker`，展示层据此分离。

### 12.2 分类补齐（否则策略失效）

三个上游的限频形态各不相同，任一形态被误判成 `provider_error`/`server_error` 都会退回按次数熔断：

| 上游 | 实测失败形态 | 补丁 |
| --- | --- | --- |
| promax | `{"ok":false,"error":"ip_rate_limited","message":"…请在 56 秒后重试"}` | `_business_failure` 兼容 `error`/`message` 字段；`_RATE_LIMIT_MESSAGE_TERMS` 加 `rate_limited`/`too many requests`；新增 `_retry_hint_seconds()` 解析"56 秒后重试" |
| teajoin | `{"code":-1,"msg":"rate limit exceeded"}` | 同上的 `rate_limited` 词表 |
| quantdata | `{"code":50302,"msg":"service temporarily unavailable"}` | 新增 `_CAPACITY_BUSINESS_CODES={"50302"}`；`_BUSINESS_CAPACITY_TERMS` / `_CAPACITY_EXHAUSTED_TERMS` 加 `temporarily unavailable` 等措辞 → 判为容量忙（短冷却+换源），不再判 `PROVIDER_ERROR` |

### 12.3 展示与告警口径

- `/api/data-sources/limits` 的 `breakers` 每项新增 `kind`；
- `/api/metrics/runtime` 把暂停从 `open_breakers` 拆到 `rate_limit_pauses`；
- 数据源管理页 `_breaker_rows()` 排除 `%|pause`，健康巡检 `open_live` 不再把暂停算作"熔断打开"。

### 12.4 验证

- 新增 `tests/test_rate_limit_pause_semantics_20261115.py`（9 条）：三类限频形态分类、连续 4 次限频仍 `closed`、
  暂停到期不触发半开探测、日额度仍真熔断、暂停不出现在熔断视图。
- 改写旧语义用例：`test_datasource_limits_observability.py` 第 6 节、`test_quota_exhausted_skip.py` 分类断言、
  `test_gateway_audit_failures.py` 限频用例（暂停 + 当次降级）。
- 全量测试 **4296 passed / 24 skipped**。

*（内容由AI生成，仅供参考）*
