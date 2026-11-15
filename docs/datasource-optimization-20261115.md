# 数据源三项优化（适配模板化 / 链路自适应 / 分钟线按需缓存，2026-11-15）

承接架构复盘的三个问题：① 数据源适配碎片化 ② 实时行情链过长 ③ 全市场历史分钟线缺口。
结论仍是**不重构**，只做三处定点改造。全量 `pytest tests/ -q` → **4184 passed, 23 skipped**，
`ruff check` 全绿。

---

## 1. 问题① 适配碎片化 → `adapter.py` 模板

原来每接一条链路都要重写一遍 `fetch → 解析 → 归一化 → 映射成统一行`，8 个 provider 各有一份
`_f()`（float 归一），字段别名映射散落在各处，改一个口径要改 8 处。

新增 `backend/app/services/datasource/adapter.py`，把"所有源都要做的事"收敛成一层：

| 能力 | 函数 | 说明 |
|---|---|---|
| 数值归一 | `to_float(value, default=0.0)` | `None/NaN/""/"--"` → 默认值，`0` 不当作缺失 |
| 代码归一 | `code6(value)` / `to_ts_code(value)` | `600000.SH`/`sh600000`/`600000` 互转 |
| 字段取值 | `pick(row, aliases, default)` | 按别名优先级取值，兼容各源字段名差异 |
| 行标准化 | `quote_row(...)` / `quote_row_from_raw(raw, source=...)` | 输出统一行情行（含中文别名） |
| 集合标准化 | `normalize_mapping(rows, source=...)` | 列表 → `{code: row}` |
| Provider 基类 | `ProviderBase` | 实现 `name/supports/fetch` 骨架，子类只写"取数 + 映射" |
| 快速建源 | `make_provider(name, config_keys, supports, fetch, **kwargs)` | 一个函数造一个 provider，不必写类 |
| 链构造 | `build_chain(providers)` | 统一去重 + 过滤失效源 |

**收敛结果**：`web_backups` / `qvix` / `tdx_quote` / `eastmoney` / `sina_realtime` / `akshare_quote` /
`quote_realtime` 里的 7 份 `_f` 全部改成一行转调 `adapter.to_float`（行为逐字节保持，有测试锁定），
`quote_realtime._ts` 改用 `adapter.to_ts_code`。

**新源接入从此是三段式**：写 `fetch(raw) -> list[dict]` → `make_provider(...)` → 注册进 `gateway()` 的
`_providers`。归一化、别名字段、异常包装都不用再抄。

---

## 2. 问题② 实时链过长 → 自适应排序 + 单跳预算

原链 7 段串行：`tushare_rt_k → ths_realtime → sina_realtime → tdx_quote → tencent_qt → akshare_snapshot → tushare_rt_min`。
痛点不是"源多"，而是**每次都从头试**：链首 `rt_k` 在额度耗尽时仍要吃掉 1.5s 硬超时，接着每个慢源依次空耗，
3s 的整链 deadline 被前两跳吃光，后面的快源根本没机会被调用。

### 2.1 失败后置（`_ProviderHealth`，gateway.py）

- 进程内健康档案：`(provider, dataset) → {fails, ewma, ts}`；
- 连续失败 ≥1 的源在 TTL（`datasource_health_ttl_seconds`，默认 300s）内**排到候选队尾**，成功一次立即清零复位；
- 没有历史记录时所有源同分 → 排序退化为稳定排序，**原有链顺序不会被悄悄改掉**（旧测试锁定的顺序全部仍然成立）；
- 只观测延迟、不用延迟排序（`rt_k` 比 sina 慢但字段全，不能因慢被挤下去）。

### 2.2 单跳预算（`_Budget.step_timeout`）

`fetch_first` / `fetch_fill` 两条主链路都先取候选、再按位次分配预算：

| 位次 | 分到的时间 |
|---|---|
| 链首 | `remaining × datasource_first_step_share`（默认 0.6，3s → 1.8s，仍够 `rt_k` 的 1.5s 硬截止） |
| 后备 | `min(remaining/剩余候选数 × datasource_step_share, remaining × datasource_backup_step_share)` |
| 下限 | `datasource_step_min_seconds`（0.8s），且不会突破 `remaining` |

`remaining` 按**真实剩余时间**算（不预扣），所以预算随实际耗时自然递减。以实时 3s / 三源为例：

```
链首 0.6×3.0 = 1.8s   → 若真耗尽，剩 1.2s
第二 0.6s            → 剩 0.6s
第三 max(0.8, min(0.6, 0.6)) = 0.6s   ← 改动前这里拿 0 剩余，直接记 deadline 返回空
```

链首慢源再也不能独占整链，每个候选都拿得到时间片。分钟线 120s / 四源则为 72s → 60s → 60s → 60s。
前置的 `rt_k` 1.5s 硬截止逻辑保留不变。

### 2.3 新增配置

