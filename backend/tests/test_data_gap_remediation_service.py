from __future__ import annotations

import json
from unittest.mock import MagicMock
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.tables import Recommendation, RecommendationRun, DailyKbar, StockQuote
from app.services.market_source import InMemoryMarketSourceAdapter
from app.services.data_gap_remediation_service import (
    DEFAULT_DAILY_KBAR_CODE_LIMIT,
    _backfill_daily_kbar_date,
    _backfill_index_daily_dates,
    _backfill_minute_kbar_date,
    _backfill_news_announcements,
    _candidate_quote_date,
    _chip_target_dates,
    _daily_kbar_target_codes,
    _is_natural_short_history,
    _local_pool_gap_comparison,
    _minute_probe_failed,
    _provider_activity_delta,
    _upsert_daily_kbar_payload,
    remediate_data_health_gaps,
    remediate_recommendation_run_auxiliary_data,
    sync_local_pool_kline_gaps,
)
from scripts.backfill_latest_days import get_missing_trade_dates


def test_provider_activity_route_policy_does_not_claim_hard_coded_primary_provider():
    activity = _provider_activity_delta({}, {})

    assert activity["route_policy"] == "通用接口按数据源配置优先级路由；特殊 API 按能力路由；失败时安全 fallback。"


def test_chip_target_dates_cover_field_gaps_after_daily_rows_exist():
    diagnosis = {
        "reference_trade_date": "2026-09-21",
        "recent_trade_day_coverage": {
            "trade_days": [
                "2026-09-16",
                "2026-09-17",
                "2026-09-18",
                "2026-09-21",
            ],
        },
        "data_domains": {
            "categories": [
                {
                    "category": "行情K线",
                    "domains": [
                        {
                            "key": "chip",
                            "status": "stale",
                            "max_date": "2026-09-18",
                        }
                    ],
                }
            ]
        },
    }

    assert _chip_target_dates(diagnosis, max_closed_trade_date="2026-09-21") == ["2026-09-21"]


def test_minute_probe_stops_only_when_every_probe_has_provider_error():
    unavailable = [
        ("000001", [], RuntimeError("data_pending")),
        ("000002", [], RuntimeError("business_rate_limit")),
        ("000003", [], RuntimeError("data_pending")),
    ]
    available = [
        *unavailable[:2],
        ("000003", [{"trade_date": "2026-09-21", "trade_time": "09:31:00"}], None),
    ]

    assert _minute_probe_failed(unavailable) is True
    assert _minute_probe_failed(available) is False


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
        lambda _db, **kw: diagnosis_calls.pop(0),
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
    def _refresh_auxiliary(_db, *, dataset, trade_date, codes=None):
        del trade_date
        return (5500 if dataset == "capital_flow" else 1, {"dataset": dataset})

    monkeypatch.setattr(
        "app.services.data_gap_remediation_service._refresh_auxiliary_dataset",
        _refresh_auxiliary,
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
    assert actions["capital_flow"]["scope"] == "full_market"
    assert actions["capital_flow"]["row_count"] == 5500
    assert actions["concept_sector"]["row_count"] == 1

    # 纯观测计时：逐表 + 逐动作，覆盖每个计划动作，不影响动作结果。
    timings = result["timings_seconds"]
    assert set(timings["actions_by_table"]) == set(actions)
    assert len(timings["actions"]) == len(result["actions"])
    per_action = {item["table"]: item for item in timings["actions"]}
    assert per_action["index_daily"]["target_date_count"] == 1
    assert per_action["limit_up_board"]["trade_date"] == "2026-05-27"
    assert all(item["seconds"] >= 0 and item["status"] for item in timings["actions"])
    assert timings["total"] >= timings["pre_diagnosis_and_plan"]


def test_remediate_data_health_gaps_targets_closed_reference_trade_date_when_lagged(monkeypatch):
    db = MagicMock()

    before = {
        "score": 100,
        "reference_trade_date": "2026-06-10",
        "reference_trade_date_source": "tushare",
        "issues": [],
        "lag_days": {
            "daily_kbar": 1,
            "minute_kbar": 1,
            "index_daily": 1,
            "limit_up_board": 1,
            "capital_flow": 1,
        },
        "tables": {
            "daily_kbar": {"max_date": "2026-06-09"},
            "minute_kbar": {"max_date": "2026-06-09"},
            "index_daily": {"max_date": "2026-06-09"},
            "limit_up_board": {"max_date": "2026-06-09"},
            "capital_flow": {"max_date": "2026-06-09"},
        },
        "recent_trade_day_coverage": {
            "summary": {"max_missing_days": 0, "high_impact_tables": [], "warning_tables": []},
            "tables": {
                "daily_kbar": {"missing_dates": [], "incomplete_dates": []},
                "minute_kbar": {"missing_dates": []},
                "index_daily": {"missing_dates": []},
                "limit_up_board": {"missing_dates": []},
                "capital_flow": {"missing_dates": []},
            },
        },
    }
    after = {
        **before,
        "lag_days": {
            "daily_kbar": 0,
            "minute_kbar": 0,
            "index_daily": 0,
            "limit_up_board": 0,
            "capital_flow": 0,
        },
        "tables": {
            "daily_kbar": {"max_date": "2026-06-10"},
            "minute_kbar": {"max_date": "2026-06-10"},
            "index_daily": {"max_date": "2026-06-10"},
            "limit_up_board": {"max_date": "2026-06-10"},
            "capital_flow": {"max_date": "2026-06-10"},
        },
    }

    diagnosis_calls = [before, after]
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service.diagnose_all",
        lambda _db, **kw: diagnosis_calls.pop(0),
    )
    monkeypatch.setattr("app.services.data_gap_remediation_service._latest_allowed_bar_date", lambda: "2026-06-10")
    monkeypatch.setattr("app.services.data_gap_remediation_service._daily_kbar_target_codes", lambda _db, trade_date, limit: ["000001"])
    monkeypatch.setattr("app.services.data_gap_remediation_service._minute_kbar_target_codes", lambda _db, trade_date, limit: ["000001"])
    monkeypatch.setattr("app.services.data_gap_remediation_service._index_ts_codes", lambda _db: ["000001.SH"])
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service._backfill_daily_kbar_date",
        lambda _db, trade_date, codes: {"row_count": len(codes), "trade_date": trade_date},
    )
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service._backfill_minute_kbar_date",
        lambda _db, trade_date, codes: {"row_count": len(codes), "trade_date": trade_date},
    )
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service._backfill_index_daily_dates",
        lambda _db, target_dates, ts_codes: {"row_count": len(target_dates) * len(ts_codes), "target_dates": target_dates},
    )
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service._refresh_auxiliary_dataset",
        lambda _db, *, dataset, trade_date, codes=None: (1, {"dataset": dataset}),
    )

    result = remediate_data_health_gaps(
        db,
        target_tables=["daily_kbar", "minute_kbar", "index_daily", "limit_up_board", "capital_flow"],
    )

    actions = result["actions"]
    assert [item["table"] for item in actions] == [
        "daily_kbar",
        "minute_kbar",
        "index_daily",
        "limit_up_board",
        "capital_flow",
    ]
    assert actions[0]["trade_date"] == "2026-06-10"
    assert actions[1]["trade_date"] == "2026-06-10"
    assert actions[2]["target_dates"] == ["2026-06-10"]
    assert actions[3]["trade_date"] == "2026-06-10"
    assert actions[4]["trade_date"] == "2026-06-10"


