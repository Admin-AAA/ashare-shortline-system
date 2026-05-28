from __future__ import annotations

from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.tables import DailyKbar, StockQuote
from app.services.data_gap_remediation_service import (
    DEFAULT_DAILY_KBAR_CODE_LIMIT,
    _backfill_daily_kbar_date,
    _daily_kbar_target_codes,
    _upsert_daily_kbar_payload,
    remediate_data_health_gaps,
)


def test_remediate_data_health_gaps_executes_targeted_plan(monkeypatch):
    db = MagicMock()

    before = {
        "score": 72,
        "reference_trade_date": "2026-05-27",
        "reference_trade_date_source": "local",
        "issues": [{"severity": "medium", "message": "概念板块滞后"}] * 4,
        "lag_days": {"index_daily": 1, "concept_sector": 1},
        "tables": {
            "index_daily": {"max_date": "2026-05-26"},
            "limit_up_board": {"max_date": "2026-05-26"},
            "capital_flow": {"max_date": "2026-05-26"},
            "concept_sector": {"max_date": "2026-05-26"},
        },
        "recent_trade_day_coverage": {
            "summary": {"max_missing_days": 1, "high_impact_tables": [], "warning_tables": ["index_daily", "limit_up_board", "capital_flow"]},
            "tables": {
                "index_daily": {"missing_dates": ["2026-05-27"]},
                "limit_up_board": {"missing_dates": ["2026-05-27"]},
                "capital_flow": {"missing_dates": ["2026-05-27"]},
            },
        },
    }
    after = {
        "score": 83,
        "reference_trade_date": "2026-05-27",
        "reference_trade_date_source": "local",
        "issues": [{"severity": "medium", "message": "仍有少量问题"}] * 1,
        "lag_days": {"index_daily": 0, "concept_sector": 0},
        "tables": {
            "index_daily": {"max_date": "2026-05-27"},
            "limit_up_board": {"max_date": "2026-05-27"},
            "capital_flow": {"max_date": "2026-05-27"},
            "concept_sector": {"max_date": "2026-05-27"},
        },
        "recent_trade_day_coverage": {
            "summary": {"max_missing_days": 0, "high_impact_tables": [], "warning_tables": []},
            "tables": {
                "index_daily": {"missing_dates": []},
                "limit_up_board": {"missing_dates": []},
                "capital_flow": {"missing_dates": []},
            },
        },
    }

    diagnosis_calls = [before, after]
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service.diagnose_all",
        lambda _db: diagnosis_calls.pop(0),
    )
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service._index_ts_codes",
        lambda _db: ["000300.SH", "000905.SH"],
    )
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service._backfill_index_daily_dates",
        lambda _db, target_dates, ts_codes: {
            "row_count": len(target_dates) * len(ts_codes),
            "requested_date_count": len(target_dates),
            "requested_ts_code_count": len(ts_codes),
        },
    )
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service.refresh_limit_up_board",
        lambda _db, trade_date=None: [{"trade_date": trade_date, "code": "000001"}],
    )
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service.refresh_concept_sectors",
        lambda _db, trade_date=None: [{"trade_date": trade_date, "sector_code": "885001"}],
    )
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service.refresh_capital_flows_for_codes",
        lambda _db, codes, trade_date=None: {code: {"trade_date": trade_date} for code in codes},
    )
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service._capital_flow_target_codes",
        lambda _db, trade_date, limit: ["000001", "000002", "000003"],
    )

    result = remediate_data_health_gaps(db, target_tables=["index_daily", "limit_up_board", "capital_flow", "concept_sector"])

    assert result["status"] == "completed"
    assert result["before"]["issue_count"] == 4
    assert result["after"]["issue_count"] == 1
    assert result["comparison"]["issue_count_delta"] == -3
    assert result["comparison"]["score_delta"] == 11
    assert result["comparison"]["target_missing_day_count"] == 3
    assert result["comparison"]["resolved_missing_day_count"] == 3
    assert result["comparison"]["unresolved_missing_day_count"] == 0
    assert result["comparison"]["remediation_coverage_rate"] == 1.0
    assert set(result["comparison"]["tables_fully_remediated"]) == {"index_daily", "limit_up_board", "capital_flow"}
    assert result["comparison"]["tables_still_missing"] == []
    assert result["comparison"]["table_deltas"]["index_daily"]["after_missing_days"] == 0
    assert result["comparison"]["table_deltas"]["index_daily"]["resolved_missing_dates"] == ["2026-05-27"]
    assert result["comparison"]["table_deltas"]["index_daily"]["remediation_coverage_rate"] == 1.0
    assert result["rollback_guidance"]["mode"] == "delete_by_operation_log"
    assert result["operation_log"][0]["rollback_hint"]

    actions = {item["table"]: item for item in result["actions"]}
    assert actions["index_daily"]["row_count"] == 2
    assert actions["index_daily"]["rollback_hint"].startswith("按 index_daily.ts_code")
    assert actions["limit_up_board"]["row_count"] == 1
    assert actions["capital_flow"]["requested_code_count"] == 3
    assert actions["concept_sector"]["row_count"] == 1