```bash
ASHARE_DATASOURCE_ADAPTIVE_ROUTING=true    # 自适应排序总开关，false 退回纯链序
ASHARE_DATASOURCE_HEALTH_TTL_SECONDS=300   # 失败记忆的生命周期
ASHARE_DATASOURCE_FIRST_STEP_SHARE=0.6
ASHARE_DATASOURCE_BACKUP_STEP_SHARE=0.5
ASHARE_DATASOURCE_STEP_SHARE=2.0           # 后备源"均分 × 该倍数"
ASHARE_DATASOURCE_STEP_MIN_SECONDS=0.8
```

---

## 3. 问题③ 分钟线缺口 → 按月按需缓存（不囤全市场）

全市场 3 年 1 分钟约 10 亿行 / 20GB，囤不动也没必要——真正跑回测的票是少数。改为**按需取用 + 按月落盘**。

新增 `backend/app/services/datasource/minute_cache.py`：

- 路径 `backend/data/minute_cache/{freq}/{code}/{YYYY-MM}.parquet`，`minute_cache_dir` 可覆盖根目录；
- **只缓存已经走完的月份**（`_persistable`）：当月数据还会增长，写盘就再也不会刷新，所以当月每次重拉；
- **窗口截断自愈**：某月返回空但窗口 >5 天时对半二分重试（单月最多 12 次额外调用）——
  上游 `stk_mins` 单次 8000 行，长区间会"整窗放弃返回空列表"，二分会把真有的那段救回来；
  窄到 5 天仍空就认定是真没数据（周末/停牌/停牌退市），不再浪费调用；
- **读写语义一致**：parquet 往返会把 `None` 变 `NaN`、整数列变 float，读回时统一还原成 `None`；
- **故障隔离**：`serve()` 捕获所有异常 → 回落纯网关，缓存坏了绝不拖垮取数主链路。

### 3.1 接线方式

`bar_minute.fetch(..., use_cache=True)` 是唯一入口，默认先走缓存：命中直接返回（provider 记为
`minute_cache`），缺失才向上游补并落盘。缓存内部补数时传 `use_cache=False` 走纯网关，避免自调用回环。
上游确实没数据时直接透传上游的 `attempts`，不重复打一次。

调用方（`tushare_client` / `topology` / 回测脚本）**无需改动**，签名向后兼容。

### 3.2 使用

```bash
# 只查本地覆盖情况，不联网
python -m app.services.datasource.minute_cache --codes 600000,000001 \
  --start 2024-01-01 --end 2024-06-30 --stats-only

# 预热（回测前跑一次，之后重复回跑不再打上游）
python -m app.services.datasource.minute_cache --codes 600000,000001 \
  --start 2024-01-01 --end 2024-06-30 --freq 1min

# 强制重拉
python -m app.services.datasource.minute_cache --codes 600000 --start 2024-01-01 \
  --end 2024-06-30 --refresh
```

```bash
ASHARE_MINUTE_CACHE_ENABLED=true    # 总开关
ASHARE_MINUTE_CACHE_DIR=            # 留空 = backend/data/minute_cache
```

容量参考：单票单月 1min ≈ 5000 行 ≈ 300KB，100 只票 3 年 ≈ 1GB，比全市场 20GB 低两个量级。

---

## 4. 测试

新增 `backend/tests/test_datasource_optimization_20261115.py`（23 条，全打桩）：

- `to_float` 语义 + 6 个模块 `_f` 行为一致；`code6` / `quote_row` 字段与中文别名；`ProviderBase` / `make_provider` / `build_chain`
- 失败源后置、TTL 过期复位、开关关闭退回链序；`step_timeout` 上下限；**链首慢源仍能让快源补上**
- `month_windows` 首尾裁剪；缓存命中不重复拉；`refresh` 强制重拉；二分救回截断窗口；触底放弃
- 当月不落盘且不命中磁盘；`serve` 在缓存异常时不抛；`bar_minute.fetch` 命中缓存不触碰网关链、开关关闭时回落

同时改动两个既有测试（语义变化，非放宽）：`test_gateway_audit_deadline::test_realtime_whole_request_under_3s`
按新预算语义断言"链首超时 → 第二源超时 → 第三源成功"；`conftest.py` 增加 `_isolate_minute_cache`
（默认关闭并指向 tmp_path，防止落盘状态在测试间互相污染）与 `_HEALTH.reset()` 前后隔离。

---

## 5. 未做（明确取舍）

- **不囤全市场分钟线**：20GB 换来的是"大部分票永远用不到"，按需缓存的性价比高一个量级；需要时用 `--codes` 预热。
- **不做 provider 级别的"按市场热度分级缓存"**：问题②的根因是"每次都从头试"，失败后置 + 单跳预算已解决；
  再叠加一层热/冷分级会造成两套缓存语义（5s 行情缓存 vs 分钟线缓存）互相干扰，收益不抵复杂度。
- **teajoin 到期日 / promax 与 quantdata 额度**仍未确认——这是**风险最大的单项**，不在本次代码范围内，
  需要找卖家问清（`docs/datasource-coverage-compare-20261026.md` 已列）。