def test_sync_local_pool_kline_gaps_reports_unlimited_scope(monkeypatch):
    db = MagicMock()
    gaps = [
        {"code": "000001", "name": "样本1", "reason": "missing", "bar_count": 0, "quote_date": "2026-05-28"},
        {"code": "000002", "name": "样本2", "reason": "missing", "bar_count": 0, "quote_date": "2026-05-28"},
    ]
    counts = {
        "candidate_count": 2,
        "checked_count": 2,
        "ready_count": 0,
        "missing_count": 2,
        "short_history_count": 0,
        "immature_history_count": 0,
        "stale_count": 0,
        "gap_count": 2,
        "latest_allowed_bar_date": "2026-05-28",
    }

    monkeypatch.setattr(
        "app.services.data_gap_remediation_service._local_pool_kline_gap_state",
        lambda _db, min_bars: {"counts": counts, "gaps": gaps},
    )

    result = sync_local_pool_kline_gaps(db, code_limit=0, dry_run=True)

    assert result["code_limit"] == 0
    assert result["effective_code_limit"] == 2
    assert result["target_count"] == 2


def test_candidate_quote_date_allows_current_realtime_quote_bar_gap(monkeypatch):
    monkeypatch.setattr("app.services.market_pool.core._latest_allowed_quote_date", lambda today=None: "2026-06-10")

    quote_date = _candidate_quote_date({"trade_date": "2026-06-10"}, "2026-06-10")

    assert quote_date == "2026-06-09"


