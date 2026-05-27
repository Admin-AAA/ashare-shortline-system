from datetime import datetime

from app.models.tables import BacktestRun
from app.services.backtest_history_service import _serialize_backtest_run, get_backtest_version_matrix


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
            "accuracy_numerator": 2,
            "accuracy_denominator": 2,
            "trade_accuracy": 1.0,
            "executed_trade_count": 2,
            "unexecuted_count": 0,
            "average_net_return": 0.314,
            "average_executed_return": 0.314,
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
            "adoption_reason": "增强版样本外优势不足",
            "decision_scope": "walk_forward",
            "decision_scope_label": "滚动样本外验证",
            "guardrail_notes": ["样本低于30笔，按低置信度处理。"],
            "decision_metrics": {
              "baseline": {"trade_count": 12, "recommendation_accuracy": 0.58, "accuracy_denominator": 12, "trade_accuracy": 0.70, "executed_trade_count": 10, "unexecuted_count": 2, "average_net_return": 0.21, "max_drawdown": -1.3, "sample_confidence": "low"},
              "candidate": {"trade_count": 15, "recommendation_accuracy": 0.55, "accuracy_denominator": 15, "trade_accuracy": 0.65, "executed_trade_count": 12, "unexecuted_count": 3, "average_net_return": 0.18, "max_drawdown": -3.9, "sample_confidence": "low"}
            },
            "validation_profile": {
              "primary_scope": "walk_forward",
              "primary_scope_label": "滚动样本外验证",
              "fallback_reason": null,
              "primary_samples": {
                "baseline_trade_count": 12,
                "candidate_trade_count": 15,
                "baseline_confidence": "low",
                "candidate_confidence": "low"
              },
              "walk_forward": {
                "baseline": {"eligible": true, "window_count": 3, "trade_count": 12, "sample_confidence": "low"},
                "candidate": {"eligible": true, "window_count": 3, "trade_count": 15, "sample_confidence": "low"}
              },
              "time_split": {
                "baseline": {"eligible": true, "test_trade_count": 8},
                "candidate": {"eligible": true, "test_trade_count": 10}
              }
            }
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
    assert summary["metrics"]["accuracy_denominator"] == 2
    assert summary["metrics"]["trade_accuracy"] == 1.0
    assert summary["metrics"]["unexecuted_count"] == 0
    assert summary["metrics"]["average_net_return"] == 0.314
    assert summary["metrics"]["average_executed_return"] == 0.314
    assert summary["selected_variant"] == "baseline"
    assert summary["decision_scope"] == "walk_forward"
    assert summary["decision_scope_label"] == "滚动样本外验证"
    assert summary["guardrail_notes"] == ["样本低于30笔，按低置信度处理。"]
    assert summary["decision_metrics"]["candidate"]["max_drawdown"] == -3.9
    assert summary["decision_metrics"]["candidate"]["accuracy_denominator"] == 15
    assert summary["decision_metrics"]["candidate"]["trade_accuracy"] == 0.65
    assert summary["validation_profile"]["primary_scope"] == "walk_forward"
    assert summary["validation_profile"]["primary_samples"]["candidate_trade_count"] == 15
    assert summary["validation_profile"]["walk_forward"]["baseline"]["window_count"] == 3
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


def test_get_backtest_version_matrix_extracts_baseline_candidate_deltas():
    class _FakeScalarResult:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class _FakeDb:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self, stmt):
            return _FakeScalarResult(self._rows)

    row = BacktestRun(
        backtest_id="bt_cmp_001",
        skill_id="_general__comparison",
        date_from="2026-05-01",
        date_to="2026-05-20",
        status="completed",
        created_at=datetime(2026, 5, 26, 10, 30),
        result_json="""{
          "strategy_name": "通用推荐流水线比较",
          "comparison": {
            "baseline_label": "基线版(score=72)",
            "candidate_label": "候选版(score=65)",
            "selected_variant": "baseline",
            "selected_variant_label": "基线版(score=72)",
            "decision_scope": "walk_forward",
            "decision_scope_label": "滚动样本外验证",
            "guardrail_notes": ["命中回撤护栏"],
            "adoption_reason": "候选版回撤恶化",
            "decision_metrics": {
              "baseline": {"recommendation_accuracy": 0.58, "accuracy_denominator": 12, "trade_accuracy": 0.70, "executed_trade_count": 10, "unexecuted_count": 2, "average_net_return": 0.22, "max_drawdown": -1.3, "sample_confidence": "low"},
              "candidate": {"recommendation_accuracy": 0.61, "accuracy_denominator": 15, "trade_accuracy": 0.64, "executed_trade_count": 11, "unexecuted_count": 4, "average_net_return": 0.34, "max_drawdown": -3.9, "sample_confidence": "low"}
            },
            "validation_profile": {
              "primary_scope": "walk_forward",
              "primary_scope_label": "滚动样本外验证",
              "fallback_reason": null
            }
          }
        }""",
    )

    matrix = get_backtest_version_matrix(_FakeDb([row]), skill_id="_general__comparison", limit=10)

    assert len(matrix) == 1
    item = matrix[0]
    assert item["selected_variant"] == "baseline"
    assert item["decision_scope_label"] == "滚动样本外验证"
    assert item["accuracy_delta"] == 0.03
    assert item["baseline_accuracy_denominator"] == 12
    assert item["candidate_accuracy_denominator"] == 15
    assert item["trade_accuracy_delta"] == -0.06
    assert item["candidate_unexecuted_count"] == 4
    assert item["return_delta"] == 0.12
    assert item["drawdown_delta"] == -2.6
    assert item["validation_profile"]["primary_scope"] == "walk_forward"
    assert item["guardrail_notes"] == ["命中回撤护栏"]
