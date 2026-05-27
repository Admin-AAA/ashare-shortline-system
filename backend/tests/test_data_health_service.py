from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.data_health_service import (
    _TABLE_DEFS,
    _compute_health_score,
    _find_issues,
    _recent_trade_day_coverage,
    _resolve_reference_trade_date,
)


def test_resolve_reference_trade_date_falls_back_to_local_when_tushare_unavailable():
    db = MagicMock()
    with patch("app.services.data_health_service._get_tushare_latest_trade_date", return_value=None), patch(
        "app.services.data_health_service._infer_local_reference_trade_date",
        return_value="2026-05-26",
    ):
        result = _resolve_reference_trade_date(db)

    assert result["reference_trade_date"] == "2026-05-26"
    assert result["external_trade_date"] is None
    assert result["source"] == "local"
    assert "回退" in result["note"]


def test_recent_trade_day_coverage_reports_missing_dates():
    db = MagicMock()
    day_rows = MagicMock()
    day_rows.all.return_value = ["2026-05-21", "2026-05-22", "2026-05-23", "2026-05-26", "2026-05-27"]
    present_rows = MagicMock()
    present_rows.all.return_value = ["2026-05-21", "2026-05-23", "2026-05-27"]
    date_table_count = sum(1 for item in _TABLE_DEFS if item["date_col"])
    db.scalars.side_effect = [day_rows] + [present_rows] * date_table_count

    result = _recent_trade_day_coverage(db, "2026-05-27", window=5)

    assert result["trade_days"] == ["2026-05-21", "2026-05-22", "2026-05-23", "2026-05-26", "2026-05-27"]
    daily = result["tables"]["daily_kbar"]
    assert daily["covered_count"] == 3
    assert daily["missing_dates"] == ["2026-05-22", "2026-05-26"]
    assert daily["coverage_rate"] == 0.6
    assert "daily_kbar" in result["summary"]["warning_tables"]


def test_compute_health_score_penalizes_recent_critical_gaps():
    table_stats = {
        "daily_kbar": {"critical": True, "total_rows": 100},
        "recommendation": {"critical": True, "total_rows": 10},
        "outcome": {"critical": True, "total_rows": 8},
    }
    recent_coverage = {
        "tables": {
            "daily_kbar": {"critical": True, "missing_dates": ["2026-05-22", "2026-05-23", "2026-05-26"]},
            "stock_quote": {"critical": False, "missing_dates": ["2026-05-26"]},
        }
    }

    score = _compute_health_score(table_stats, {"daily_kbar": 1}, "2026-05-27", recent_coverage)

    assert score == 90


def test_find_issues_includes_reference_fallback_and_recent_gap_messages():
    table_stats = {
        "daily_kbar": {"label": "日K线", "critical": True, "total_rows": 100, "max_date": "2026-05-25"},
        "stock_quote": {"label": "实时快照", "critical": False, "total_rows": 100, "max_date": "2026-05-25"},
        "outcome": {"label": "标注结果", "critical": True, "total_rows": 10},
    }
    recent_coverage = {
        "window": 5,
        "tables": {
            "daily_kbar": {
                "label": "日K线",
                "critical": True,
                "missing_dates": ["2026-05-22", "2026-05-23", "2026-05-26", "2026-05-27"],
            }
        },
    }

    issues = _find_issues(
        table_stats,
        {"daily_kbar": 2, "stock_quote": 2},
        "2026-05-27",
        recent_coverage,
        {"source": "local", "note": "Tushare 最新交易日不可用，已回退到本地关键表最新交易日。"},
    )

    messages = [item["message"] for item in issues]
    assert any("回退到本地关键表最新交易日" in msg for msg in messages)
    assert any("最近 5 个交易日缺失 4 天" in msg for msg in messages)
    assert any("实时快照 未覆盖参考交易日" in msg for msg in messages)