def test_sync_local_pool_kline_gaps_batches_same_day_stale_targets(monkeypatch):
    db = MagicMock()
    gaps = [
        {"code": "000001", "name": "样本1", "reason": "stale", "bar_count": 90, "latest_bar_date": "2026-05-29", "quote_date": "2026-06-02"},
        {"code": "000002", "name": "样本2", "reason": "stale", "bar_count": 90, "latest_bar_date": "2026-05-29", "quote_date": "2026-06-02"},
    ]
    before_counts = {
        "candidate_count": 2,
        "checked_count": 2,
        "ready_count": 0,
        "missing_count": 0,
        "short_history_count": 0,
        "immature_history_count": 0,
        "stale_count": 2,
        "gap_count": 2,
        "latest_allowed_bar_date": "2026-06-03",
    }
    after_counts = {**before_counts, "ready_count": 2, "stale_count": 0, "gap_count": 0}

    states = [
        {"counts": before_counts, "gaps": gaps},
        {"counts": after_counts, "gaps": []},
    ]
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service._local_pool_kline_gap_state",
        lambda _db, min_bars: states.pop(0),
    )
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service._backfill_daily_kbar_date",
        lambda _db, trade_date, codes, **_kwargs: {
            "row_count": 2,
            "provider_row_count": 5200,
            "requested_code_count": len(codes),
            "target_hit_count": 2,
            "missing_code_count": 0,
            "missing_codes": [],
        },
    )

    result = sync_local_pool_kline_gaps(db, code_limit=0, dry_run=False)

    assert result["status"] == "completed"
    assert result["row_count"] == 2
    assert result["succeeded_count"] == 2
    assert result["operation_log"] == [
        {
            "table": "daily_kbar",
            "trade_date": "2026-06-02",
            "status": "completed",
            "row_count": 2,
            "provider_row_count": 5200,
            "requested_code_count": 2,
            "target_hit_count": 2,
            "missing_code_count": 0,
            "missing_codes": [],
            "mode": "trade_date_batch",
        }
    ]


def test_backfill_latest_days_detects_incomplete_latest_trade_date(monkeypatch):
    class _Result:
        def __init__(self, *, scalar_value=None, rows=None):
            self._scalar_value = scalar_value
            self._rows = rows or []

        def scalar(self):
            return self._scalar_value

        def all(self):
            return self._rows

    class _Row:
        def __init__(self, trade_date):
            self.trade_date = trade_date

    class _TradeCal:
        empty = False

        def iterrows(self):
            yield 0, {"cal_date": "20260602"}

    class _Pro:
        def trade_cal(self, **_kwargs):
            return _TradeCal()

    db = MagicMock()
    db.execute.side_effect = [
        _Result(scalar_value="2026-06-02"),
        _Result(rows=[_Row("2026-06-02")]),
    ]
    monkeypatch.setattr("app.services.tushare_client.fetch_latest_trade_date", lambda: "20260602")
    monkeypatch.setattr("app.services.tushare_client.get_pro", lambda: _Pro())
    monkeypatch.setattr("scripts.backfill_latest_days._incomplete_daily_kbar_dates", lambda _db, days: ["2026-06-02"])

    assert get_missing_trade_dates(db) == ["2026-06-02"]


def test_remediate_recommendation_run_auxiliary_data_updates_candidate_snapshot(db, monkeypatch):
    unique_id = uuid4().hex[:12]
    run_id = f"run_aux_{unique_id}"
    recommendation_id = f"rec_aux_{unique_id}"
    run = RecommendationRun(
        run_id=run_id,
        run_name="辅助证据补齐测试",
        trade_date="2026-05-27",
        universe_count=1,
        recommended_count=1,
        market_scope="all",
        risk_template="balanced",
        output_count=1,
        selected_skills_json="[]",
        params_json="{}",
        market_snapshot_json=json.dumps({"trade_date": "2026-05-27", "breadth": {"data_source": "local_pool"}}),
        result_summary_json="{}",
        filter_audit_json="{}",
        status="completed",
    )
    feature_snapshot = {
        "code": "000001",
        "name": "平安银行",
        "industry": "银行",
        "price": 10,
        "pct": 2.0,
        "amount_yi": 8.0,
        "turnover_rate": 3.0,
        "free_mv_yi": 100.0,
        "total_mv_yi": 200.0,
        "pe_ttm": 8.0,
        "latest_trade_date": "2026-05-27",
        "data_source": "real_market",
        "data_quality": {
            "strict_real_data": True,
            "kline_days": 90,
            "missing_fields": ["announcements", "capital_flow", "dragon_tiger", "main_net_inflow", "news"],
            "quote_source": "unified_pool",
            "quote_source_count": 1,
            "quote_quality_score": 100,
            "bars_source": "daily_bars_cache",
        },
        "data_completeness_score": 56.0,
        "evidence_quality_score": 31.0,
    }
    db.add(run)
    db.add(
        Recommendation(
            recommendation_id=recommendation_id,
            run_id=run.run_id,
            trade_date=run.trade_date,
            code="000001",
            name="平安银行",
            industry="银行",
            recommend_price=10,
            bucket="",
            final_score=58.0,
            consensus_score=50.0,
            setup_type="多头顺势",
            risk_tags_json="[]",
            info_tags_json=json.dumps(["多维度数据缺失（公告/龙虎榜/题材等），置信度受影响"]),
            feature_snapshot_json=json.dumps(feature_snapshot),
            market_context_json="{}",
            skill_breakdown_json="[]",
            agent_summary_json="{}",
            status="pending",
        )
    )
    db.commit()

    def _refresh_auxiliary(_db, *, dataset, trade_date, codes=None):
        return (len(codes or ()) if dataset in {"capital_flow", "news_announcement"} else 1, {"dataset": dataset})

    monkeypatch.setattr(
        "app.services.data_gap_remediation_service._refresh_auxiliary_dataset",
        _refresh_auxiliary,
    )
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service.load_auxiliary_feature_views",
        lambda _db, codes, trade_date=None: {
            "limit_up": {},
            "concept_sectors": {"000001": [{"sector_name": "金融科技", "heat_rank": 3}]},
            "capital_flows": {"000001": {"main_net_inflow_yi": 1.2, "super_large_net_inflow_yi": 0.5, "large_net_inflow_yi": 0.7}},
            "dragon_tigers": {"000001": {"trade_date": trade_date, "buy_amount_yi": 2.0, "sell_amount_yi": 1.0, "net_amount_yi": 1.0}},
            "news": {"000001": [{"announce_date": trade_date, "title": "订单增长", "sentiment": "positive"}]},
            "auctions": {},
            "pledges": {},
            "broker_recs": {},
            "share_floats_upcoming": {},
            "coverage": {"dragon_tiger_trade_date_covered": True, "dragon_tiger_trade_date_count": 1},
        },
    )

    dry_run = remediate_recommendation_run_auxiliary_data(db, run.run_id, dry_run=True)
    assert dry_run["status"] == "planned"
    rec = db.get(Recommendation, recommendation_id)
    assert "capital_flow" in json.loads(rec.feature_snapshot_json)["data_quality"]["missing_fields"]

    result = remediate_recommendation_run_auxiliary_data(db, run.run_id)

    rec = db.get(Recommendation, recommendation_id)
    updated = json.loads(rec.feature_snapshot_json)
    assert result["status"] == "completed"
    assert result["comparison"]["resolved_missing_field_count"] >= 4
    assert result["comparison"]["average_data_completeness_score_delta"] > 0
    assert "capital_flow" not in updated["data_quality"]["missing_fields"]
    assert "news" not in updated["data_quality"]["missing_fields"]
    assert updated["main_net_inflow_yi"] == 1.2
    assert updated["latest_news"] == "订单增长"
    run_snapshot = json.loads(db.get(RecommendationRun, run_id).market_snapshot_json)
    audit = run_snapshot["breadth"]["auxiliary_data_audit"]
    assert audit["capital_flows"]["requested_count"] == 1
    assert audit["capital_flows"]["count"] == 1
    assert audit["news"]["count"] == 1


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


