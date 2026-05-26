from datetime import datetime

from app.models.tables import BacktestRun
from app.services.backtest_history_service import _serialize_backtest_run


def test_serialize_backtest_run_summarizes_metrics_and_data_source():
    row = BacktestRun(
        backtest_id="bt_overnight_001",
        skill_id="overnight-holding-method",
        date_from="2026-05-14",
        date_to="2026-05-22",
        status="completed",
        created_at=datetime(2026, 5, 26, 10, 30),
        result_json="""{
          "strategy_name": "隔夜持股法",
          "aggregate_metrics": {
            "trade_count": 2,
            "recommendation_accuracy": 1.0,
            "average_net_return": 0.314,
            "median_net_return": 0.314,
            "profit_loss_ratio": 10.0,
            "max_drawdown": 0.0,
            "positive_month_ratio": 1.0,
            "benchmark_excess_return": -1.298,
            "sample_confidence": "low"
          },
          "comparison": {
            "selected_variant": "baseline",
            "selected_variant_label": "基线版",
            "adoption_reason": "增强版样本外优势不足"
          },
          "data_coverage": {
            "signal_date_count": 7,
            "candidate_count": 19,
            "candidate_minute_coverage_rate": 1.0,
            "benchmark_minute_coverage_rate": 0.833,
            "minute_source_diagnostics": {
              "tushare_stk_mins_status": "permission_denied",
              "tushare_permission_denied": true,
              "fallback_used": true
            }
          }
        }""",
    )

    summary = _serialize_backtest_run(row, include_result=False)

    assert summary["strategy_name"] == "隔夜持股法"
    assert summary["metrics"]["recommendation_accuracy"] == 1.0
    assert summary["metrics"]["average_net_return"] == 0.314
    assert summary["selected_variant"] == "baseline"
    assert summary["data_coverage"]["candidate_minute_coverage_rate"] == 1.0
    assert summary["data_coverage"]["tushare_permission_denied"] is True
    assert "result" not in summary


def test_serialize_backtest_run_includes_full_result_for_detail():
    row = BacktestRun(
        backtest_id="bt_overnight_002",
        skill_id="overnight-holding-method",
        date_from="2026-05-14",
        date_to="2026-05-22",
        status="completed",
        result_json='{"status":"completed","aggregate_metrics":{"trade_count":1,"recommendation_accuracy":1.0}}',
    )

    detail = _serialize_backtest_run(row, include_result=True)

    assert detail["metrics"]["trade_count"] == 1
    assert detail["result"]["status"] == "completed"
