from __future__ import annotations

import time
from threading import Event, Lock, Thread
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
    with patch("app.services.data_health_service._get_tushare_recent_trade_dates", return_value=[]), patch(
        "app.services.data_health_service._infer_local_reference_trade_date",
        return_value="2026-05-26",
    ):
        result = _resolve_reference_trade_date(db)

    assert result["reference_trade_date"] == "2026-05-26"
    assert result["external_trade_date"] is None
    assert result["source"] == "local"
    assert "回退" in result["note"]


def test_diagnose_all_coalesces_concurrent_cold_requests(monkeypatch):
    data_health_service.clear_data_health_cache()
    started = Event()
    release = Event()
    call_count_lock = Lock()
    call_count = 0
    results: list[dict] = []
    errors: list[BaseException] = []

    def slow_diagnosis(_db):
        nonlocal call_count
        with call_count_lock:
            call_count += 1
        started.set()
        assert release.wait(timeout=2)
        return {"reference_trade_date": "2026-05-27", "score": 100}

    def run_diagnosis():
        try:
            results.append(data_health_service.diagnose_all(MagicMock()))
        except BaseException as exc:  # noqa: BLE001 - surface thread failures in the assertion below
            errors.append(exc)

    monkeypatch.setattr(data_health_service, "_diagnose_all_uncached", slow_diagnosis)
    first = Thread(target=run_diagnosis)
    second = Thread(target=run_diagnosis)
    try:
        first.start()
        assert started.wait(timeout=1)
        second.start()
        assert not release.wait(timeout=0.1)
        assert call_count == 1

        release.set()
        first.join(timeout=2)
        second.join(timeout=2)

        assert not first.is_alive()
        assert not second.is_alive()
        assert errors == []
        assert call_count == 1
        assert results == [
            {"reference_trade_date": "2026-05-27", "score": 100},
            {"reference_trade_date": "2026-05-27", "score": 100},
        ]
    finally:
        release.set()
        first.join(timeout=2)
        second.join(timeout=2)
        data_health_service.clear_data_health_cache()


def test_resolve_reference_trade_date_caps_external_calendar_at_latest_closed_session():
    db = MagicMock()
    with patch(
        "app.services.data_health_service._get_tushare_recent_trade_dates",
        return_value=["2026-05-25", "2026-05-26", "2026-05-27"],
    ), patch(
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


def test_column_data_domain_skips_expensive_exact_row_count():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[DailyKbar.__table__])
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        db.add(DailyKbar(code="000001", trade_date="2026-05-27", close=1, chip_cost_50=1.0))
        db.commit()
        chip_domain = next(item for item in data_health_service._DATA_DOMAIN_DEFS if item["key"] == "chip")

        with patch.object(data_health_service, "_DATA_DOMAIN_DEFS", [chip_domain]):
            result = data_health_service._data_domain_coverage(db, "2026-05-27")

        domain = result["categories"][0]["domains"][0]
        assert domain["status"] == "covered"
        assert domain["total_rows"] is None
        assert domain["total_rows_estimated"] is False
        assert domain["max_date"] == "2026-05-27"
    finally:
        db.close()


def test_indexed_date_queries_do_not_wrap_trade_date_column():
    db = MagicMock()
    db.execute.return_value.one.return_value = ("2026-05-26", "2026-05-27")
    rows = MagicMock()
    rows.all.return_value = ["2026-05-27", "2026-05-26"]
    db.scalars.return_value = rows

    data_health_service._date_bounds(db, DailyKbar.trade_date)
    bounds_statement = str(db.execute.call_args.args[0]).lower()
    assert "replace(" not in bounds_statement

    _recent_daily_trade_days(db, "2026-05-27", 2)
    recent_statement = str(db.scalars.call_args.args[0]).lower()
    assert "replace(" not in recent_statement


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


def test_data_health_uses_canonical_trade_dates_and_flags_incomplete_daily_kbar():
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
        for day in ["2026-05-23", "2026-05-24", "2026-05-25", "2026-05-26"]:
            db.add(DailyKbar(code="000001", trade_date=day, open=1, high=1, low=1, close=1))
            db.add(IndexDaily(ts_code="000001.SH", trade_date=day, close=1))
        for idx in range(5):
            db.add(DailyKbar(code=f"30{idx:04d}", trade_date="2026-05-27", open=1, high=1, low=1, close=1))
        db.add(LimitUpBoard(code="000001", trade_date="2026-05-26", name="样本"))
        db.add(CapitalFlow(code="000001", trade_date="2026-05-26"))
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


def test_incomplete_minute_kbar_dates_flags_thin_coverage_days_only():
    """分钟线「有行但只补到几只票」必须算不完整：这是 09-04/09-23/10-12 长期没被补的缺口来源。"""
    db = MagicMock()
    rows = MagicMock()
    rows.all.return_value = [("2026-06-09", 300), ("2026-06-10", 3), (None, 19)]
    db.execute.return_value = rows

    out = data_health_service._incomplete_minute_kbar_dates(
        db, ["2026-06-09", "2026-06-10", "2026-06-11"]
    )

    # 300 只达标；3 只腰斩；06-11 完全没有行 → 属于 missing 而不是 incomplete
    assert out == ["2026-06-10"]