def test_remediation_plan_capital_flow_is_full_market_and_covers_incomplete_days(monkeypatch):
    # L-166：capital_flow 补数一次拉全市场，不再截前 N 只；只写了一部分的交易日
    # （incomplete_dates）也要重补，否则「前 300 只」的日子永远被当成已覆盖。
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
                "capital_flow": {"missing_dates": ["2026-05-26"], "incomplete_dates": ["2026-05-25"]},
            },
        },
    }
    seen_limits: dict[str, int] = {}

    def _daily_codes(_db, trade_date, limit):
        seen_limits["daily_kbar"] = limit
        return ["000001"]

    monkeypatch.setattr("app.services.data_gap_remediation_service.diagnose_all", lambda _db: before)
    monkeypatch.setattr("app.services.data_gap_remediation_service._latest_allowed_bar_date", lambda: "2026-05-27")
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service._daily_kbar_target_codes",
        _daily_codes,
    )

    result = remediate_data_health_gaps(
        db,
        target_tables=["daily_kbar", "capital_flow"],
        dry_run=True,
        capital_flow_code_limit=15,
    )

    assert seen_limits == {"daily_kbar": DEFAULT_DAILY_KBAR_CODE_LIMIT}
    actions = [item for item in result["actions"] if item["table"] == "capital_flow"]
    assert [item["trade_date"] for item in actions] == ["2026-05-25", "2026-05-26"]
    assert all(item["codes"] == [] and item["scope"] == "full_market" for item in actions)
    daily = next(item for item in result["actions"] if item["table"] == "daily_kbar")
    assert daily["code_limit"] == DEFAULT_DAILY_KBAR_CODE_LIMIT


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
        lambda _db, **kw: diagnosis_calls.pop(0),
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


    def _refresh_auxiliary(_db, *, dataset, trade_date, codes=None):
        if dataset == "limit_up_board":
            raise RuntimeError(f"limit_up_board source failed: {trade_date}")
        return (5500 if dataset == "capital_flow" else 1, {"dataset": dataset})

    monkeypatch.setattr(
        "app.services.data_gap_remediation_service._refresh_auxiliary_dataset",
        _refresh_auxiliary,
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


def test_remediate_data_health_gaps_marks_empty_required_action_partial(monkeypatch):
    db = MagicMock()
    before = {
        "score": 94,
        "reference_trade_date": "2026-06-12",
        "reference_trade_date_source": "tushare",
        "issues": [{"severity": "medium", "message": "涨停板缺失"}],
        "lag_days": {"limit_up_board": 2},
        "tables": {"limit_up_board": {"max_date": "2026-06-10"}},
        "recent_trade_day_coverage": {
            "summary": {"max_missing_days": 1, "high_impact_tables": [], "warning_tables": ["limit_up_board"]},
            "tables": {"limit_up_board": {"missing_dates": ["2026-06-11"]}},
        },
    }
    after = {
        **before,
        "issues": [{"severity": "medium", "message": "涨停板仍缺失"}],
        "recent_trade_day_coverage": {
            "summary": {"max_missing_days": 1, "high_impact_tables": [], "warning_tables": ["limit_up_board"]},
            "tables": {"limit_up_board": {"missing_dates": ["2026-06-11"]}},
        },
    }
    diagnosis_calls = [before, after]

    monkeypatch.setattr("app.services.data_gap_remediation_service.diagnose_all", lambda _db, **kw: diagnosis_calls.pop(0))
    monkeypatch.setattr("app.services.data_gap_remediation_service._latest_allowed_bar_date", lambda: "2026-06-11")
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service._refresh_auxiliary_dataset",
        lambda _db, *, dataset, trade_date, codes=None: (0, {"dataset": dataset}),
    )

    result = remediate_data_health_gaps(db, target_tables=["limit_up_board"])

    assert result["status"] == "partial_completed"
    assert result["action_summary"]["empty_count"] == 1
    assert result["action_summary"]["failed_count"] == 0
    assert result["actions"][0]["status"] == "empty"
    assert result["comparison"]["tables_still_missing"] == ["limit_up_board"]
    assert result["comparison"]["table_deltas"]["limit_up_board"]["unresolved_missing_dates"] == ["2026-06-11"]