def test_remediate_data_health_gaps_dry_run_only_returns_plan(monkeypatch):
    db = MagicMock()
    before = {
        "score": 70,
        "reference_trade_date": "2026-05-27",
        "reference_trade_date_source": "local",
        "issues": [],
        "lag_days": {"index_daily": 1, "concept_sector": 0},
        "tables": {
            "index_daily": {"max_date": "2026-05-26"},
            "limit_up_board": {"max_date": "2026-05-26"},
            "capital_flow": {"max_date": "2026-05-26"},
            "concept_sector": {"max_date": "2026-05-27"},
        },
        "recent_trade_day_coverage": {
            "summary": {"max_missing_days": 1, "high_impact_tables": [], "warning_tables": ["index_daily"]},
            "tables": {
                "index_daily": {"missing_dates": ["2026-05-27"]},
                "limit_up_board": {"missing_dates": []},
                "capital_flow": {"missing_dates": []},
            },
        },
    }
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service.diagnose_all",
        lambda _db: before,
    )
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service._index_ts_codes",
        lambda _db: ["000300.SH"],
    )

    result = remediate_data_health_gaps(db, target_tables=["index_daily"], dry_run=True)

    assert result["status"] == "dry_run"
    assert result["before"] == result["after"]
    assert result["actions"] == [
        {
            "table": "index_daily",
            "target_dates": ["2026-05-27"],
            "ts_codes": ["000300.SH"],
            "status": "planned",
            "rollback_hint": "按 index_daily.ts_code + trade_date 精确删除本次目标日期（2026-05-27）写入行，再重新诊断。",
        }
    ]
    assert result["rollback_guidance"] == {
        "mode": "no_write",
        "message": "本次为预演，未写入数据库，无需回滚。",
        "keys": [],
    }
    assert result["operation_log"] == [
        {
            "table": "index_daily",
            "status": "planned",
            "target": "2026-05-27",
            "row_count": 0,
            "rollback_hint": "按 index_daily.ts_code + trade_date 精确删除本次目标日期（2026-05-27）写入行，再重新诊断。",
        }
    ]


def test_remediate_data_health_gaps_can_skip_post_diagnosis(monkeypatch):
    db = MagicMock()
    before = {
        "score": 70,
        "reference_trade_date": "2026-05-27",
        "reference_trade_date_source": "local",
        "issues": [{"severity": "medium", "message": "指数缺口"}],
        "lag_days": {"index_daily": 1},
        "tables": {"index_daily": {"max_date": "2026-05-26"}},
        "recent_trade_day_coverage": {
            "summary": {"max_missing_days": 1, "high_impact_tables": [], "warning_tables": ["index_daily"]},
            "tables": {"index_daily": {"missing_dates": ["2026-05-27"]}},
        },
    }
    diagnose_all = MagicMock(return_value=before)
    monkeypatch.setattr("app.services.data_gap_remediation_service.diagnose_all", diagnose_all)
    monkeypatch.setattr("app.services.data_gap_remediation_service._index_ts_codes", lambda _db: ["000300.SH"])
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service._backfill_index_daily_dates",
        lambda _db, target_dates, ts_codes: {
            "row_count": 1,
            "requested_date_count": len(target_dates),
            "requested_ts_code_count": len(ts_codes),
        },
    )

    result = remediate_data_health_gaps(
        db,
        target_tables=["index_daily"],
        post_diagnosis=False,
    )

    assert diagnose_all.call_count == 1
    assert result["status"] == "completed"
    assert result["after"]["diagnosis_skipped"] is True
    assert result["comparison"]["status"] == "skipped"
    assert result["remaining_issues"] is None
    assert result["action_summary"]["completed_count"] == 1


