# 修复指令 · 2026-07-06 端到端测试发现的问题

> 本文档由 WorkBuddy UI Designer 整理，包含今日端到端测试 + 漏斗分析发现的所有问题。
> Claude Code 请按优先级顺序逐个修复，每修完一个跑一次验证。

## 项目背景

- 项目：`~/Desktop/ashare-shortline-system`（A股短线交易驾驶舱）
- 后端：FastAPI `backend/app/`，本地 MySQL `ashare_workbench`
- 前端：`frontend-v2`（Vite + React）
- 测试日期：2026-07-06
- 测试运行：`run_20260706_032435_ddd050`（DSA 单 Skill 实验，experiment 模式）

## 验证环境

- 后端：`launchctl kickstart -k gui/$(id -u)/com.wangao.ashare-backend`，端口 8765
- 前端：`cd frontend-v2 && npx vite --port 5174`
- Python：用 `backend/.venv`
- 数据库：`mysql+pymysql://aowang:1279990918@127.0.0.1:3306/ashare_workbench`

---

## P1 · experiment 模式 + 强势阶段双重惩罚导致永远 0 推荐（设计矛盾）

### 现象
- experiment 模式（DSA 单 Skill 实验）在"强牛·高潮"阶段永远 0 推荐
- 漏斗：5200 标的 → prefilter 586 → 深审 14 → 推荐 0
- 14 只深审标的最终分 37-39，但有效分数线 105.55，差距 66 分

### 根因（三段代码叠加）

**1. experiment 模式自动跳过辅助数据** — `backend/app/services/workbench_service.py:3526-3530`
```python
skip_aux_refresh = bool(
    payload.get("skip_aux_refresh")
    if "skip_aux_refresh" in payload
    else run_mode == "experiment"   # ← experiment 默认跳过
)
if run_mode == "formal":
    skip_aux_refresh = False
```
→ 辅助数据（概念板块/龙虎榜/资金流/新闻）全部 `skipped_fast_run`
→ evidence_quality 45.83（< 55）+ Agent 没有辅助数据可参考 → skill_agent 只拿 30 分

**2. 强势阶段门槛上调** — `backend/app/engine/aggregator.py:922-939`
```python
if _snapshot_regime in ("结构性强势", "强势") and card.get("support_ma5"):
    effective_score_line = round(effective_score_line + 6.0, 2)   # +6
if _snapshot_regime == "结构性强势":
    effective_score_line = round(effective_score_line + 4.0, 2)    # +4
elif _snapshot_regime == "强势":
    effective_score_line = round(effective_score_line + 8.0, 2)    # +8
```
→ 配置 72 + 强势 +8 + support_ma5 +6 + evidence_adjustment = 105.55

**3. 强牛·高潮禁用动态降线** — `backend/app/engine/aggregator.py:965-968`
```python
_disable_soft_floor = _ctx_sentiment == "高潮" or _ctx_base == "强牛"
if not result and ordered and top_final_score > 0 and not _disable_soft_floor:
    # 动态降线，避免零推荐
```
→ 强牛·高潮阶段即使无候选也不降线

### 设计意图（不要破坏）
- 强势阶段门槛上调 + 禁用降线是 **formal 模式的风控设计**，有回测验证：
  - 强势 WR=14.3%（21笔,avg=-2.23%），追高必亏
  - 代码注释："高潮阶段宁可零推荐也不降线，避免追高陷阱"
- **这个风控对 formal 模式必须保留**

### 修复方案
**让 experiment 模式豁免强势阶段门槛上调**（风控只对 formal 模式生效）。

在 `aggregator.py` 的门槛上调逻辑（922-939 行）外层加 run_mode 判断。需要把 run_mode 传入 aggregator 的上下文。

具体改法：
1. 在 `aggregate()` 函数签名或 `score_line_context` 里加入 `run_mode` 字段
2. 门槛上调代码改为：
```python
_run_mode = score_line_context.get("run_mode") or "formal"
if _run_mode != "experiment":  # experiment 模式豁免风控门槛
    if _snapshot_regime in ("结构性强势", "强势") and card.get("support_ma5"):
        effective_score_line = round(effective_score_line + 6.0, 2)
        card["_regime_support_ma5_penalty"] = 6.0
    if _snapshot_regime == "结构性强势":
        effective_score_line = round(effective_score_line + 4.0, 2)
        card["_regime_strong_penalty"] = 4.0
    elif _snapshot_regime == "强势":
        effective_score_line = round(effective_score_line + 8.0, 2)
        card["_regime_strong_penalty"] = 8.0
```
3. 动态降线（965-968 行）同理，experiment 模式不禁用：
```python
_disable_soft_floor = (_ctx_sentiment == "高潮" or _ctx_base == "强牛") and _run_mode != "experiment"
```
4. 在调用 `aggregate()` 的上游（`workbench_service.py`）把 `run_mode` 传入 score_line_context

