"""
验证 backtest_service 的自动时态一致性检查集成。

检查项：
1. _run_backtest_temporal_check 在无回测运行记录时返回 skipped
2. 有运行记录时的检查结果结构完整性
3. 时态违规时的错误计数正确
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.services.backtest_service import _run_backtest_temporal_check


def test_temporal_check_skipped_when_no_run_ids():
    """空结果列表时返回 skipped。"""
    db = MagicMock()
    result = _run_backtest_temporal_check(db, [])
    assert result["status"] == "skipped"
    assert "无可检查" in result["message"]


def test_temporal_check_delegates_to_check_functions():
    """验证委托给 check_run_temporal_consistency 和 check_backtest_temporal_consistency。"""
    db = MagicMock()
    results = [
        {"trade_date": "2026-05-20", "status": "ok", "run_id": "bt_001"},
    ]

    with (
        patch("app.services.backtest_service.check_run_temporal_consistency") as mock_run_check,
        patch("app.services.backtest_service.check_backtest_temporal_consistency") as mock_bt_check,
    ):
        mock_run_check.return_value = {
            "status": "completed",
            "violations": [
                {
                    "check": "daily_kbar_exists",
                    "severity": "error",
                    "rec_id": "rec_1",
                    "code": "000001",
                    "trade_date": "2026-05-20",
                    "message": "推荐 000001 在 2026-05-20 无对应日K线数据",
                }
            ],
        }
        mock_bt_check.return_value = {
            "status": "completed",
            "violations": [],
        }

        result = _run_backtest_temporal_check(db, results)

    assert result["status"] == "completed"
    assert result["violation_count"] == 1
    assert result["error_count"] == 1
    assert result["warning_count"] == 0
    assert result["passed"] is False
    assert result["summary"]["errors"] == 1

    # 验证委托调用
    mock_run_check.assert_called_once_with(db, run_id="bt_001")
    mock_bt_check.assert_called_once()


def test_temporal_check_passes_with_no_violations():
    """无违规时的通过状态。"""
    db = MagicMock()
    results = [
        {"trade_date": "2026-05-20", "status": "ok", "run_id": "bt_001"},
    ]

    with (
        patch("app.services.backtest_service.check_run_temporal_consistency") as mock_run_check,
        patch("app.services.backtest_service.check_backtest_temporal_consistency") as mock_bt_check,
    ):
        mock_run_check.return_value = {
            "status": "completed",
            "violations": [],
        }
        mock_bt_check.return_value = {
            "status": "completed",
            "violations": [],
        }

        result = _run_backtest_temporal_check(db, results)

    assert result["status"] == "completed"
    assert result["violation_count"] == 0
    assert result["error_count"] == 0
    assert result["passed"] is True


def test_temporal_check_counts_warnings_separately():
    """警告和错误分开计数。"""
    db = MagicMock()
    results = [
        {"trade_date": "2026-05-20", "status": "ok", "run_id": "bt_001"},
    ]

    with (
        patch("app.services.backtest_service.check_run_temporal_consistency") as mock_run_check,
        patch("app.services.backtest_service.check_backtest_temporal_consistency") as mock_bt_check,
    ):
        mock_run_check.return_value = {
            "status": "completed",
            "violations": [
                {
                    "check": "outcome_exit_same_as_trade",
                    "severity": "warning",
                    "rec_id": "rec_2",
                    "message": "出场日期与推荐日期相同",
                },
                {
                    "check": "daily_kbar_exists",
                    "severity": "error",
                    "rec_id": "rec_3",
                    "message": "推荐无对应日K线",
                },
            ],
        }
        mock_bt_check.return_value = {
            "status": "completed",
            "violations": [],
        }

        result = _run_backtest_temporal_check(db, results)

    assert result["violation_count"] == 2
    assert result["error_count"] == 1
    assert result["warning_count"] == 1
    assert result["passed"] is False
    assert result["summary"]["errors"] == 1
    assert result["summary"]["warnings"] == 1