def test_remediate_data_health_gaps_returns_no_write_result_when_pre_diagnosis_fails(monkeypatch):
    db = MagicMock()

    def _raise_diagnosis_error(_db):
        raise TimeoutError("database handshake timed out")

    monkeypatch.setattr("app.services.data_gap_remediation_service.diagnose_all", _raise_diagnosis_error)

    result = remediate_data_health_gaps(db, target_tables=["daily_kbar", "index_daily"])

    assert result["status"] == "diagnosis_failed"
    assert result["requested_tables"] == ["daily_kbar", "index_daily"]
    assert result["actions"] == []
    assert result["operation_log"] == []
    assert result["action_summary"] == {
        "planned_count": 0,
        "completed_count": 0,
        "empty_count": 0,
        "skipped_count": 0,
        "failed_count": 0,
    }
    assert result["before"]["diagnosis_failed"] is True
    assert result["before"]["diagnosis_stage"] == "pre"
    assert result["comparison"]["status"] == "skipped"
    assert result["comparison"]["reason"] == "pre_diagnosis_failed"
    assert result["rollback_guidance"]["mode"] == "no_write"
    assert result["remaining_issues"] is None
    db.rollback.assert_called_once()


def test_remediate_data_health_gaps_preserves_actions_when_post_diagnosis_fails(monkeypatch):
    db = MagicMock()
    before = {
        "score": 70,
        "reference_trade_date": "2026-05-27",
        "reference_trade_date_source": "local",
        "issues": [{"severity": "medium", "message": "指数缺口"}],
        "lag_days": {"index_daily": 1},
        "tables": {"index_daily": {"max_date": "2026-05-26"}},
        "recent_trade_day_coverage": {
            "summary": {"max_missing_days": 1, "high_impact_tables": [], "warning_tables": ["index_daily"]},
            "tables": {"index_daily": {"missing_dates": ["2026-05-27"]}},
        },
    }
    diagnosis_calls = [before, TimeoutError("post diagnosis timed out")]

    def _diagnose(_db):
        result = diagnosis_calls.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr("app.services.data_gap_remediation_service.diagnose_all", _diagnose)
    monkeypatch.setattr("app.services.data_gap_remediation_service._index_ts_codes", lambda _db: ["000300.SH"])
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service._backfill_index_daily_dates",
        lambda _db, target_dates, ts_codes: {
            "row_count": len(target_dates) * len(ts_codes),
            "requested_date_count": len(target_dates),
            "requested_ts_code_count": len(ts_codes),
        },
    )

    result = remediate_data_health_gaps(db, target_tables=["index_daily"])

    assert result["status"] == "completed_post_diagnosis_failed"
    assert result["before"]["score"] == 70
    assert result["after"]["diagnosis_failed"] is True
    assert result["after"]["diagnosis_stage"] == "post"
    assert result["actions"][0]["status"] == "completed"
    assert result["actions"][0]["row_count"] == 1
    assert result["comparison"]["status"] == "skipped"
    assert result["comparison"]["reason"] == "post_diagnosis_failed"
    assert result["rollback_guidance"]["mode"] == "delete_by_operation_log"
    assert result["remaining_issues"] is None
    db.rollback.assert_called_once()


def test_remediation_plan_separates_daily_kbar_and_capital_flow_limits(monkeypatch):
    db = MagicMock()
    before = {
        "score": 70,
        "reference_trade_date": "2026-05-27",
        "reference_trade_date_source": "tushare",
        "issues": [],
        "lag_days": {},
        "tables": {"daily_kbar": {"max_date": "2026-05-27"}, "capital_flow": {"max_date": "2026-05-25"}},
        "recent_trade_day_coverage": {
            "summary": {"max_missing_days": 1, "high_impact_tables": [], "warning_tables": ["daily_kbar", "capital_flow"]},
            "tables": {
                "daily_kbar": {"missing_dates": [], "incomplete_dates": ["2026-05-27"]},
                "capital_flow": {"missing_dates": ["2026-05-26"]},
            },
        },
    }
    seen_limits: dict[str, int] = {}

    def _daily_codes(_db, trade_date, limit):
        seen_limits["daily_kbar"] = limit
        return ["000001"]

    def _capital_codes(_db, trade_date, limit):
        seen_limits["capital_flow"] = limit
        return ["000002"]

    monkeypatch.setattr("app.services.data_gap_remediation_service.diagnose_all", lambda _db: before)
    monkeypatch.setattr("app.services.data_gap_remediation_service._latest_allowed_bar_date", lambda: "2026-05-27")
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service._daily_kbar_target_codes",
        _daily_codes,
    )
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service._capital_flow_target_codes",
        _capital_codes,
    )

    result = remediate_data_health_gaps(
        db,
        target_tables=["daily_kbar", "capital_flow"],
        dry_run=True,
        capital_flow_code_limit=15,
    )

    assert seen_limits == {"daily_kbar": DEFAULT_DAILY_KBAR_CODE_LIMIT, "capital_flow": 15}
    actions = {item["table"]: item for item in result["actions"]}
    assert actions["daily_kbar"]["code_limit"] == DEFAULT_DAILY_KBAR_CODE_LIMIT
    assert actions["capital_flow"]["code_limit"] == 15