def test_daily_kbar_target_codes_includes_quote_date_codes_for_full_refresh():
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

        codes = _daily_kbar_target_codes(db, "2026-05-27", limit=3)

        assert codes == ["000003", "000001", "000002"]
    finally:
        db.close()


def test_backfill_daily_kbar_date_uses_one_typed_trade_date_batch() -> None:
    db = MagicMock()
    source = InMemoryMarketSourceAdapter(
        bars_by_code={
            "000001": [{"code": "000001", "trade_date": "20260527", "open": 1, "high": 2, "low": 1, "close": 2}],
            "000002": [{"code": "000002", "trade_date": "2026-05-27", "open": 1, "high": 2, "low": 1, "close": 2}],
            "000004": [{"code": "000004", "trade_date": "2026-05-27", "open": 1, "high": 2, "low": 1, "close": 2}],
        }
    )
    source.fetch_daily_bars = MagicMock(wraps=source.fetch_daily_bars)  # type: ignore[method-assign]

    result = _backfill_daily_kbar_date(
        db,
        "2026-05-27",
        ["000001", "000003"],
        market_source=source,
    )

    source.fetch_daily_bars.assert_called_once()
    request = source.fetch_daily_bars.call_args.args[0]
    assert request.is_trade_date_batch
    assert request.trade_date == "2026-05-27"
    assert [reference.code for reference in request.instruments] == ["000001", "000003"]
    assert result["row_count"] == 1
    assert result["provider_row_count"] == 3
    assert result["requested_code_count"] == 2
    assert result["target_hit_count"] == 1
    assert result["missing_code_count"] == 1
    assert result["missing_codes"] == ["000003"]
    assert result["market_source_audit"]["status"] == "success"
    assert result["market_source_audit"]["returned_count"] == 3
    db.execute.assert_called_once()
    db.commit.assert_called_once()


def test_backfill_index_daily_dates_normalizes_target_dates(monkeypatch):
    db = MagicMock()

    def _fake_fetch(ts_code, start_date=None, end_date=None):
        assert ts_code == "000300.SH"
        assert start_date == "20260601"
        assert end_date == "20260604"
        return [
            {"ts_code": ts_code, "trade_date": "20260601", "open": 1, "high": 2, "low": 1, "close": 2},
            {"ts_code": ts_code, "trade_date": "2026-06-04", "open": 3, "high": 4, "low": 3, "close": 4},
            {"ts_code": ts_code, "trade_date": "20260605", "open": 5, "high": 6, "low": 5, "close": 6},
        ]

    monkeypatch.setattr("app.services.data_gap_remediation_service.fetch_index_daily", _fake_fetch)

    result = _backfill_index_daily_dates(db, ["20260601", "2026-06-04"], ["000300.SH"])

    assert result["row_count"] == 2
    assert result["requested_date_count"] == 2
    assert result["requested_ts_code_count"] == 1
    db.execute.assert_called_once()
    db.commit.assert_called_once()


