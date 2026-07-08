"""
验证 backtest_service 的 baseline vs candidate 版本化比较功能。

检查项：
1. _build_backtest_walk_forward 的 expanding windows 构建
2. _choose_backtest_variant 的决策逻辑
3. run_backtest_comparison 的完整流程
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.backtest_service import (
    _build_backtest_walk_forward,
    _build_time_split_analysis,
    _load_backtest_trades,
    _choose_backtest_variant,
    run_backtest_comparison,
)


def test_build_walk_forward_eligible_with_multiple_dates():
    """多日期时 walk_forward 应该正确构建 expanding windows。"""
    signal_dates = ["2026-05-18", "2026-05-19", "2026-05-20", "2026-05-21"]
    trades = [
        {"trade_date": "2026-05-18", "code": "000001", "resolved": True,
         "accurate": True, "net_return_pct": 1.2, "benchmark_return_pct": 0.4,
         "gross_return_pct": 1.5, "achievable_return_pct": 2.0,
         "morning_drawdown_pct": -0.5, "holding_days": 5,
         "false_positive_reason": None, "name": "测试A"},
        {"trade_date": "2026-05-19", "code": "000002", "resolved": True,
         "accurate": False, "net_return_pct": -0.5, "benchmark_return_pct": -0.1,
         "gross_return_pct": -0.3, "achievable_return_pct": 0.5,
         "morning_drawdown_pct": -1.0, "holding_days": 5,
         "false_positive_reason": "成本后净收益不为正", "name": "测试B"},
        {"trade_date": "2026-05-20", "code": "000003", "resolved": True,
         "accurate": True, "net_return_pct": 0.8, "benchmark_return_pct": 0.2,
         "gross_return_pct": 1.0, "achievable_return_pct": 1.5,
         "morning_drawdown_pct": -0.3, "holding_days": 5,
         "false_positive_reason": None, "name": "测试C"},
        {"trade_date": "2026-05-21", "code": "000004", "resolved": True,
         "accurate": False, "net_return_pct": -0.1, "benchmark_return_pct": -0.2,
         "gross_return_pct": 0.0, "achievable_return_pct": 0.3,
         "morning_drawdown_pct": -0.1, "holding_days": 5,
         "false_positive_reason": "成本后净收益不为正", "name": "测试D"},
    ]

    result = _build_backtest_walk_forward(signal_dates, trades, min_train_dates=2)

    assert result["eligible"] is True
    assert result["window_count"] == 2  # 4 dates - 2 min = 2 windows
    assert result["test_aggregate"]["trade_count"] == 3
    assert result["notes"][0] == "滚动验证按最少 2 个训练信号日构建 expanding windows。"

    # 验证窗口结构
    assert result["windows"][0]["train_end"] == "2026-05-19"
    assert result["windows"][0]["test_start"] == "2026-05-20"
    assert result["windows"][1]["train_end"] == "2026-05-20"
    assert result["windows"][1]["test_start"] == "2026-05-21"


def test_walk_forward_counts_unexecuted_recommendations_in_accuracy_denominator():
    signal_dates = ["2026-05-18", "2026-05-19", "2026-05-20", "2026-05-21"]
    trades = [
        {"trade_date": "2026-05-18", "code": "000001", "resolved": True,
         "executed": True, "accurate": True, "net_return_pct": 2.0,
         "benchmark_return_pct": 0.2, "gross_return_pct": 2.2,
         "achievable_return_pct": 2.5, "morning_drawdown_pct": -0.2,
         "holding_days": 2, "false_positive_reason": None, "name": "赢家"},
        {"trade_date": "2026-05-19", "code": "000002", "resolved": True,
         "executed": True, "accurate": False, "net_return_pct": -1.0,
         "benchmark_return_pct": 0.1, "gross_return_pct": -0.8,
         "achievable_return_pct": 0.2, "morning_drawdown_pct": -1.2,
         "holding_days": 2, "false_positive_reason": "成本后净收益不为正", "name": "亏损"},
        {"trade_date": "2026-05-20", "code": "000003", "resolved": True,
         "executed": False, "accurate": False, "net_return_pct": 0.0,
         "benchmark_return_pct": 0.0, "gross_return_pct": 1.0,
         "achievable_return_pct": 1.5, "morning_drawdown_pct": 0.0,
         "holding_days": 0, "false_positive_reason": "一字涨停买不进", "name": "未成交"},
        {"trade_date": "2026-05-21", "code": "000004", "resolved": True,
         "executed": False, "accurate": False, "net_return_pct": 0.0,
         "benchmark_return_pct": 0.0, "gross_return_pct": 0.5,
         "achievable_return_pct": 0.8, "morning_drawdown_pct": 0.0,
         "holding_days": 0, "false_positive_reason": "未触发买点", "name": "未触发"},
    ]

    result = _build_backtest_walk_forward(signal_dates, trades, min_train_dates=2)
    aggregate = result["test_aggregate"]

    assert result["eligible"] is True
    assert aggregate["trade_count"] == 3
    assert aggregate["executed_trade_count"] == 1
    assert aggregate["unexecuted_count"] == 2
    assert aggregate["accuracy_numerator"] == 0
    assert aggregate["accuracy_denominator"] == 3
    assert aggregate["recommendation_accuracy"] == 0.0
    assert aggregate["trade_accuracy"] == 0.0
    assert aggregate["accuracy_basis"] == "labeled_recommendations"


def test_time_split_exposes_strict_accuracy_denominator():
    all_dates = ["2026-05-18", "2026-05-19", "2026-05-20", "2026-05-21", "2026-05-22"]
    trades = [
        {"trade_date": "2026-05-18", "code": "000001", "resolved": True,
         "executed": True, "accurate": True, "net_return_pct": 2.0},
        {"trade_date": "2026-05-19", "code": "000002", "resolved": True,
         "executed": True, "accurate": False, "net_return_pct": -1.0,
         "false_positive_reason": "成本后净收益不为正"},
        {"trade_date": "2026-05-20", "code": "000003", "resolved": True,
         "executed": False, "accurate": False, "net_return_pct": 0.0,
         "false_positive_reason": "一字涨停买不进"},
        {"trade_date": "2026-05-21", "code": "000004", "resolved": True,
         "executed": True, "accurate": True, "net_return_pct": 1.0},
        {"trade_date": "2026-05-22", "code": "000005", "resolved": True,
         "executed": False, "accurate": False, "net_return_pct": 0.0,
         "false_positive_reason": "未触发买点"},
    ]

    result = _build_time_split_analysis(trades, all_dates, train_ratio=0.6)
    test_metrics = result["test_metrics"]

    assert result["eligible"] is True
    assert test_metrics["trade_count"] == 2
    assert test_metrics["executed_trade_count"] == 1
    assert test_metrics["unexecuted_count"] == 1
    assert test_metrics["accuracy_numerator"] == 1
    assert test_metrics["accuracy_denominator"] == 2
    assert test_metrics["recommendation_accuracy"] == pytest.approx(0.5, abs=0.001)
    assert test_metrics["trade_accuracy"] == pytest.approx(1.0, abs=0.001)


def test_build_walk_forward_not_eligible_with_few_trades():
    """交易数量不足时返回 not eligible。"""
    result = _build_backtest_walk_forward(
        ["2026-05-18"],
        [{"trade_date": "2026-05-18", "code": "000001", "resolved": True,
          "accurate": True, "net_return_pct": 1.0, "benchmark_return_pct": 0.2,
          "gross_return_pct": 1.2, "achievable_return_pct": 1.5,
          "morning_drawdown_pct": 0, "holding_days": 5,
          "false_positive_reason": None, "name": "测试"}],
        min_train_dates=2,
    )
    assert result["eligible"] is False


def test_load_backtest_trades_filters_to_current_successful_run_ids():
    db = MagicMock()
    result = {
        "results": [
            {"status": "ok", "run_id": "baseline_run"},
            {"status": "skipped", "run_id": "ignored_run"},
            {"status": "ok"},
        ],
    }

    with patch("app.services.backtest_service._build_backtest_metric_records") as mock_records:
        mock_records.return_value = [{"trade_date": "2026-05-18", "code": "000001"}]
        trades = _load_backtest_trades(db, result, "2026-05-18", "2026-05-21")

    assert trades == [{"trade_date": "2026-05-18", "code": "000001"}]
    mock_records.assert_called_once_with(
        db,
        "2026-05-18",
        "2026-05-21",
        run_ids={"baseline_run"},
    )


def test_choose_variant_prefers_walk_forward():
    """walk_forward 测试集结果应优先于全量指标。"""
    baseline_result = {
        "status": "completed",
        "aggregate_metrics": {"trade_count": 10, "recommendation_accuracy": 0.60, "average_net_return": 0.5},
    }
    candidate_result = {
        "status": "completed",
        "aggregate_metrics": {"trade_count": 8, "recommendation_accuracy": 0.50, "average_net_return": 0.3},
    }
    walk_baseline = {
        "eligible": True,
        "test_aggregate": {"trade_count": 5, "recommendation_accuracy": 0.55, "average_net_return": 0.2},
    }
    walk_candidate = {
        "eligible": True,
        "test_aggregate": {"trade_count": 5, "recommendation_accuracy": 0.65, "average_net_return": 0.3, "max_drawdown": -0.1},
    }
    walk_baseline["test_aggregate"]["max_drawdown"] = -0.2
    time_split_baseline = {"eligible": True, "test_metrics": {"trade_count": 6, "recommendation_accuracy": 0.52, "average_net_return": 0.18, "max_drawdown": -0.2}}
    time_split_candidate = {"eligible": True, "test_metrics": {"trade_count": 6, "recommendation_accuracy": 0.61, "average_net_return": 0.19, "max_drawdown": -0.15}}

    selected, reason, decision = _choose_backtest_variant(
        baseline_result, candidate_result, walk_baseline, walk_candidate, time_split_baseline, time_split_candidate,
    )

    assert selected == "candidate"
    assert "滚动样本外验证" in reason
    assert decision["scope"] == "walk_forward"


def test_choose_variant_falls_back_to_full_metrics():
    """walk_forward/time-split 都不足时回退到全量指标。"""
    baseline_result = {
        "status": "completed",
        "aggregate_metrics": {"trade_count": 3, "recommendation_accuracy": 0.40, "average_net_return": 0.05, "max_drawdown": -0.2},
    }
    candidate_result = {
        "status": "completed",
        "aggregate_metrics": {"trade_count": 6, "recommendation_accuracy": 0.70, "average_net_return": 0.70, "max_drawdown": -0.3},
    }
    walk_baseline = {"eligible": False, "test_aggregate": {}, "message": "no data"}
    walk_candidate = {"eligible": False, "test_aggregate": {}, "message": "no data"}
    time_split_baseline = {"eligible": False, "test_metrics": {}}
    time_split_candidate = {"eligible": False, "test_metrics": {}}

    selected, reason, decision = _choose_backtest_variant(
        baseline_result, candidate_result, walk_baseline, walk_candidate, time_split_baseline, time_split_candidate,
    )

    assert selected == "candidate"
    assert "全量指标" in reason
    assert decision["scope"] == "aggregate"


def test_choose_variant_defaults_to_baseline_when_no_clear_winner():
    """无明显差异时默认选 baseline。"""
    baseline_result = {
        "status": "completed",
        "aggregate_metrics": {"trade_count": 10, "recommendation_accuracy": 0.55, "average_net_return": 0.2, "max_drawdown": -0.5},
    }
    candidate_result = {
        "status": "completed",
        "aggregate_metrics": {"trade_count": 10, "recommendation_accuracy": 0.56, "average_net_return": 0.21, "max_drawdown": -0.6},
    }
    walk_baseline = {"eligible": True, "test_aggregate": {"trade_count": 3, "recommendation_accuracy": 0.52, "average_net_return": 0.1, "max_drawdown": -0.4}}
    walk_candidate = {"eligible": True, "test_aggregate": {"trade_count": 3, "recommendation_accuracy": 0.53, "average_net_return": 0.11, "max_drawdown": -0.45}}
    time_split_baseline = {"eligible": False, "test_metrics": {}}
    time_split_candidate = {"eligible": False, "test_metrics": {}}

    selected, reason, _decision = _choose_backtest_variant(
        baseline_result, candidate_result, walk_baseline, walk_candidate, time_split_baseline, time_split_candidate,
    )

    assert selected == "baseline"
    assert "保留基线版" in reason


def test_choose_variant_candidate_not_completed():
    """候选版未完成时选 baseline。"""
    baseline_result = {"status": "completed", "aggregate_metrics": {"trade_count": 5}}
    candidate_result = {"status": "no_data", "aggregate_metrics": {}}
    walk_baseline = {}
    walk_candidate = {}
    time_split_baseline = {}
    time_split_candidate = {}

    selected, reason, _decision = _choose_backtest_variant(
        baseline_result, candidate_result, walk_baseline, walk_candidate, time_split_baseline, time_split_candidate,
    )
    assert selected == "baseline"


def test_choose_variant_falls_back_to_time_split_when_walk_forward_insufficient():
    baseline_result = {
        "status": "completed",
        "aggregate_metrics": {"trade_count": 40, "recommendation_accuracy": 0.48, "average_net_return": 0.15, "max_drawdown": -0.8},
    }
    candidate_result = {
        "status": "completed",
        "aggregate_metrics": {"trade_count": 42, "recommendation_accuracy": 0.50, "average_net_return": 0.18, "max_drawdown": -0.9},
    }
    walk_baseline = {"eligible": True, "test_aggregate": {"trade_count": 4, "recommendation_accuracy": 0.50, "average_net_return": 0.12, "max_drawdown": -0.6}}
    walk_candidate = {"eligible": True, "test_aggregate": {"trade_count": 4, "recommendation_accuracy": 0.70, "average_net_return": 0.18, "max_drawdown": -0.7}}
    time_split_baseline = {"eligible": True, "test_metrics": {"trade_count": 12, "recommendation_accuracy": 0.45, "average_net_return": 0.08, "max_drawdown": -0.5}}
    time_split_candidate = {"eligible": True, "test_metrics": {"trade_count": 12, "recommendation_accuracy": 0.55, "average_net_return": 0.16, "max_drawdown": -0.55}}

    selected, reason, decision = _choose_backtest_variant(
        baseline_result, candidate_result, walk_baseline, walk_candidate, time_split_baseline, time_split_candidate,
    )

    assert selected == "candidate"
    assert "时间切分测试集" in reason
    assert decision["scope"] == "time_split"


def test_choose_variant_blocks_candidate_when_drawdown_deteriorates_too_much():
    baseline_result = {
        "status": "completed",
        "aggregate_metrics": {"trade_count": 50, "recommendation_accuracy": 0.50, "average_net_return": 0.18, "max_drawdown": -1.0},
    }
    candidate_result = {
        "status": "completed",
        "aggregate_metrics": {"trade_count": 50, "recommendation_accuracy": 0.54, "average_net_return": 0.30, "max_drawdown": -4.0},
    }
    walk_baseline = {"eligible": True, "test_aggregate": {"trade_count": 18, "recommendation_accuracy": 0.51, "average_net_return": 0.20, "max_drawdown": -1.2}}
    walk_candidate = {"eligible": True, "test_aggregate": {"trade_count": 18, "recommendation_accuracy": 0.55, "average_net_return": 0.32, "max_drawdown": -4.2}}
    time_split_baseline = {"eligible": True, "test_metrics": {"trade_count": 18, "recommendation_accuracy": 0.50, "average_net_return": 0.18, "max_drawdown": -1.1}}
    time_split_candidate = {"eligible": True, "test_metrics": {"trade_count": 18, "recommendation_accuracy": 0.54, "average_net_return": 0.30, "max_drawdown": -4.1}}

    selected, reason, decision = _choose_backtest_variant(
        baseline_result, candidate_result, walk_baseline, walk_candidate, time_split_baseline, time_split_candidate,
    )

    assert selected == "baseline"
    assert "回撤较基线恶化" in reason
    assert any("回撤护栏" in item for item in decision["guardrail_notes"])


def test_run_backtest_comparison_structure():
    """验证 run_backtest_comparison 的输出结构完整性。"""
    db = MagicMock()

    mock_baseline = {
        "status": "completed",
        "dates_scanned": 10,
        "dates_succeeded": 8,
        "dates_failed": 2,
        "total_recommendations": 42,
        "labeled_count": 40,
        "aggregate_metrics": {"trade_count": 40, "recommendation_accuracy": 0.55, "average_net_return": 0.3},
        "temporal_consistency": {"passed": True, "violation_count": 0},
        "results": [{"trade_date": "2026-05-20", "status": "ok"}],
    }
    mock_candidate = {
        "status": "completed",
        "dates_scanned": 10,
        "dates_succeeded": 9,
        "dates_failed": 1,
        "total_recommendations": 68,
        "labeled_count": 65,
        "aggregate_metrics": {"trade_count": 60, "recommendation_accuracy": 0.52, "average_net_return": 0.25},
        "temporal_consistency": {"passed": True, "violation_count": 0},
        "results": [{"trade_date": "2026-05-20", "status": "ok"}],
    }

    with (
        patch("app.services.backtest_service.run_backtest") as mock_run_backtest,
        patch("app.services.backtest_service._trading_dates_in_range") as mock_dates,
        patch("app.services.backtest_service._load_backtest_trades") as mock_trades,
        patch("app.services.backtest_service._save_backtest_summary") as mock_save,
    ):
        mock_run_backtest.side_effect = [mock_baseline, mock_candidate]
        mock_dates.return_value = ["2026-05-18", "2026-05-19", "2026-05-20", "2026-05-21"]
        mock_trades.return_value = [
            {"trade_date": "2026-05-18", "code": "000001", "resolved": True,
             "accurate": True, "net_return_pct": 1.0, "benchmark_return_pct": 0.2,
             "gross_return_pct": 1.2, "achievable_return_pct": 1.5,
             "morning_drawdown_pct": 0, "holding_days": 5,
             "false_positive_reason": None, "name": "测试"},
        ]

        result = run_backtest_comparison(
            db, "2026-05-18", "2026-05-21",
            baseline_score_line=72.0, candidate_score_line=65.0,
        )

    assert result["status"] == "completed"
    assert "baseline" in result
    assert "candidate" in result
    assert "comparison" in result
    assert result["comparison"]["selected_variant"] in ("baseline", "candidate")
    assert result["baseline"]["score_line"] == 72.0
    assert result["candidate"]["score_line"] == 65.0
    assert result["baseline"]["aggregate_metrics"] is not None
    assert result["candidate"]["aggregate_metrics"] is not None
    assert "walk_forward" in result["baseline"]
    assert "walk_forward" in result["candidate"]
    assert result["comparison"]["walk_forward"]["baseline"]["eligible"] is False
    assert result["comparison"]["time_split"]["candidate"]["eligible"] is False
    assert result["comparison"]["validation_profile"]["primary_scope"] == result["comparison"]["decision_scope"]
    assert result["comparison"]["validation_profile"]["walk_forward"]["baseline"]["trade_count"] >= 0
    assert "aggregate" in result["comparison"]["validation_profile"]

    # 验证调用次数：baseline + candidate
    assert mock_run_backtest.call_count == 2
    mock_save.assert_called_once()