def test_remediate_data_health_gaps_continues_after_single_action_failure(monkeypatch):
    db = MagicMock()
    before = {
        "score": 60,
        "reference_trade_date": "2026-05-27",
        "reference_trade_date_source": "local",
        "issues": [{"severity": "high", "message": "多个表缺口"}] * 3,
        "lag_days": {"index_daily": 1, "concept_sector": 1},
        "tables": {
            "index_daily": {"max_date": "2026-05-26"},
            "limit_up_board": {"max_date": "2026-05-26"},
            "capital_flow": {"max_date": "2026-05-26"},
            "concept_sector": {"max_date": "2026-05-26"},
        },
        "recent_trade_day_coverage": {
            "summary": {"max_missing_days": 1, "high_impact_tables": ["index_daily"], "warning_tables": ["limit_up_board"]},
            "tables": {
                "index_daily": {"missing_dates": ["2026-05-27"]},
                "limit_up_board": {"missing_dates": ["2026-05-27"]},
                "capital_flow": {"missing_dates": ["2026-05-27"]},
            },
        },
    }
    after = {
        **before,
        "score": 69,
        "issues": [{"severity": "medium", "message": "涨停板仍未补齐"}],
        "lag_days": {"index_daily": 0, "concept_sector": 0},
        "recent_trade_day_coverage": {
            "summary": {"max_missing_days": 1, "high_impact_tables": [], "warning_tables": ["limit_up_board"]},
            "tables": {
                "index_daily": {"missing_dates": []},
                "limit_up_board": {"missing_dates": ["2026-05-27"]},
                "capital_flow": {"missing_dates": []},
            },
        },
    }
    diagnosis_calls = [before, after]
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service.diagnose_all",
        lambda _db: diagnosis_calls.pop(0),
    )
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service._index_ts_codes",
        lambda _db: ["000300.SH"],
    )
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service._backfill_index_daily_dates",
        lambda _db, target_dates, ts_codes: {
            "row_count": len(target_dates) * len(ts_codes),
            "requested_date_count": len(target_dates),
            "requested_ts_code_count": len(ts_codes),
        },
    )

    def _raise_limit_up_error(_db, trade_date=None):
        raise RuntimeError(f"limit_up_board source failed: {trade_date}")

    monkeypatch.setattr(
        "app.services.data_gap_remediation_service.refresh_limit_up_board",
        _raise_limit_up_error,
    )
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service._capital_flow_target_codes",
        lambda _db, trade_date, limit: ["000001"],
    )
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service.refresh_capital_flows_for_codes",
        lambda _db, codes, trade_date=None: {codes[0]: {"trade_date": trade_date}},
    )
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service.refresh_concept_sectors",
        lambda _db, trade_date=None: [{"trade_date": trade_date, "sector_code": "885001"}],
    )

    result = remediate_data_health_gaps(
        db,
        target_tables=["index_daily", "limit_up_board", "capital_flow", "concept_sector"],
    )

    assert result["status"] == "partial_failed"
    assert result["after"]["score"] == 69
    assert result["comparison"]["score_delta"] == 9
    assert result["comparison"]["target_missing_day_count"] == 3
    assert result["comparison"]["resolved_missing_day_count"] == 2
    assert result["comparison"]["unresolved_missing_day_count"] == 1
    assert result["comparison"]["remediation_coverage_rate"] == 0.667
    assert result["comparison"]["tables_still_missing"] == ["limit_up_board"]
    assert result["action_summary"]["failed_count"] == 1
    assert result["action_summary"]["completed_count"] == 3
    actions = {item["table"]: item for item in result["actions"]}
    assert actions["limit_up_board"]["status"] == "failed"
    assert actions["limit_up_board"]["error_type"] == "RuntimeError"
    assert actions["limit_up_board"]["rollback_hint"].startswith("按 limit_up_board.trade_date")
    assert actions["capital_flow"]["status"] == "completed"
    assert result["comparison"]["table_deltas"]["limit_up_board"]["unresolved_missing_dates"] == ["2026-05-27"]
    db.rollback.assert_called_once()


