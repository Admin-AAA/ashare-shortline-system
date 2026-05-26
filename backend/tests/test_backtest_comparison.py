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
        "test_aggregate": {"trade_count": 5, "recommendation_accuracy": 0.65, "average_net_return": 0.3},
    }

    selected, reason = _choose_backtest_variant(
        baseline_result, candidate_result, walk_baseline, walk_candidate,
    )

    assert selected == "candidate"
    assert "滚动样本外验证" in reason


def test_choose_variant_falls_back_to_full_metrics():
    """walk_forward 不可用时回退到全量指标。"""
    baseline_result = {
        "status": "completed",
        "aggregate_metrics": {"trade_count": 3, "recommendation_accuracy": 0.40, "average_net_return": 0.05},
    }
    candidate_result = {
        "status": "completed",
        "aggregate_metrics": {"trade_count": 3, "recommendation_accuracy": 0.70, "average_net_return": 0.12},
    }
    walk_baseline = {"eligible": False, "test_aggregate": {}, "message": "no data"}
    walk_candidate = {"eligible": False, "test_aggregate": {}, "message": "no data"}

    selected, reason = _choose_backtest_variant(
        baseline_result, candidate_result, walk_baseline, walk_candidate,
    )

    assert selected == "candidate"
    assert "全量指标" in reason


def test_choose_variant_defaults_to_baseline_when_no_clear_winner():
    """无明显差异时默认选 baseline。"""
    baseline_result = {
        "status": "completed",
        "aggregate_metrics": {"trade_count": 10, "recommendation_accuracy": 0.55, "average_net_return": 0.2},
    }
    candidate_result = {
        "status": "completed",
        "aggregate_metrics": {"trade_count": 10, "recommendation_accuracy": 0.56, "average_net_return": 0.21},
    }
    walk_baseline = {"eligible": True, "test_aggregate": {"trade_count": 3, "recommendation_accuracy": 0.52}}
    walk_candidate = {"eligible": True, "test_aggregate": {"trade_count": 3, "recommendation_accuracy": 0.53}}

    selected, reason = _choose_backtest_variant(
        baseline_result, candidate_result, walk_baseline, walk_candidate,
    )

    assert selected == "baseline"
    assert "差异不显著" in reason


def test_choose_variant_candidate_not_completed():
    """候选版未完成时选 baseline。"""
    baseline_result = {"status": "completed", "aggregate_metrics": {"trade_count": 5}}
    candidate_result = {"status": "no_data", "aggregate_metrics": {}}
    walk_baseline = {}
    walk_candidate = {}

    selected, reason = _choose_backtest_variant(
        baseline_result, candidate_result, walk_baseline, walk_candidate,
    )
    assert selected == "baseline"


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

    # 验证调用次数：baseline + candidate
    assert mock_run_backtest.call_count == 2
    mock_save.assert_called_once()