def test_remediate_data_health_gaps_executes_minute_kbar_plan(monkeypatch):
    db = MagicMock()
    before = {
        "score": 72,
        "reference_trade_date": "2026-05-28",
        "reference_trade_date_source": "tushare",
        "issues": [{"severity": "medium", "message": "分钟K线缺口"}],
        "lag_days": {},
        "tables": {"minute_kbar": {"max_date": "2026-05-26"}},
        "recent_trade_day_coverage": {
            "summary": {"max_missing_days": 1, "high_impact_tables": [], "warning_tables": ["minute_kbar"]},
            "tables": {"minute_kbar": {"missing_dates": ["2026-05-27"]}},
        },
    }
    after = {
        **before,
        "score": 80,
        "issues": [],
        "tables": {"minute_kbar": {"max_date": "2026-05-27"}},
        "recent_trade_day_coverage": {
            "summary": {"max_missing_days": 0, "high_impact_tables": [], "warning_tables": []},
            "tables": {"minute_kbar": {"missing_dates": []}},
        },
    }
    diagnosis_calls = [before, after]
    seen_limits = []

    monkeypatch.setattr("app.services.data_gap_remediation_service.diagnose_all", lambda _db, **kw: diagnosis_calls.pop(0))
    monkeypatch.setattr("app.services.data_gap_remediation_service._latest_allowed_bar_date", lambda: "2026-05-27")
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service._minute_kbar_target_codes",
        lambda _db, trade_date, limit: seen_limits.append((trade_date, limit)) or ["000001", "000002"],
    )
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service._backfill_minute_kbar_date",
        lambda _db, trade_date, codes: {
            "row_count": len(codes) * 240,
            "provider_row_count": len(codes) * 240,
            "requested_code_count": len(codes),
            "target_hit_count": len(codes),
            "missing_code_count": 0,
            "missing_codes": [],
            "failed_code_count": 0,
            "failed_codes": [],
        },
    )

    result = remediate_data_health_gaps(
        db,
        target_tables=["minute_kbar"],
        minute_kbar_code_limit=2,
    )

    assert seen_limits == [("2026-05-27", 2)]
    assert result["status"] == "completed"
    assert result["comparison"]["tables_fully_remediated"] == ["minute_kbar"]
    assert result["comparison"]["table_deltas"]["minute_kbar"]["resolved_missing_dates"] == ["2026-05-27"]
    action = result["actions"][0]
    assert action["table"] == "minute_kbar"
    assert action["row_count"] == 480
    assert action["requested_code_count"] == 2
    assert action["rollback_hint"].startswith("按 minute_kbars.trade_date")


def test_backfill_minute_kbar_date_filters_target_date_and_upserts(monkeypatch):
    db = MagicMock()

    def _fake_fetch(code, trade_date=None, freq="1min"):
        return [
            {
                "trade_date": trade_date,
                "trade_time": f"{trade_date} 09:31:00",
                "open": 1,
                "high": 2,
                "low": 1,
                "close": 2,
                "volume": 100,
                "amount": 200,
            },
            {
                "trade_date": "2026-05-26",
                "trade_time": "2026-05-26 09:31:00",
                "open": 1,
                "high": 2,
                "low": 1,
                "close": 2,
                "volume": 100,
                "amount": 200,
            },
        ] if code == "000001" else []

    monkeypatch.setattr("app.services.data_gap_remediation_service.fetch_minute_kbars", _fake_fetch)

    result = _backfill_minute_kbar_date(db, "2026-05-27", ["000001", "000002"])

    assert result["row_count"] == 1
    assert result["provider_row_count"] == 2
    assert result["target_hit_count"] == 1
    assert result["missing_codes"] == ["000002"]
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
            "minute_kbar": {"max_date": "2026-05-26"},
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
                "minute_kbar": {"missing_dates": ["2026-05-27"]},
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
            "minute_kbar": {"max_date": "2026-05-27"},
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
                "minute_kbar": {"missing_dates": []},
                "index_daily": {"missing_dates": []},
                "limit_up_board": {"missing_dates": []},
                "capital_flow": {"missing_dates": []},
            },
        },
    }
    diagnosis_calls = [before, after]

    monkeypatch.setattr(
        "app.services.data_gap_remediation_service.diagnose_all",
        lambda _db, **kw: diagnosis_calls.pop(0),
    )
    monkeypatch.setattr("app.services.data_gap_remediation_service._index_ts_codes", lambda _db: ["000300.SH"])
    monkeypatch.setattr("app.services.data_gap_remediation_service._latest_allowed_bar_date", lambda: "2026-05-27")
    monkeypatch.setattr("app.services.data_gap_remediation_service._daily_kbar_target_codes", lambda _db, trade_date, limit: ["000001", "000002"])
    monkeypatch.setattr("app.services.data_gap_remediation_service._minute_kbar_target_codes", lambda _db, trade_date, limit: ["000001", "000002"])
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
        "app.services.data_gap_remediation_service._backfill_minute_kbar_date",
        lambda _db, trade_date, codes: {
            "row_count": len(codes) * 240,
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
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service._refresh_auxiliary_dataset",
        lambda _db, *, dataset, trade_date, codes=None: (
            5500 if dataset == "capital_flow" else len(codes or ()) if dataset == "news_announcement" else 1,
            {"dataset": dataset},
        ),
    )

    result = remediate_data_health_gaps(db)

    assert result["requested_tables"] == [
        "daily_kbar",
        "chip",
        "minute_kbar",
        "index_daily",
        "limit_up_board",
        "capital_flow",
        "concept_sector",
        "dragon_tiger",
        "news",
        "concept_member",
        "value_distribution",
    ]
    # 本夹具的诊断快照里没有值分布失真、也没有滞后数据域，因此这两项不产生动作；
    # chip 同理（无缺口）。
    assert {item["table"] for item in result["actions"]} == set(result["requested_tables"]) - {
        "chip",
        "concept_member",
        "value_distribution",
    }
    assert result["comparison"]["score_delta"] == 29
    assert result["comparison"]["table_deltas"]["daily_kbar"]["resolved_incomplete_dates"] == ["2026-05-27"]
    assert result["comparison"]["table_deltas"]["minute_kbar"]["resolved_missing_dates"] == ["2026-05-27"]
    assert result["comparison"]["table_deltas"]["dragon_tiger"]["lag_days_delta"] == -1
    assert result["comparison"]["table_deltas"]["news"]["lag_days_delta"] == -1
    assert result["action_summary"]["completed_count"] == 8

    actions = {item["table"]: item for item in result["actions"]}
    assert actions["daily_kbar"]["target_hit_count"] == 2
    assert actions["daily_kbar"]["rollback_hint"].startswith("按 daily_kbars.trade_date")
    assert actions["minute_kbar"]["requested_code_count"] == 2
    assert actions["minute_kbar"]["rollback_hint"].startswith("按 minute_kbars.trade_date")
    assert actions["dragon_tiger"]["row_count"] == 1
    assert actions["news"]["requested_code_count"] == 2
    assert actions["news"]["row_count"] == 2
    assert actions["news"]["rollback_hint"].startswith("按 news_announcement.announce_date")