def test_daily_kbar_target_codes_excludes_existing_compact_trade_date():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[DailyKbar.__table__, StockQuote.__table__])
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        db.add(DailyKbar(code="000001", trade_date="20260527", open=1, high=1, low=1, close=1))
        db.add_all(
            [
                StockQuote(code="000001", trade_date="2026-05-27", name="已有", source="test", amount_yi=100),
                StockQuote(code="000002", trade_date="2026-05-27", name="候选2", source="test", amount_yi=80),
                StockQuote(code="000003", trade_date="2026-05-27", name="候选3", source="test", amount_yi=120),
            ]
        )
        db.commit()

        codes = _daily_kbar_target_codes(db, "2026-05-27", limit=2)

        assert codes == ["000003", "000002"]
    finally:
        db.close()


def test_backfill_daily_kbar_date_uses_trade_date_batch_fetch(monkeypatch):
    db = MagicMock()
    calls = []

    def _fake_fetch(trade_date):
        calls.append(trade_date)
        return [
            {"code": "000001", "trade_date": "20260527", "open": 1, "high": 2, "low": 1, "close": 2},
            {"code": "000002", "trade_date": "2026-05-27", "open": 1, "high": 2, "low": 1, "close": 2},
            {"code": "000004", "trade_date": "2026-05-27", "open": 1, "high": 2, "low": 1, "close": 2},
        ]

    monkeypatch.setattr(
        "app.services.data_gap_remediation_service.fetch_daily_kbars_for_trade_date",
        _fake_fetch,
    )

    result = _backfill_daily_kbar_date(db, "2026-05-27", ["000001", "000003"])

    assert calls == ["2026-05-27"]
    assert result["row_count"] == 1
    assert result["provider_row_count"] == 3
    assert result["requested_code_count"] == 2
    assert result["target_hit_count"] == 1
    assert result["missing_code_count"] == 1
    assert result["missing_codes"] == ["000003"]
    db.execute.assert_called_once()
    db.commit.assert_called_once()


def test_upsert_daily_kbar_payload_splits_large_batches():
    db = MagicMock()
    payload = [
        {"code": f"00000{idx}", "trade_date": "2026-05-27", "open": 1, "high": 2, "low": 1, "close": 2}
        for idx in range(5)
    ]

    _upsert_daily_kbar_payload(db, payload, batch_size=2)

    assert db.execute.call_count == 3
    db.commit.assert_called_once()