### 验证方法
```bash
# 1. 触发 experiment 运行
curl -X POST http://127.0.0.1:8765/api/runs \
  -H "Content-Type: application/json" \
  -d '{"preset_id":"preset_experiment_dsa","wait_for_completion":true,"async":false}'

# 2. 检查推荐数 > 0（之前是 0）
# 3. 检查 effective_score_line 应该接近 72（不是 105.55）
curl http://127.0.0.1:8765/api/runs/{run_id}/funnel | python3 -m json.tool | grep effective_score_line
```

---

## P2 · health 接口 api_error_rate 统计误导

### 现象
- `/api/admin/health` 返回 `api_error_rate_7d: 0.152`（15.2%）
- 实际 HTTP 端点错误率只有 0.7%（access.log 统计）
- 15.2% 是 LLM 调用错误（jucode 路由 token 失效 93 次 + deepseek 余额不足 2 次）

### 根因 — `backend/app/services/cost_service.py:129-147`
```python
week_logs = db.scalars(
    select(ApiCallLog).where(ApiCallLog.created_at >= week_ago)
).all()
week_calls = len(week_logs)            # 651（全是 LLM 调用）
week_errors = sum(1 for log in week_logs if log.status != "success")  # 95
# ...
"api_calls_7d": week_calls,            # 字段名误导：叫 api_calls 实际是 llm_calls
"api_errors_7d": week_errors,
"api_error_rate_7d": round(week_errors / max(1, week_calls), 3),
```

`ApiCallLog` 表只记录 LLM 调用（deepseek/jucode），不记录 HTTP 端点请求。字段名 `api_calls_7d` 让人以为是 HTTP 端点错误率。

### 修复方案
**拆分为两个字段，区分 LLM 调用错误和 HTTP 端点错误。**

在 `cost_service.py:system_health()` 里：
1. 保留现有 LLM 调用统计，但重命名字段：
```python
"llm_api_calls_7d": week_calls,
"llm_api_errors_7d": week_errors,
"llm_api_error_rate_7d": round(week_errors / max(1, week_calls), 3),
```
2. 新增 HTTP 端点错误统计（从 access.log 解析或新增 HttpAccessLog 表）。
   如果不想新增表，至少把字段名改清楚，并在响应里加 `note` 说明：
```python
"llm_api_calls_7d": week_calls,
"llm_api_errors_7d": week_errors,
"llm_api_error_rate_7d": round(week_errors / max(1, week_calls), 3),
"note": "错误率统计仅含 LLM 调用（ApiCallLog），不含 HTTP 端点请求",
```
3. 前端 `frontend-v2` 里 health 显示面板同步更新字段名（搜索 `api_error_rate_7d` / `api_errors_7d` 改为 `llm_api_error_rate_7d` / `llm_api_errors_7d`）

### 验证方法
```bash
curl http://127.0.0.1:8765/api/admin/health | python3 -m json.tool
# 确认字段名变为 llm_api_error_rate_7d，且有 note 说明
```

---

## P3 · fina_indicator 数据层 5 个问题

### P3a · `fetch_fina_indicator_batch` 的 limit 是死参数
**位置**：`backend/app/services/tushare_client.py:1577-1600`
```python
def fetch_fina_indicator_batch(trade_date=None, limit=4):
    # ...
    df = pro.fina_indicator(ann_date=target)   # ← limit 根本没传
```
**修复**：去掉 limit 参数（Tushare `fina_indicator(ann_date=X)` 返回该日公告全部记录，limit 语义接不上），或在注释里说明 limit 未使用。

### P3b · `fetch_fina_indicator` 单股 trade_date 过滤顺序有偏差
**位置**：`backend/app/services/tushare_client.py:1526-1575`
```python
df = pro.fina_indicator(ts_code=tsc, limit=limit)   # 先拉最新 limit 期
if target:
    df = df[df["ann_date"].astype(str) <= target]   # 再过滤
```
回测场景下，如果 target 是半年前，过滤后可能只剩 0 行（因为最新 4 期都在 target 之后）。
**修复**：用 Tushare 的 `period` 参数按期倒推拉取，或先过滤再 limit。建议：
```python
if target:
    # 用 period 倒推：target 往前找最近 4 个财报期末
    target_date = datetime.strptime(target, "%Y%m%d")
    periods = _recent_report_periods(target_date, limit)
    rows = []
    for p in periods:
        df = pro.fina_indicator(ts_code=tsc, period=p)
        rows.append(df)
    df = pd.concat(rows) if rows else pd.DataFrame()
else:
    df = pro.fina_indicator(ts_code=tsc, limit=limit)
```

