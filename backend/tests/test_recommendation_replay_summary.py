from __future__ import annotations

from app.services.workbench_service import _build_replay_summary


def test_build_replay_summary_merges_outcome_review_and_signal_context():
    detail = {
        "recommended_at": "2026-05-12T14:45:00",
        "trade_date": "2026-05-12",
        "code": "600519",
        "name": "贵州茅台",
        "recommend_price": 100.0,
        "entry_rule": "回踩不破 5 日线",
        "setup_type": "趋势突破",
        "final_score": 82.0,
        "feature_snapshot": {
            "market_regime": "active",
            "trend_status": "up",
            "volume_ratio_5d": 1.5,
            "score_line_meta": {
                "effective_score_line": 72.0,
                "reasons": ["市场加分 +2", "证据加分 +1"],
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
            "review_text": "开盘后强转弱，不适合追。",
        },
    }

    result = _build_replay_summary(detail)

    assert result["recommend_time"] == "2026-05-12T14:45:00"
    assert result["symbol"] == "600519"
    assert result["name"] == "贵州茅台"
    assert result["entry_time"] == "2026-05-13"
    assert result["exit_time"] == "2026-05-16"
    assert result["entry_price"] == 100.2
    assert result["exit_price"] == 97.8
    assert result["net_return_pct"] == -2.4
    assert result["false_positive_reason"] == "竞价后承接转弱"
    assert result["false_positive_category"] == "weak_followthrough"
    assert result["false_positive_category_label"] == "承接转弱"
    assert result["false_positive_stage"] == "signal"
    assert result["review_failure_reason"] == "竞价后承接转弱"
    assert result["outcome_reason"] == "次日承接不足，跌破止损"
    assert result["effective_score_line"] == 72.0
    assert result["final_score"] == 82.0
    assert result["trigger_conditions"][:2] == ["回踩不破 5 日线", "趋势突破"]
    assert "市场加分 +2" in result["trigger_conditions"]
    assert result["signal_features"]["market_regime"] == "active"