def test_default_remediation_plan_covers_full_recommendation_data_dependencies(monkeypatch):
    db = MagicMock()

    before = {
        "score": 58,
        "reference_trade_date": "2026-05-27",
        "reference_trade_date_source": "tushare",
        "issues": [{"severity": "medium", "message": "推荐上下文依赖数据缺失"}] * 6,
        "lag_days": {"index_daily": 1, "concept_sector": 1, "dragon_tiger": 1, "news": 1},
        "tables": {
            "daily_kbar": {"max_date": "2026-05-27"},
            "index_daily": {"max_date": "2026-05-26"},
            "limit_up_board": {"max_date": "2026-05-26"},
            "capital_flow": {"max_date": "2026-05-26"},
            "concept_sector": {"max_date": "2026-05-26"},
            "dragon_tiger": {"max_date": "2026-05-26"},
            "news": {"max_date": "2026-05-26"},
        },
        "recent_trade_day_coverage": {
            "summary": {"max_missing_days": 1, "high_impact_tables": [], "warning_tables": ["index_daily", "limit_up_board", "capital_flow"]},
            "tables": {
                "daily_kbar": {"missing_dates": [], "incomplete_dates": ["2026-05-27"]},
                "index_daily": {"missing_dates": ["2026-05-27"]},
                "limit_up_board": {"missing_dates": ["2026-05-27"]},
                "capital_flow": {"missing_dates": ["2026-05-27"]},
            },
        },
    }
    after = {
        "score": 87,
        "reference_trade_date": "2026-05-27",
        "reference_trade_date_source": "tushare",
        "issues": [],
        "lag_days": {"index_daily": 0, "concept_sector": 0, "dragon_tiger": 0, "news": 0},
        "tables": {
            "daily_kbar": {"max_date": "2026-05-27"},
            "index_daily": {"max_date": "2026-05-27"},
            "limit_up_board": {"max_date": "2026-05-27"},
            "capital_flow": {"max_date": "2026-05-27"},
            "concept_sector": {"max_date": "2026-05-27"},
            "dragon_tiger": {"max_date": "2026-05-27"},
            "news": {"max_date": "2026-05-27"},
        },
        "recent_trade_day_coverage": {
            "summary": {"max_missing_days": 0, "high_impact_tables": [], "warning_tables": []},
            "tables": {
                "daily_kbar": {"missing_dates": [], "incomplete_dates": []},
                "index_daily": {"missing_dates": []},
                "limit_up_board": {"missing_dates": []},
                "capital_flow": {"missing_dates": []},
            },
        },
    }
    diagnosis_calls = [before, after]

    monkeypatch.setattr(
        "app.services.data_gap_remediation_service.diagnose_all",
        lambda _db: diagnosis_calls.pop(0),
    )
    monkeypatch.setattr("app.services.data_gap_remediation_service._index_ts_codes", lambda _db: ["000300.SH"])
    monkeypatch.setattr("app.services.data_gap_remediation_service._latest_allowed_bar_date", lambda: "2026-05-27")
    monkeypatch.setattr("app.services.data_gap_remediation_service._daily_kbar_target_codes", lambda _db, trade_date, limit: ["000001", "000002"])
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service._backfill_daily_kbar_date",
        lambda _db, trade_date, codes: {
            "row_count": len(codes),
            "requested_code_count": len(codes),
            "target_hit_count": len(codes),
            "failed_code_count": 0,
            "failed_codes": [],
        },
    )
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service._backfill_index_daily_dates",
        lambda _db, target_dates, ts_codes: {
            "row_count": len(target_dates) * len(ts_codes),
            "requested_date_count": len(target_dates),
            "requested_ts_code_count": len(ts_codes),
        },
    )
    monkeypatch.setattr("app.services.data_gap_remediation_service._auxiliary_target_codes", lambda _db, trade_date, limit: ["000001", "000002"])
    monkeypatch.setattr("app.services.data_gap_remediation_service._capital_flow_target_codes", lambda _db, trade_date, limit: ["000001", "000002"])
    monkeypatch.setattr("app.services.data_gap_remediation_service.refresh_limit_up_board", lambda _db, trade_date=None: [{"trade_date": trade_date}])
    monkeypatch.setattr("app.services.data_gap_remediation_service.refresh_concept_sectors", lambda _db, trade_date=None: [{"trade_date": trade_date}])
    monkeypatch.setattr("app.services.data_gap_remediation_service.refresh_capital_flows_for_codes", lambda _db, codes, trade_date=None: {code: [{"trade_date": trade_date}] for code in codes})
    monkeypatch.setattr("app.services.data_gap_remediation_service.refresh_dragon_tiger", lambda _db, trade_date=None: [{"trade_date": trade_date, "code": "000001"}])
    monkeypatch.setattr("app.services.data_gap_remediation_service.refresh_news_for_codes", lambda _db, codes, trade_date=None: {code: [{"announce_date": trade_date}] for code in codes})

    result = remediate_data_health_gaps(db)

    assert result["requested_tables"] == [
        "daily_kbar",
        "index_daily",
        "limit_up_board",
        "capital_flow",
        "concept_sector",
        "dragon_tiger",
        "news",
    ]
    assert {item["table"] for item in result["actions"]} == set(result["requested_tables"])
    assert result["comparison"]["score_delta"] == 29
    assert result["comparison"]["table_deltas"]["daily_kbar"]["resolved_incomplete_dates"] == ["2026-05-27"]
    assert result["comparison"]["table_deltas"]["dragon_tiger"]["lag_days_delta"] == -1
    assert result["comparison"]["table_deltas"]["news"]["lag_days_delta"] == -1
    assert result["action_summary"]["completed_count"] == 7

    actions = {item["table"]: item for item in result["actions"]}
    assert actions["daily_kbar"]["target_hit_count"] == 2
    assert actions["daily_kbar"]["rollback_hint"].startswith("按 daily_kbars.trade_date")
    assert actions["dragon_tiger"]["row_count"] == 1
    assert actions["news"]["requested_code_count"] == 2
    assert actions["news"]["row_count"] == 2
    assert actions["news"]["rollback_hint"].startswith("按 news_announcement.announce_date")
