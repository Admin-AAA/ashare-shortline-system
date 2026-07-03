"""
测试：时态一致性验证框架。

基线：旧系统无时态检查
候选：新 temporal_consistency 框架提供自动验证
验证方式：单元测试 + 对现有数据库 recommendations 运行实检
"""

import json
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.services.temporal_consistency import (
    _looks_like_date,
    _extract_date_references,
    _summarize_violations,
    check_run_temporal_consistency,
    check_backtest_temporal_consistency,
)


class TestDateDetection:
    def test_looks_like_date_standard(self):
        assert _looks_like_date("2026-05-22") is True
        assert _looks_like_date("2026-05-22 14:30:00") is True
        assert _looks_like_date("not-a-date") is False
        assert _looks_like_date("") is False
        assert _looks_like_date("2026-13-01") is False  # invalid month

    def test_extract_date_references_dict(self):
        obj = {"entry_date": "2026-05-23", "name": "test"}
        assert "2026-05-23" in _extract_date_references(obj)

    def test_extract_date_references_nested(self):
        obj = {"conditions": {"trade_date": "2026-05-22", "above_vwap": True}}
        assert "2026-05-22" in _extract_date_references(obj)

    def test_extract_date_references_no_dates(self):
        obj = {"score": 85, "name": "test"}
        assert _extract_date_references(obj) == []


class TestSummary:
    def test_summarize_no_violations(self):
        s = _summarize_violations([], 10)
        assert s["total"] == 40  # 10 recs * 4 checks
        assert s["passed"] == 40
        assert s["warnings"] == 0
        assert s["failed"] == 0

    def test_summarize_mixed(self):
        violations = [
            {"severity": "error", "message": "test1"},
            {"severity": "warning", "message": "test2"},
            {"severity": "error", "message": "test3"},
        ]
        s = _summarize_violations(violations, 5)
        assert s["total"] == 20  # 5 recs * 4 checks
        assert s["failed"] == 2
        assert s["warnings"] == 1


class TestCheckRunTemporalConsistency:
    def test_no_data_returns_empty(self):
        from app.db.session import SessionLocal
        db = SessionLocal()
        try:
            result = check_run_temporal_consistency(db, run_id="nonexistent")
            assert result["status"] == "no_data"
        finally:
            db.close()

    def test_existing_recommendations_produce_report(self):
        from app.db.session import SessionLocal
        db = SessionLocal()
        try:
            result = check_run_temporal_consistency(db, run_mode="formal")
            if result["status"] == "no_data":
                pytest.skip("测试环境无 formal 推荐数据")
            assert result["status"] == "completed"
            assert "check_summary" in result
            assert "violations" in result
            assert isinstance(result["run_count"], int)
        finally:
            db.close()


class TestCheckBacktestTemporalConsistency:
    def test_produces_report(self):
        from app.db.session import SessionLocal
        db = SessionLocal()
        try:
            result = check_backtest_temporal_consistency(db)
            assert result["status"] == "completed"
            assert "check_summary" in result
        finally:
            db.close()
