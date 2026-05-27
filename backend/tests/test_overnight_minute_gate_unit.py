from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import app

from app.services.overnight_holding_service import (
    _evaluate_preflight_minute_gate,
    _local_minute_inventory,
    remediate_minute_gaps,
    run_overnight_holding_backtest,
)


def test_evaluate_preflight_minute_gate_blocks_low_coverage(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(
        "app.services.overnight_holding_service._load_benchmark_bundle",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.overnight_holding_service._load_daily_basic_map",
        lambda trade_date, cache: {"001234": {"turnover_rate": 6.0, "total_mv_yi": 120.0, "circ_mv_yi": 110.0}},
    )
    monkeypatch.setattr(
        "app.services.overnight_holding_service._daily_candidates",
        lambda **kwargs: [
            {"code": "001234", "name": "隔夜样本", "industry": "银行", "trade_date": kwargs["trade_date"]},
        ],
    )
    minute_map = {
        ("001234", "2026-05-19"): [],
        ("001234", "2026-05-20"): [],
        ("001234", "2026-05-21"): [{"trade_time": "2026-05-21 14:30:00", "close": 11.9}],
    }
    monkeypatch.setattr(
        "app.services.overnight_holding_service._load_stock_minutes",
        lambda _db, cache, code, trade_date, minute_source_trace=None: minute_map.get((code, trade_date), []),
    )

    gate = _evaluate_preflight_minute_gate(
        db=db,
        signal_dates=["2026-05-19", "2026-05-20", "2026-05-21"],
        date_lookup=["2026-05-19", "2026-05-20", "2026-05-21", "2026-05-22"],
        daily_by_date={},
        history_by_code={},
        stock_meta={},
        daily_basic_cache={},
        minute_cache={},
        benchmark_cache={},
        minute_source_trace=[],
    )

    assert gate["passed"] is False
    assert gate["candidate_count"] == 3
    assert gate["minute_ready_candidate_count"] == 1
    assert gate["coverage_rate"] == pytest.approx(0.333, abs=0.001)
    assert gate["benchmark_missing_dates"] == ["2026-05-19", "2026-05-20", "2026-05-21", "2026-05-22"]


def test_evaluate_preflight_minute_gate_counts_full_missing_not_sample_cap(monkeypatch):
    db = MagicMock()
    candidates = [
        {"code": f"{idx:06d}", "name": f"样本{idx}", "industry": "测试", "trade_date": "2026-05-19"}
        for idx in range(60)
    ]
    monkeypatch.setattr(
        "app.services.overnight_holding_service._load_benchmark_bundle",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.overnight_holding_service._load_daily_basic_map",
        lambda trade_date, cache: {},
    )
    monkeypatch.setattr(
        "app.services.overnight_holding_service._daily_candidates",
        lambda **kwargs: candidates,
    )
    monkeypatch.setattr(
        "app.services.overnight_holding_service._load_stock_minutes",
        lambda _db, cache, code, trade_date, minute_source_trace=None: [],
    )

    gate = _evaluate_preflight_minute_gate(
        db=db,
        signal_dates=["2026-05-19"],
        date_lookup=["2026-05-19", "2026-05-20"],
        daily_by_date={},
        history_by_code={},
        stock_meta={},
        daily_basic_cache={},
        minute_cache={},
        benchmark_cache={},
        minute_source_trace=[],
    )

    day = gate["date_summaries"][0]
    assert day["candidate_count"] == 60
    assert day["minute_missing_candidate_count"] == 60
    assert len(day["missing_minute_codes"]) == 8
    assert gate["missing_pair_count"] == 60
    assert gate["missing_pair_sample_count"] == 50
    assert len(gate["missing_pairs"]) == 50


def test_run_overnight_holding_backtest_returns_blocked_when_minute_gate_fails(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(
        "app.services.overnight_holding_service._signal_trade_dates",
        lambda _db, start, end: (["2026-05-19", "2026-05-20"], ["2026-05-19", "2026-05-20", "2026-05-21"]),
    )
    monkeypatch.setattr(
        "app.services.overnight_holding_service._load_daily_window",
        lambda _db, start, end: ({}, {}),
    )
    monkeypatch.setattr(
        "app.services.overnight_holding_service._load_stock_meta",
        lambda _db, codes: {},
    )
    monkeypatch.setattr(
        "app.services.overnight_holding_service._local_minute_inventory",
        lambda _db: {"trade_date_start": "2026-05-19", "trade_date_end": "2026-05-21", "trade_date_count": 3, "code_count": 1},
    )
    monkeypatch.setattr(
        "app.services.overnight_holding_service._evaluate_preflight_minute_gate",
        lambda **kwargs: {
            "passed": False,
            "reason": "分钟线覆盖不足",
            "candidate_count": 12,
            "minute_ready_candidate_count": 4,
            "benchmark_required_dates": ["2026-05-19", "2026-05-20"],
            "benchmark_missing_dates": ["2026-05-20"],
            "date_summaries": [
                {
                    "trade_date": "2026-05-19",
                    "status": "precheck",
                    "candidate_count": 12,
                    "minute_ready_candidate_count": 4,
                    "minute_missing_candidate_count": 8,
                    "benchmark_minute_covered": False,
                    "missing_minute_codes": ["001234"],
                }
            ],
            "notes": ["先验分钟线覆盖不足"],
        },
    )
    persisted: dict[str, str] = {}
    monkeypatch.setattr(
        "app.services.overnight_holding_service._persist_backtest_run",
        lambda _db, start_date, end_date, result, status="completed": persisted.update({"status": status, "backtest_status": result["status"]}),
    )

    result = run_overnight_holding_backtest(db, "2026-05-19", "2026-05-20")

    assert result["status"] == "insufficient_minute_coverage"
    assert result["aggregate_metrics"]["trade_count"] == 0
    assert result["minute_gate"]["passed"] is False
    assert persisted == {"status": "insufficient_minute_coverage", "backtest_status": "insufficient_minute_coverage"}


def test_remediate_minute_gaps_improves_gate_result(monkeypatch):
    db = MagicMock()
    before = {
        "status": "insufficient_minute_coverage",
        "minute_gate": {
            "passed": False,
            "missing_pair_count": 3,
            "missing_pairs": [
                {"trade_date": "2026-05-19", "code": "001234"},
                {"trade_date": "2026-05-19", "code": "005678"},
            ],
            "benchmark_missing_dates": ["2026-05-19", "2026-05-20"],
        },
        "data_coverage": {
            "candidate_minute_coverage_rate": 0.25,
            "benchmark_minute_coverage_rate": 0.5,
        },
    }
    after = {
        "status": "ready",
        "minute_gate": {
            "passed": True,
            "missing_pair_count": 1,
            "missing_pairs": [{"trade_date": "2026-05-19", "code": "005678"}],
            "benchmark_missing_dates": ["2026-05-20"],
        },
        "data_coverage": {
            "candidate_minute_coverage_rate": 0.9,
            "benchmark_minute_coverage_rate": 1.0,
        },
    }
    preview_calls = [before, after]
    monkeypatch.setattr(
        "app.services.overnight_holding_service.preview_minute_gap_remediation",
        lambda _db, start_date, end_date: preview_calls.pop(0),
    )
    monkeypatch.setattr(
        "app.services.overnight_holding_service._execute_minute_gap_remediation",
        lambda _db, gate: [
            {"scope": "stock", "trade_date": "2026-05-19", "requested_code_count": 1, "row_count": 240, "status": "completed"},
            {"scope": "benchmark", "trade_date_start": "2026-05-19", "trade_date_end": "2026-05-20", "requested_date_count": 2, "row_count": 480, "status": "completed"},
        ],
    )

    result = remediate_minute_gaps(db, "2026-05-19", "2026-05-21")

    assert result["status"] == "completed"
    assert result["comparison"]["candidate_coverage_rate_delta"] == pytest.approx(0.65, abs=0.001)
    assert result["comparison"]["benchmark_coverage_rate_delta"] == pytest.approx(0.5, abs=0.001)
    assert result["comparison"]["gate_passed_before"] is False
    assert result["comparison"]["gate_passed_after"] is True
    assert result["comparison"]["target_missing_pair_count"] == 3
    assert result["comparison"]["resolved_missing_pair_count"] == 2
    assert result["comparison"]["unresolved_missing_pair_count"] == 1
    assert result["comparison"]["sample_resolved_missing_pair_count"] == 1
    assert result["comparison"]["sample_unresolved_missing_pair_count"] == 1
    assert result["comparison"]["benchmark_target_missing_date_count"] == 2
    assert result["comparison"]["benchmark_resolved_missing_date_count"] == 1
    assert result["comparison"]["benchmark_unresolved_missing_date_count"] == 1
    assert result["comparison"]["target_missing_unit_count"] == 5
    assert result["comparison"]["resolved_missing_unit_count"] == 3
    assert result["comparison"]["unresolved_missing_unit_count"] == 2
    assert result["comparison"]["remediation_coverage_rate"] == pytest.approx(0.6, abs=0.001)


def test_local_minute_inventory_uses_bounded_group_queries_not_full_distincts():
    class FakeExecuteResult:
        def one(self):
            return ("2026-05-19", "2026-05-26", 2)

    class FakeDB:
        def __init__(self):
            self.queries: list[str] = []

        def execute(self, query):
            self.queries.append(str(query).lower())
            return FakeExecuteResult()

        def scalar(self, query):
            self.queries.append(str(query).lower())
            return 319

        def rollback(self):
            raise AssertionError("rollback should not be needed")

    db = FakeDB()

    result = _local_minute_inventory(db)  # type: ignore[arg-type]

    assert result["trade_date_start"] == "2026-05-19"
    assert result["trade_date_end"] == "2026-05-26"
    assert result["trade_date_count"] == 2
    assert result["code_count"] == 319
    assert result["inventory_status"] == "ok"
    assert result["trade_date_count_scope"] == "db_raw_trade_dates_indexed"
    assert len(db.queries) == 2
    assert "replace" not in db.queries[0]
    assert "count(distinct" in db.queries[0]
    assert "group by" in db.queries[1]


def test_local_minute_inventory_normalizes_display_dates_without_sql_replace():
    class FakeExecuteResult:
        def one(self):
            return ("20260524", "20260526", 3)

    class FakeDB:
        def __init__(self):
            self.queries: list[str] = []

        def execute(self, query):
            self.queries.append(str(query).lower())
            return FakeExecuteResult()

        def scalar(self, query):
            query_text = str(query).lower()
            self.queries.append(query_text)
            return 428

        def rollback(self):
            raise AssertionError("rollback should not be needed")

    db = FakeDB()

    result = _local_minute_inventory(db)  # type: ignore[arg-type]

    assert result["trade_date_start"] == "2026-05-24"
    assert result["trade_date_end"] == "2026-05-26"
    assert result["trade_date_count"] == 3
    assert result["code_count"] == 428
    assert result["trade_date_count_scope"] == "db_raw_trade_dates_indexed"
    assert "replace" not in db.queries[0]


def test_backtest_api_preview_minute_gate_dispatches_to_preview_service(monkeypatch):
    fake_db = object()
    calls: list[tuple[object, str, str]] = []

    def override_db():
        yield fake_db

    def fake_preview(db, start_date, end_date):
        calls.append((db, start_date, end_date))
        return {
            "status": "ready",
            "strategy_window": {"start_date": start_date, "end_date": end_date},
            "minute_gate": {"passed": True, "reason": "mock gate ready"},
            "data_coverage": {"candidate_minute_coverage_rate": 1.0},
        }

    def fail_full_backtest(*args, **kwargs):
        raise AssertionError("preview action must not run full overnight backtest")

    monkeypatch.setattr(
        "app.services.overnight_holding_service.preview_minute_gap_remediation",
        fake_preview,
    )
    monkeypatch.setattr(
        "app.services.overnight_holding_service.run_overnight_holding_backtest",
        fail_full_backtest,
    )
    app.dependency_overrides[get_db] = override_db
    try:
        response = TestClient(app).post(
            "/api/runs/backtest",
            json={
                "strategy_id": "overnight-holding-method",
                "action": "preview_minute_gate",
                "start_date": "2026-05-19",
                "end_date": "2026-05-21",
            },
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["minute_gate"]["passed"] is True
    assert calls == [(fake_db, "2026-05-19", "2026-05-21")]


def test_backtest_api_preview_minute_gate_rejects_bad_date_range(monkeypatch):
    def fail_preview(*args, **kwargs):
        raise AssertionError("invalid date range should be rejected before service dispatch")

    monkeypatch.setattr(
        "app.services.overnight_holding_service.preview_minute_gap_remediation",
        fail_preview,
    )

    response = TestClient(app).post(
        "/api/runs/backtest",
        json={
            "strategy_id": "overnight-holding-method",
            "action": "preview_minute_gate",
            "start_date": "2026-05-21",
            "end_date": "2026-05-19",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "start_date must be <= end_date"