def test_incomplete_minute_kbar_dates_merges_date_format_variants():
    db = MagicMock()
    rows = MagicMock()
    rows.all.return_value = [("2026-06-10", 120), ("20260610", 121)]
    db.execute.return_value = rows

    out = data_health_service._incomplete_minute_kbar_dates(db, ["2026-06-10"])

    assert out == []  # 两种日期格式合计 241 只，刚过 240 门槛，不能误报


def _seed_stale_cache(monkeypatch, score: int = 90) -> None:
    """放一份「已过期」的缓存：TTL 归零 + 数据版本探测固定为 None。"""
    data_health_service.clear_data_health_cache()
    data_health_service._set_cached_diagnosis(
        {"reference_trade_date": "2026-05-27", "score": score}, None
    )
    data_health_service._diagnosis_cache["ts"] = 0.0
    monkeypatch.setattr(data_health_service, "_diagnosis_data_version", lambda _db: None)


def _wait_background_refresh(timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not data_health_service._diagnosis_in_progress:
            return
        time.sleep(0.01)
    raise AssertionError("后台重算没有在超时内结束")


def test_diagnose_all_allow_stale_returns_old_result_and_refreshes_in_background(monkeypatch):
    """HTTP 读接口口径：缓存过期先返旧值，后台重算，页面不等 4~17s 的冷算。"""
    _seed_stale_cache(monkeypatch, score=90)
    computed = Event()

    def fresh(_db):
        computed.set()
        return {"reference_trade_date": "2026-05-28", "score": 100}

    monkeypatch.setattr(data_health_service, "_diagnose_all_uncached", fresh)
    monkeypatch.setattr(data_health_service, "SessionLocal", lambda: MagicMock())

    started = time.monotonic()
    result = data_health_service.diagnose_all(MagicMock(), allow_stale=True)
    elapsed = time.monotonic() - started

    assert result["score"] == 90            # 立刻拿旧值
    assert elapsed < 0.5                    # 不在请求线程里等冷算
    assert computed.wait(timeout=2)         # 后台确实重算了一次
    _wait_background_refresh()
    assert data_health_service._diagnosis_cache["result"]["score"] == 100
    data_health_service.clear_data_health_cache()


def test_diagnose_all_default_still_recomputes_synchronously_when_expired(monkeypatch):
    """数据管道 / 出票闸门口径不变：缓存过期就同步重算，绝不用旧结论。"""
    _seed_stale_cache(monkeypatch, score=90)
    calls: list[int] = []

    def fresh(_db):
        calls.append(1)
        return {"reference_trade_date": "2026-05-28", "score": 77}

    monkeypatch.setattr(data_health_service, "_diagnose_all_uncached", fresh)

    result = data_health_service.diagnose_all(MagicMock())

    assert result["score"] == 77
    assert calls == [1]
    assert not data_health_service._diagnosis_in_progress
    data_health_service.clear_data_health_cache()


def test_background_refresh_is_not_duplicated_by_concurrent_stale_readers(monkeypatch):
    _seed_stale_cache(monkeypatch, score=90)
    started, release = Event(), Event()
    calls: list[int] = []
    calls_lock = Lock()

    def slow(_db):
        with calls_lock:
            calls.append(1)
        started.set()
        assert release.wait(timeout=2)
        return {"reference_trade_date": "2026-05-28", "score": 100}

    monkeypatch.setattr(data_health_service, "_diagnose_all_uncached", slow)
    monkeypatch.setattr(data_health_service, "SessionLocal", lambda: MagicMock())
    try:
        data_health_service.diagnose_all(MagicMock(), allow_stale=True)
        assert started.wait(timeout=2)
        data_health_service.diagnose_all(MagicMock(), allow_stale=True)
        assert calls == [1]
    finally:
        release.set()
        _wait_background_refresh()
        data_health_service.clear_data_health_cache()


def test_diagnose_all_force_refresh_bypasses_fresh_cache(monkeypatch):
    """「重新诊断」/每日快照口径：force_refresh 必须真的重算，不能被新鲜缓存短路。"""
    data_health_service.clear_data_health_cache()
    data_health_service._set_cached_diagnosis({"reference_trade_date": "2026-05-27", "score": 90}, None)
    monkeypatch.setattr(data_health_service, "_diagnosis_data_version", lambda _db: None)
    calls: list[int] = []

    def fresh(_db):
        calls.append(1)
        return {"reference_trade_date": "2026-05-27", "score": 42}

    monkeypatch.setattr(data_health_service, "_diagnose_all_uncached", fresh)

    assert data_health_service.diagnose_all(MagicMock())["score"] == 90
    assert data_health_service.diagnose_all(MagicMock(), force_refresh=True)["score"] == 42
    assert calls == [1]
    data_health_service.clear_data_health_cache()