def test_local_pool_gap_comparison_reports_candidate_coverage_lift():
    result = _local_pool_gap_comparison(
        before_counts={
            "candidate_count": 10,
            "ready_count": 6,
            "gap_count": 4,
        },
        after_counts={
            "candidate_count": 10,
            "ready_count": 9,
            "gap_count": 1,
        },
        target_count=4,
        succeeded_count=3,
        unresolved_count=1,
        failed_count=0,
    )

    assert result == {
        "before_candidate_count": 10,
        "after_candidate_count": 10,
        "before_ready_count": 6,
        "after_ready_count": 9,
        "ready_count_delta": 3,
        "before_gap_count": 4,
        "after_gap_count": 1,
        "gap_count_delta": -3,
        "target_gap_count": 4,
        "resolved_target_count": 3,
        "unresolved_target_count": 1,
        "failed_target_count": 0,
        "candidate_coverage_rate_before": 0.6,
        "candidate_coverage_rate_after": 0.9,
        "candidate_coverage_rate_delta": 0.3,
        "remediation_coverage_rate": 0.75,
    }


def test_local_pool_gap_comparison_marks_no_gap_as_fully_covered():
    result = _local_pool_gap_comparison(
        before_counts={"candidate_count": 5, "ready_count": 5, "gap_count": 0},
        after_counts={"candidate_count": 5, "ready_count": 5, "gap_count": 0},
        target_count=0,
        succeeded_count=0,
        unresolved_count=0,
        failed_count=0,
    )

    assert result["candidate_coverage_rate_before"] == 1.0
    assert result["candidate_coverage_rate_after"] == 1.0
    assert result["candidate_coverage_rate_delta"] == 0.0
    assert result["remediation_coverage_rate"] == 1.0


def test_local_pool_gap_comparison_uses_actual_gap_delta_for_resolution():
    result = _local_pool_gap_comparison(
        before_counts={"candidate_count": 1200, "ready_count": 0, "gap_count": 1199},
        after_counts={"candidate_count": 1200, "ready_count": 280, "gap_count": 918},
        target_count=1199,
        succeeded_count=280,
        unresolved_count=1,
        failed_count=0,
    )

    assert result["resolved_target_count"] == 281
    assert result["unresolved_target_count"] == 918
    assert result["remediation_coverage_rate"] == 0.234


def test_natural_short_history_requires_latest_bar_and_listing_start():
    assert _is_natural_short_history(
        bar_count=22,
        latest_bar_date="2026-05-28",
        quote_date="2026-05-28",
        first_bar_date="2026-04-24",
        list_date="2026-04-24",
    )
    assert not _is_natural_short_history(
        bar_count=22,
        latest_bar_date="2026-05-20",
        quote_date="2026-05-28",
        first_bar_date="2026-04-24",
        list_date="2026-04-24",
    )
    assert not _is_natural_short_history(
        bar_count=22,
        latest_bar_date="2026-05-28",
        quote_date="2026-05-28",
        first_bar_date="2026-05-01",
        list_date="2026-04-24",
    )


