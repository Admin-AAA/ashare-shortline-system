"""backtest/result.py —— trades → 展示用样本记录 的收敛点（需求 9.1 / S8.1）。

## 审计结论（S8.1）

`backtest_service` / `overnight_holding_service` / `stock_backtest_service` 三个
"回测家族成员"里，"trade 记录构造"（从原始数据源 → 标准化 trade dict）与
"trades → 展示用样本记录"是两类不同性质的逻辑，处理方式不同：

1. **trade 记录构造本身 —— 本质不同，不强行收敛**：
   - `backtest_service._build_backtest_metric_records` 从数据库
     `RecommendationOutcome`/`Recommendation` 表查询后构造 trade dict。
   - `overnight_holding_service._simulate_variant` 从分钟线模拟直接产出 trade dict
     （字段含 `conditions`/`entry_time`/`exit_time` 等，构造方式是回放行情，不查
     outcome 表）。
   - `stock_backtest_service._simulate_trade` 又是另一套：从单只股票的日 K 线止损/
     止盈模拟产出 trade dict（字段是 `net_return`/`max_return`/`max_drawdown`，命名
     与前两者的 `net_return_pct` 体系都不同）。
   这三者的"构造"步骤是完全不同的数据来源与计算路径，强行抽出一个公共构造函数只会
   引入无意义的参数化分支（违反 design.md §10/§15 "避免过度抽象"的克制态度），
   本次不收敛。

2. **trades → 展示用样本记录 —— 真正重复，收敛到本模块**：
   `backtest_service._backtest_recommendation_records`/`_backtest_false_positive_samples`
   与 `overnight_holding_service._recommendation_records`/`_false_positive_samples`
   在"trades 已经是 `list[dict]` 之后"这一层做的是同一件事：
   - 误判样本筛选：`resolved` 且 `not accurate` → 按 `net_return_pct` 升序 →
     截取前 N 条——两处筛选+排序逻辑逐字节相同，收敛为 `select_false_positive_trades`。
   - 核心字段投影：`trade_date`/`code`/`name`/入场出场时间价格/收益率/
     `exit_reason`/`false_positive_reason` 这组核心字段两个家族都在投影，只是各自
     还挂了一些私有字段（`backtest_service` 挂 `accuracy_failure_label` 等信号
     有效性字段；`overnight_holding_service` 挂 `conditions` 触发条件），收敛为
     `project_recommendation_record`/`project_false_positive_sample`，通过
     `entry_time_key`/`exit_time_key`（字段名差异）与 `extra_fields`（家族私有字段）
     两个参数吸收差异，不强行同构双方的私有字段。

3. **`stock_backtest_service._compute_skill_performance` —— 不接入本模块**：
   它产出的 `trades` 字段命名（`net_return`/`max_return`/`max_drawdown`，无
   `code`/`name`/`accurate`/`false_positive_reason`）与本模块消费的 trade 字段
   （`net_return_pct` 体系）不同，聚合目标是"胜率/盈亏比/profit_factor"
   （per-skill 表现），不产出 `recommendation_records`/`false_positive_samples`
   展示格式，也不喂 `compute_full_metrics`（9-KPI 体系）。它的"trade 构造 + 聚合"
   是自成一体的评估路径，不属于需求 9.1 字面意思要求收敛的"trade/指标/样本成形"，
   本次不改动。

指标计算本身（`compute_full_metrics`）已在 S6.2 统一，不是本模块的职责。
"""

from __future__ import annotations

from typing import Any


def select_false_positive_trades(
    trades: list[dict[str, Any]],
    limit: int = 8,
) -> list[dict[str, Any]]:
    """从已成形的 trades 里筛出"已解决但判定不准确"的误判样本。

    筛选条件：`resolved` 为真且 `accurate` 为假；按 `net_return_pct` 升序排列
    （净收益最差的排最前面），截取前 `limit` 条。

    这是 `backtest_service._backtest_false_positive_samples` 与
    `overnight_holding_service._false_positive_samples` 共享的筛选+排序逻辑
    （原两处实现逐字节相同）。返回的是原始 trade dict（未投影为展示字段），
    调用方负责按各自家族的展示字段集调用 `project_false_positive_sample` 投影。
    """
    losers = [item for item in trades if item.get("resolved") and not item.get("accurate")]
    losers.sort(key=lambda item: float(item.get("net_return_pct") or 0.0))
    return losers[:limit]


def project_recommendation_record(
    trade: dict[str, Any],
    *,
    recommendation_time_key: str = "trade_date",
    entry_time_key: str = "entry_time",
    exit_time_key: str = "exit_time",
    extra_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把单条 trade dict 投影为前端展示用的 recommendation record 核心字段。

    核心字段是两个家族共同投影的部分。三个 `*_key` 参数吸收字段命名差异：
    - `recommendation_time_key`：`backtest_service` 的 trade dict 没有独立的
      `recommendation_time` 字段，用 `trade_date` 顶替（默认值）；
      `overnight_holding_service` 的 trade dict 自带独立的 `recommendation_time`
      字段（来自分钟线信号时间），需传 `"recommendation_time"`。
    - `entry_time_key`/`exit_time_key`：`backtest_service` 用 `entry_date`/
      `exit_date`，`overnight_holding_service` 用 `entry_time`/`exit_time`。
    `extra_fields` 吸收各家族私有的展示字段（不强行同构）。

    是否排序/截取由调用方负责——两个家族原实现在这一层的排序行为不同
    （`backtest_service` 无条件排序，`overnight_holding_service` 的主列表不排序，
    只有 `_trade_samples` 会排序+截取），本函数只做单条投影，不改变这一行为。
    """
    record: dict[str, Any] = {
        "trade_date": trade.get("trade_date"),
        "code": trade.get("code"),
        "name": trade.get("name"),
        "recommendation_time": trade.get(recommendation_time_key),
        "entry_time": trade.get(entry_time_key),
        "entry_price": trade.get("entry_price"),
        "exit_date": trade.get("exit_date"),
        "exit_time": trade.get(exit_time_key),
        "exit_price": trade.get("exit_price"),
        "net_return_pct": trade.get("net_return_pct"),
        "gross_return_pct": trade.get("gross_return_pct"),
        "benchmark_return_pct": trade.get("benchmark_return_pct"),
        "accurate": trade.get("accurate"),
        "exit_reason": trade.get("exit_reason"),
        "false_positive_reason": trade.get("false_positive_reason"),
    }
    if extra_fields:
        record.update(extra_fields)
    return record


def project_false_positive_sample(
    trade: dict[str, Any],
    *,
    entry_time_key: str = "entry_time",
    exit_time_key: str = "exit_time",
    extra_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把单条误判 trade dict 投影为前端展示用的 false-positive sample 核心字段。

    参数语义与 `project_recommendation_record` 一致。
    """
    sample: dict[str, Any] = {
        "trade_date": trade.get("trade_date"),
        "code": trade.get("code"),
        "name": trade.get("name"),
        "entry_time": trade.get(entry_time_key),
        "exit_time": trade.get(exit_time_key),
        "net_return_pct": trade.get("net_return_pct"),
        "false_positive_reason": trade.get("false_positive_reason"),
    }
    if extra_fields:
        sample.update(extra_fields)
    return sample
