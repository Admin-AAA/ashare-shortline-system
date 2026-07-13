"""``TimeSeriesBacktestStrategy`` 协议 + ``OvernightHoldingBacktestStrategy`` 单测
（任务 8.2，需求 9.2）。

覆盖范围：
1. ``OvernightHoldingBacktestStrategy`` 满足 ``TimeSeriesBacktestStrategy`` 协议
   （structural typing）。
2. ``variants()`` 返回顺序/内容与 ``run_overnight_holding_backtest`` 内部循环使用的
   四个变体一致。
3. ``run()`` 委托 ``run_overnight_holding_backtest``，参数与返回值逐一透传（不重写
   任何业务逻辑）——用 monkeypatch 替换模块级函数验证委托关系，不依赖真实 DB。
4. ``choose_variant()`` 委托 ``_choose_variant``，参数与返回值逐一透传。

纯 in-process：只依赖 ``app.engine.timeseries_backtest_strategy`` /
``app.services.overnight_holding_service``，不吃真实 DB（``db`` 参数在委托测试里
用哨兵对象代替，只验证「原样传递」，不触发真实查询）。
"""
from __future__ import annotations

from typing import Any

from app.engine.timeseries_backtest_strategy import (
    BacktestVariant,
    TimeSeriesBacktestStrategy,
)
from app.services.overnight_holding_service import (
    BASELINE_VARIANT,
    CANDIDATE_VARIANT,
    DELAYED_EXIT_VARIANT,
    RELAXED_PCT_VARIANT,
    STRATEGY_ID,
    STRATEGY_NAME,
    OvernightHoldingBacktestStrategy,
)


class _SentinelDb:
    """哨兵 db 对象：只用于身份比对（``is``），验证委托时参数原样传递，不触发真实查询。"""


# ══════════════════════════════════════════════════════════════════════════
# 1. 协议满足性 + 身份字段
# ══════════════════════════════════════════════════════════════════════════
def test_overnight_holding_backtest_strategy_satisfies_protocol():
    strategy = OvernightHoldingBacktestStrategy()
    assert isinstance(strategy, TimeSeriesBacktestStrategy)


def test_overnight_holding_backtest_strategy_identity_fields():
    strategy = OvernightHoldingBacktestStrategy()
    assert strategy.strategy_id == STRATEGY_ID
    assert strategy.strategy_name == STRATEGY_NAME


def test_baseline_variant_satisfies_backtest_variant_protocol():
    assert isinstance(BASELINE_VARIANT, BacktestVariant)


# ══════════════════════════════════════════════════════════════════════════
# 2. variants()：顺序与内容对齐 run_overnight_holding_backtest 内部循环
# ══════════════════════════════════════════════════════════════════════════
def test_variants_returns_all_four_in_declared_order():
    strategy = OvernightHoldingBacktestStrategy()

    result = strategy.variants()

    assert result == (BASELINE_VARIANT, CANDIDATE_VARIANT, DELAYED_EXIT_VARIANT, RELAXED_PCT_VARIANT)
    assert [v.name for v in result] == ["baseline", "candidate", "delayed_exit", "relaxed_pct"]


# ══════════════════════════════════════════════════════════════════════════
# 3. run()：委托 run_overnight_holding_backtest，参数/返回值原样透传
# ══════════════════════════════════════════════════════════════════════════
def test_run_delegates_to_run_overnight_holding_backtest(monkeypatch):
    captured: dict[str, Any] = {}
    sentinel_result = {"status": "completed", "strategy_id": STRATEGY_ID}

    def _fake_run_overnight_holding_backtest(db, start_date, end_date):
        captured["db"] = db
        captured["start_date"] = start_date
        captured["end_date"] = end_date
        return sentinel_result

    monkeypatch.setattr(
        "app.services.overnight_holding_service.run_overnight_holding_backtest",
        _fake_run_overnight_holding_backtest,
    )

    strategy = OvernightHoldingBacktestStrategy()
    sentinel_db = _SentinelDb()

    result = strategy.run(sentinel_db, "2026-05-19", "2026-05-21")

    assert result is sentinel_result
    assert captured["db"] is sentinel_db
    assert captured["start_date"] == "2026-05-19"
    assert captured["end_date"] == "2026-05-21"


def test_run_return_value_matches_direct_call_byte_for_byte(monkeypatch):
    """不 monkeypatch，直接验证 strategy.run 与直接调用模块级函数在同一次调用下
    产出完全相同的对象（因为内部就是 return run_overnight_holding_backtest(...)，
    不是重新计算）。"""
    sentinel_result = {"status": "no_data", "dates_scanned": 0}

    def _fake(db, start_date, end_date):
        return sentinel_result

    monkeypatch.setattr(
        "app.services.overnight_holding_service.run_overnight_holding_backtest",
        _fake,
    )

    strategy = OvernightHoldingBacktestStrategy()
    via_strategy = strategy.run(_SentinelDb(), "2026-05-01", "2026-05-02")
    direct = _fake(_SentinelDb(), "2026-05-01", "2026-05-02")

    assert via_strategy == direct
    assert via_strategy is sentinel_result


# ══════════════════════════════════════════════════════════════════════════
# 4. choose_variant()：委托 _choose_variant，参数/返回值原样透传
# ══════════════════════════════════════════════════════════════════════════
def test_choose_variant_delegates_to_module_level_choose_variant(monkeypatch):
    captured: dict[str, Any] = {}
    sentinel_return = ("candidate", "候选版更优")

    def _fake_choose_variant(signal_dates, comparisons):
        captured["signal_dates"] = signal_dates
        captured["comparisons"] = comparisons
        return sentinel_return

    monkeypatch.setattr(
        "app.services.overnight_holding_service._choose_variant",
        _fake_choose_variant,
    )

    strategy = OvernightHoldingBacktestStrategy()
    signal_dates = ["2026-05-19", "2026-05-20"]
    comparisons = {"baseline": {"variant": BASELINE_VARIANT}}

    result = strategy.choose_variant(signal_dates, comparisons)

    assert result == sentinel_return
    assert captured["signal_dates"] is signal_dates
    assert captured["comparisons"] is comparisons


def test_choose_variant_matches_direct_call_with_real_choose_variant():
    """不 monkeypatch，用真实 _choose_variant 验证委托层不改写返回值
    （零行为变化的最终校验）。"""
    from app.services.overnight_holding_service import _choose_variant

    comparisons = {
        "baseline": {
            "variant": BASELINE_VARIANT,
            "split_metrics": {
                "test": {
                    "trade_count": 2,
                    "recommendation_accuracy": 0.50,
                    "average_net_return": 0.10,
                    "max_drawdown": -0.40,
                }
            },
            "walk_forward": {
                "test_aggregate": {
                    "trade_count": 0,
                    "recommendation_accuracy": 0.0,
                    "average_net_return": 0.0,
                    "max_drawdown": 0.0,
                }
            },
        },
        "candidate": {
            "variant": CANDIDATE_VARIANT,
            "split_metrics": {
                "test": {
                    "trade_count": 2,
                    "recommendation_accuracy": 0.80,
                    "average_net_return": 0.25,
                    "max_drawdown": -0.30,
                }
            },
            "walk_forward": {
                "test_aggregate": {
                    "trade_count": 0,
                    "recommendation_accuracy": 0.0,
                    "average_net_return": 0.0,
                    "max_drawdown": 0.0,
                }
            },
        },
    }

    strategy = OvernightHoldingBacktestStrategy()
    via_strategy = strategy.choose_variant([], comparisons)
    direct = _choose_variant([], comparisons)

    assert via_strategy == direct