### P3c · 缺关键字段
**位置**：`tushare_client.py` 的 `fetch_fina_indicator` + `tables.py` 的 `FinaIndicator` 模型 + `market_pool.py:2327-2340` 的字段转换
**当前字段**：roe, grossprofit_margin, netprofit_margin, netprofit_yoy, or_yoy, eps, bps
**需补字段**：
| 字段 | 用途 |
|------|------|
| `rd_exp` | 研发费用 → 算研发费用率 → 判断"高毛利是否有技术壁垒" |
| `q_profit_yoy` | 单季归母净利同比 → 识别业绩拐点（累计同比会掩盖单季滑坡） |
| `update_flag` | 财报更新标志（1=最新）→ 避免拉到旧版快报 |
| `debt_to_assets` | 资产负债率 → 小盘成长股风控 |

**修复**：
1. `tushare_client.py:fetch_fina_indicator` 的 fields 列表加上这 4 个字段
2. `tables.py:FinaIndicator` 模型加 4 个 Column
3. `market_pool.py:_load_fina_indicator_for_stocks` 的返回字典加字段映射
4. 拉取时 `fetch_fina_indicator` 显式传 `update_flag=1`（只拉最新版财报）

### P3d · `feature_builder.py:932` pe_ttm 死代码
**位置**：`backend/app/services/feature_builder.py:932`
```python
profile["pe_ttm"] = fina.get("pe_ttm")   # fina_indicator 没有 pe_ttm 字段，永远是 None
```
pe_ttm 来自 `daily_basic`，已由 `fetch_daily_kbars` 注入 `item["pe_ttm"]`，不需要 fina 重复提供。
**修复**：删除这行。

### P3e · `has_real_fundamental` 判定阈值偏低
**位置**：`backend/app/services/feature_builder.py:938-941`
```python
has_real_fina = any(
    fina.get(k) is not None
    for k in ("revenue_yoy", "net_profit_yoy", "roe", "gross_margin")
)
```
只要 4 个里任意一个非 None 就算 True。但白毛策略硬筛 4 个全要，只拉到 roe 其他都 None 时，evaluator 把 None 当 0 处理，硬筛全失败。
**修复**：这个判定本身保留（给其他策略用），但在白毛策略的 `strategy.yaml` universe 第一条加守门：
```yaml
universe:
  include:
    - "has_real_fundamental == true"   # 数据完整守门
```
同时确认 `has_real_fundamental` 在 `evaluator.py` 的 `ALLOWED_NAMES` 白名单内（如果不在，加一行注册）。

### 验证方法
```bash
# P3a/b: 单元测试
cd backend && .venv/bin/python -c "
from app.services.tushare_client import fetch_fina_indicator
r = fetch_fina_indicator('002876', limit=4)
print(f'rows: {len(r)}')
print(f'fields: {list(r[0].keys()) if r else None}')
# 确认含 rd_exp / q_profit_yoy / update_flag / debt_to_assets
"

# P3d: 确认 pe_ttm 死代码已删
grep -n 'pe_ttm.*fina.get' backend/app/services/feature_builder.py
# 应该无输出

# P3e: 确认白毛策略 universe 有守门（如果白毛策略已创建）
grep -A 3 'universe:' backend/skills/bottleneck-hunter-a-share/strategy.yaml 2>/dev/null
```

---

## 修复顺序建议

1. **P3d**（删 pe_ttm 死代码）— 最简单，1 行改动
2. **P3a**（fetch_fina_indicator_batch limit 死参数）— 简单
3. **P2**（health 接口字段重命名）— 中等，影响前后端
4. **P3c**（补 fina 字段）— 中等，3 个文件联动
5. **P3b**（fetch_fina_indicator 过滤顺序）— 较复杂
6. **P3e**（白毛策略 universe 守门）— 等白毛策略创建后再做
7. **P1**（experiment 模式豁免强势门槛）— 最复杂，涉及 aggregator 上下文传递，需谨慎不破坏 formal 风控

## 注意事项

- **不要破坏 formal 模式的风控**：P1 的修复只让 experiment 模式豁免，formal 模式的强势阶段门槛 + 禁用降线必须原样保留
- **不要清库**：CLAUDE.md 明确禁止清库/删表/重建等不可恢复操作
- **用 .venv**：Python 测试用 `backend/.venv`
- **跑构建验证**：每修完一个 P，跑 `cd backend && .venv/bin/python -c "from app.main import app"` 确认能导入
- **提交粒度**：每个 P 单独一个 commit，commit message 用 `fix: P1-experiment-...` 格式
