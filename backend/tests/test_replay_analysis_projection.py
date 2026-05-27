from __future__ import annotations

from app.services.workbench_service import _build_replay_analysis_row, _summarize_replay_analysis_rows


def test_build_replay_analysis_row_marks_false_positive_and_flattens_context():
    detail = {
        "recommendation_id": "rec_001",
        "run_id": "run_001",
        "trade_date": "2026-05-12",
        "recommended_at": "2026-05-12T14:45:00",
        "code": "600519",
        "name": "贵州茅台",
        "final_score": 82.0,
        "entry_rule": "回踩不破 5 日线",
        "setup_type": "趋势突破",
        "feature_snapshot": {
            "market_regime": "active",
            "trend_status": "up",
            "concept_sector_name": "白酒",
            "score_line_meta": {
                "effective_score_line": 72.0,
                "reasons": ["市场加分 +2"],
            },
        },
        "outcome": {
            "entry_date": "2026-05-13",
            "entry_price": 100.2,
            "exit_date": "2026-05-16",
            "exit_price": 97.8,
            "executed_return": -2.4,
            "outcome_label": "failed",
            "outcome_reason": "次日承接不足，跌破止损",
            "exit_reason": "stop_loss",
            "holding_days": 3,
        },
        "review_note": {
            "user_label": "false_positive",
            "failure_reason": "竞价后承接转弱",
        },
    }

    row = _build_replay_analysis_row(detail)

    assert row["recommendation_id"] == "rec_001"
    assert row["is_false_positive"] is True
    assert row["false_positive_reason"] == "竞价后承接转弱"
    assert row["false_positive_category"] == "weak_followthrough"
    assert row["false_positive_category_label"] == "承接转弱"
    assert row["false_positive_stage"] == "signal"
    assert row["trigger_condition_text"].startswith("回踩不破 5 日线")
    assert row["market_regime"] == "active"
    assert row["concept_sector_name"] == "白酒"
    assert row["effective_score_line"] == 72.0


def test_summarize_replay_analysis_rows_aggregates_false_positive_breakdown():
    rows = [
        {
            "recommendation_id": "rec_001",
            "net_return_pct": -2.4,
            "is_false_positive": True,
            "false_positive_reason": "竞价后承接转弱",
            "false_positive_category": "weak_followthrough",
            "false_positive_category_label": "承接转弱",
            "false_positive_stage": "signal",
            "user_label": "false_positive",
            "outcome_label": "failed",
            "market_regime": "active",
        },
        {
            "recommendation_id": "rec_002",
            "net_return_pct": 1.2,
            "is_false_positive": False,
            "false_positive_reason": None,
            "false_positive_category": None,
            "false_positive_category_label": None,
            "false_positive_stage": None,
            "user_label": "true_positive",
            "outcome_label": "success",
            "market_regime": "active",
        },
        {
            "recommendation_id": "rec_003",
            "net_return_pct": -0.8,
            "is_false_positive": True,
            "false_positive_reason": "大盘分歧放大",
            "false_positive_category": "market_context",
            "false_positive_category_label": "市场环境拖累",
            "false_positive_stage": "context",
            "user_label": None,
            "outcome_label": "failed",
            "market_regime": "weak",
        },
    ]

    summary = _summarize_replay_analysis_rows(rows)

    assert summary["total_count"] == 3
    assert summary["false_positive_count"] == 2
    assert summary["false_positive_rate"] == 0.667
    assert summary["average_net_return"] == -0.667
    assert summary["reason_counts"][0]["count"] == 1
    assert {item["category"]: item["count"] for item in summary["category_counts"]} == {
        "weak_followthrough": 1,
        "market_context": 1,
    }
    assert {item["stage"]: item["count"] for item in summary["stage_counts"]} == {"signal": 1, "context": 1}
    assert {item["market_regime"]: item["count"] for item in summary["market_regime_counts"]} == {"active": 2, "weak": 1}
