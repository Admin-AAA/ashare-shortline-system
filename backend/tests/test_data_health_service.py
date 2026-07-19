from __future__ import annotations

from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.services.data_health_service as data_health_service
from app.db.session import Base
from app.models.tables import CapitalFlow, DailyKbar, IndexDaily, LimitUpBoard, Recommendation, StockQuote
from app.services.data_health_service import (
    _TABLE_DEFS,
    _compute_health_score,
    _find_issues,
    _recent_trade_day_coverage,
    _recent_daily_trade_days,
    _resolve_reference_trade_date,
    _table_stats,
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


def test_resolve_reference_trade_date_caps_external_calendar_at_latest_closed_session():
    db = MagicMock()
    with patch("app.services.data_health_service._get_tushare_latest_trade_date", return_value="2026-05-27"), patch(
        "app.services.data_health_service._latest_allowed_bar_date",
        return_value="2026-05-26",
    ):
        result = _resolve_reference_trade_date(db)

    assert result["reference_trade_date"] == "2026-05-26"
    assert result["external_trade_date"] == "2026-05-27"
    assert "收盘" in result["note"]


def test_daily_data_domain_one_day_lag_is_stale():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[StockQuote.__table__])
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        db.add(StockQuote(code="000001", name="测试股票", trade_date="2026-05-26", source="test"))
        db.commit()
        quote_domain = next(item for item in data_health_service._DATA_DOMAIN_DEFS if item["key"] == "realtime_quote")

        with patch.object(data_health_service, "_DATA_DOMAIN_DEFS", [quote_domain]):
            result = data_health_service._data_domain_coverage(db, "2026-05-27")

        domain = result["categories"][0]["domains"][0]
        assert domain["status"] == "stale"
        assert domain["gap_days"] == 1
        assert domain["fresh_window_days"] == 0
    finally:
        db.close()


def test_domain_inventory_uses_current_margin_source_and_quarterly_hk_hold_cadence():
    domains = {item["key"]: item for item in data_health_service._DATA_DOMAIN_DEFS}

    assert "margin" not in domains
    assert domains["margin_detail"]["table"].__tablename__ == "margin_detail"
    assert domains["hk_hold"]["fresh_days"] == 130


def test_recent_daily_trade_days_includes_reference_even_when_daily_table_missed_whole_day():
    db = MagicMock()
    rows = MagicMock()
    rows.all.return_value = ["2026-05-26", "2026-05-25", "2026-05-22", "2026-05-21"]
    db.scalars.return_value = rows

    result = _recent_daily_trade_days(db, "2026-05-27", 5)

    assert result == ["2026-05-21", "2026-05-22", "2026-05-25", "2026-05-26", "2026-05-27"]


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


def test_data_health_normalizes_mixed_trade_dates_and_flags_incomplete_daily_kbar():
    engine = create_engine("sqlite:///:memory:")
    tables = [
        DailyKbar.__table__,
        IndexDaily.__table__,
        LimitUpBoard.__table__,
        CapitalFlow.__table__,
        StockQuote.__table__,
        Recommendation.__table__,
    ]
    Base.metadata.create_all(engine, tables=tables)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        for day in ["20260523", "2026-05-24", "20260525", "2026-05-26"]:
            db.add(DailyKbar(code="000001", trade_date=day, open=1, high=1, low=1, close=1))
            db.add(IndexDaily(ts_code="000001.SH", trade_date=day, close=1))
        for idx in range(5):
            db.add(DailyKbar(code=f"30{idx:04d}", trade_date="2026-05-27", open=1, high=1, low=1, close=1))
        db.add(LimitUpBoard(code="000001", trade_date="20260526", name="样本"))
        db.add(CapitalFlow(code="000001", trade_date="20260526"))
        db.commit()

        stats = _table_stats(db)
        coverage = _recent_trade_day_coverage(db, "2026-05-27", window=5)

        assert stats["daily_kbar"]["max_date"] == "2026-05-27"
        assert stats["index_daily"]["max_date"] == "2026-05-26"
        assert coverage["trade_days"] == [
            "2026-05-23",
            "2026-05-24",
            "2026-05-25",
            "2026-05-26",
            "2026-05-27",
        ]
        assert coverage["tables"]["daily_kbar"]["missing_dates"] == []
        assert coverage["tables"]["daily_kbar"]["incomplete_dates"] == [
            "2026-05-23",
            "2026-05-24",
            "2026-05-25",
            "2026-05-26",
            "2026-05-27",
        ]
        assert coverage["tables"]["index_daily"]["missing_dates"] == ["2026-05-27"]
        assert coverage["tables"]["limit_up_board"]["present_dates"] == ["2026-05-26"]
        assert coverage["tables"]["limit_up_board"]["missing_dates"] == [
            "2026-05-23",
            "2026-05-24",
            "2026-05-25",
            "2026-05-27",
        ]
        assert "daily_kbar" in coverage["summary"]["high_impact_tables"]
    finally:
        db.close()


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


def test_compute_health_score_penalizes_closed_reference_day_gap():
    table_stats = {
        "daily_kbar": {"critical": True, "total_rows": 100, "label": "日K线", "max_date": "2026-05-26"},
        "index_daily": {"critical": True, "total_rows": 100, "label": "指数日线", "max_date": "2026-05-26"},
        "recommendation": {"critical": True, "total_rows": 10},
        "outcome": {"critical": True, "total_rows": 8},
    }
    recent_coverage = {
        "window": 5,
        "tables": {
            "daily_kbar": {"label": "日K线", "critical": True, "missing_dates": ["2026-05-27"]},
            "index_daily": {"label": "指数日线", "critical": True, "missing_dates": ["2026-05-27"]},
        },
    }

    score = _compute_health_score(table_stats, {"daily_kbar": 1, "index_daily": 1}, "2026-05-27", recent_coverage)
    issues = _find_issues(
        table_stats,
        {"daily_kbar": 1, "index_daily": 1},
        "2026-05-27",
        recent_coverage,
        {"source": "tushare", "note": "参考交易日来自 Tushare 交易日历。"},
    )

    assert score < 100
    assert any("2026-05-27" in item["message"] for item in issues)


def test_recent_trade_day_coverage_does_not_tolerate_closed_reference_day_gap():
    db = MagicMock()
    trade_days = ["2026-05-23", "2026-05-24", "2026-05-25", "2026-05-26", "2026-05-27"]
    day_rows = MagicMock()
    day_rows.all.return_value = trade_days
    present_rows = MagicMock()
    present_rows.all.return_value = trade_days
    db.scalars.side_effect = [day_rows] + [present_rows] * 8

    with patch(
        "app.services.data_health_service._incomplete_daily_kbar_dates",
        return_value=["2026-05-27"],
    ):
        result = _recent_trade_day_coverage(db, "2026-05-27", window=5)

    daily = result["tables"]["daily_kbar"]
    assert daily["incomplete_dates"] == ["2026-05-27"]
    assert daily["tolerated_gap_dates"] == []
    assert daily["effective_gap_count"] == 1
    assert result["summary"]["max_missing_days"] == 1
    assert result["summary"]["high_impact_tables"] == []
    assert result["summary"]["warning_tables"] == []


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