def test_backfill_news_announcements_rate_limits_and_aborts_on_many_failures(monkeypatch):
    """L-044：东方财富主路径连续失败也必须 sleep + 在 MAX_FAILURES 处中止。"""
    import app.services.data_gap_remediation_service as dgr
    import app.services.eastmoney_news as em

    sleep_calls = {"n": 0}

    def fake_sleep(_s):
        sleep_calls["n"] += 1

    monkeypatch.setattr(dgr.time, "sleep", fake_sleep)
    monkeypatch.setattr(em.time, "sleep", fake_sleep)

    def boom(*_a, **_k):
        raise RuntimeError("fetch failed")

    monkeypatch.setattr(em, "fetch_announcements_for_code", boom)
    monkeypatch.setattr(dgr, "get_pro", lambda: type("P", (), {"anns_d": lambda **kw: None})())

    codes = [f"{i:06d}" for i in range(500)]
    result = _backfill_news_announcements(
        MagicMock(), trade_date="2026-07-30", codes=codes,
    )
    assert result["primary_source"] == "eastmoney"
    assert result["failed_code_count"] < 500
    assert result["failed_code_count"] <= 200
    assert sleep_calls["n"] >= result["failed_code_count"]


def test_backfill_news_announcements_uses_eastmoney_primary(monkeypatch):
    import app.services.data_gap_remediation_service as dgr
    import app.services.eastmoney_news as em

    monkeypatch.setattr(dgr.time, "sleep", lambda *_: None)
    monkeypatch.setattr(em.time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        em,
        "fetch_announcements_for_code",
        lambda code, **kw: [{
            "source": "eastmoney",
            "article_id": f"AN-{code}",
            "code": code,
            "announce_date": "2026-10-14",
            "title": "关于收到监管措施决定书的公告",
            "summary": None,
            "category": "announcement",
            "sentiment": "negative",
            "is_risk_warning": True,
        }],
    )
    db = MagicMock()
    result = _backfill_news_announcements(db, trade_date="2026-10-14", codes=["000001"])
    assert result["row_count"] == 1
    assert result["eastmoney_ok_codes"] == 1
    assert result["tushare_fallback_used"] is False
    assert db.execute.called
    assert db.commit.called


def test_backfill_news_announcements_falls_back_to_tushare_when_eastmoney_blocked(monkeypatch):
    import app.services.data_gap_remediation_service as dgr
    import app.services.eastmoney_news as em
    import app.services.cninfo_news as cn
    import pandas as pd

    monkeypatch.setattr(dgr.time, "sleep", lambda *_: None)
    monkeypatch.setattr(em.time, "sleep", lambda *_: None)
    monkeypatch.setattr(cn.time, "sleep", lambda *_: None)

    def blocked(*_a, **_k):
        raise em.EastmoneyBlockedError("HTTP 567")

    monkeypatch.setattr(em, "fetch_announcements_for_code", blocked)
    monkeypatch.setattr(cn, "fetch_announcements_for_code", lambda *a, **k: [])  # cninfo 也空 → 落到 tushare

    frame = pd.DataFrame([{
        "ts_code": "000001.SZ",
        "ann_date": "20261014",
        "title": "普通公告",
        "url": "http://x",
        "ann_type": "公告",
    }])
    class _Pro:
        def anns_d(self, **kw):
            return frame

    monkeypatch.setattr(dgr, "get_pro", lambda: _Pro())
    db = MagicMock()
    result = _backfill_news_announcements(db, trade_date="2026-10-14", codes=["000001", "000002"])
    assert result["eastmoney_blocked"] is True
    assert result["tushare_fallback_used"] is True
    assert result["row_count"] >= 1


def test_backfill_news_announcements_uses_cninfo_when_eastmoney_blocked(monkeypatch):
    import app.services.data_gap_remediation_service as dgr
    import app.services.eastmoney_news as em
    import app.services.cninfo_news as cn

    monkeypatch.setattr(dgr.time, "sleep", lambda *_: None)
    monkeypatch.setattr(em.time, "sleep", lambda *_: None)
    monkeypatch.setattr(cn.time, "sleep", lambda *_: None)
    monkeypatch.setattr(em, "fetch_announcements_for_code", lambda *a, **k: (_ for _ in ()).throw(em.EastmoneyBlockedError("567")))
    monkeypatch.setattr(
        cn,
        "fetch_announcements_for_code",
        lambda code, **kw: [{
            "source": "cninfo", "article_id": f"C-{code}", "code": code,
            "announce_date": "2026-10-14", "title": "巨潮公告", "summary": None,
            "category": "announcement", "sentiment": "neutral", "is_risk_warning": False,
        }],
    )
    monkeypatch.setattr(dgr, "get_pro", lambda: type("P", (), {"anns_d": lambda self, **kw: None})())
    db = MagicMock()
    result = _backfill_news_announcements(db, trade_date="2026-10-14", codes=["000001"])
    assert result["eastmoney_blocked"] is True
    assert result["cninfo_ok_codes"] == 1
    assert result["row_count"] == 1
