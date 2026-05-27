from __future__ import annotations

from unittest.mock import MagicMock

from app.services.data_gap_remediation_service import remediate_data_health_gaps


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
