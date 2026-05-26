"""
集成测试：统一评估指标框架 + 回测结果持久化。

基线 (Baseline)：旧的 _backtest_aggregate_metrics 只输出 partial metrics (无 9-KPI)
候选 (Candidate)：新框架通过 evaluation_metrics.compute_full_metrics 输出完整 9-KPI
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

from app.models.tables import BacktestRun, Recommendation, RecommendationOutcome, RecommendationRun
from app.services.evaluation_metrics import (
    MIN_SIGNAL_SAMPLE,
    compute_full_metrics,
    _empty_metrics,
    _positive_month_ratio,
    _equity_max_drawdown,
    _compound_return,
    _false_positive_notes,
)


# ═════════════════════════════════════════════════════════════════════════════
# 测试 1: compute_full_metrics 核心算法
# ═════════════════════════════════════════════════════════════════════════════

class TestComputeFullMetrics:
    """验证 9-KPI 核心计算逻辑。"""

    def test_empty_trades_returns_all_zeros(self):
        result = compute_full_metrics([])
        assert result["trade_count"] == 0
        assert result["recommendation_accuracy"] == 0.0
        assert result["sample_confidence"] == "low"
        assert result["false_positive_notes"] == []

    def test_single_winning_trade(self):
        trades = [{
            "net_return_pct": 5.0,
            "gross_return_pct": 5.5,
            "achievable_return_pct": 6.0,
            "morning_drawdown_pct": -1.0,
            "holding_days": 3.0,
            "benchmark_return_pct": 2.0,
            "accurate": True,
            "trade_date": "2026-05-22",
            "code": "000001",
            "name": "平安银行",
            "false_positive_reason": None,
            "resolved": True,
        }]
        result = compute_full_metrics(trades)
        assert result["trade_count"] == 1
        assert result["recommendation_accuracy"] == 1.0
        assert result["average_net_return"] == 5.0
        assert result["median_net_return"] == 5.0
        # 无亏损交易时 profit_loss_ratio 极高（分母为 0.001 防止除零）
        assert result["profit_loss_ratio"] > 100
        assert result["benchmark_excess_return"] == 3.0  # 5% - 2%
        assert result["sample_confidence"] == "low"

    def test_mixed_trades_9kpi_correctness(self):
        """用 10 笔交易（5 赢 5 输）验证全部 9 个 KPI 的数值正确性。"""
        trades = []
        for i in range(5):
            trades.append({
                "net_return_pct": 4.0,
                "gross_return_pct": 4.5,
                "achievable_return_pct": 5.0,
                "morning_drawdown_pct": -0.5,
                "holding_days": 3.0,
                "benchmark_return_pct": 1.0,
                "accurate": True,
                "trade_date": f"2026-05-{19+i:02d}",
                "code": f"00000{i}",
                "name": f"赢家{i}",
                "false_positive_reason": None,
                "resolved": True,
            })
        for i in range(5):
            trades.append({
                "net_return_pct": -2.0,
                "gross_return_pct": -1.5,
                "achievable_return_pct": 0.0,
                "morning_drawdown_pct": -3.0,
                "holding_days": 2.0,
                "benchmark_return_pct": -0.5,
                "accurate": False,
                "trade_date": f"2026-05-{14+i:02d}",
                "code": f"1111{i}",
                "name": f"输家{i}",
                "false_positive_reason": "开盘走弱",
                "resolved": True,
            })
        result = compute_full_metrics(trades)

        # 9-KPI 验证
        assert result["trade_count"] == 10
        assert result["recommendation_accuracy"] == 0.5          # 5/10
        assert result["average_net_return"] == 1.0               # (20-10)/10
        assert result["median_net_return"] == pytest.approx(1.0, rel=0.01)  # 中位数
        assert result["profit_loss_ratio"] == 2.0                # 4.0/2.0
        assert result["max_drawdown"] < 0                        # 有回撤
        assert result["positive_month_ratio"] == 1.0             # 单月累计 > 0
        assert result["benchmark_excess_return"] > 0             # 策略跑赢基准
        assert result["sample_confidence"] == "low"              # 10 < 30

        # 误判原因
        fp_notes = result["false_positive_notes"]
        assert len(fp_notes) == 1
        assert fp_notes[0]["reason"] == "开盘走弱"
        assert fp_notes[0]["count"] == 5

    def test_30_trades_is_normal_confidence(self):
        """trade_count >= 30 → normal confidence。"""
        trades = [{
            "net_return_pct": 1.0,
            "gross_return_pct": 1.0,
            "accurate": True,
            "trade_date": "2026-05-{:02d}".format(d),
            "code": f"00{d:04d}",
            "name": f"股票{d}",
            "resolved": True,
        } for d in range(1, 30)]
        result = compute_full_metrics(trades)
        assert result["sample_confidence"] == "low"  # 29 < 30

        # 第 30 笔
        trades.append({
            "net_return_pct": 1.0, "gross_return_pct": 1.0,
            "accurate": True, "trade_date": "2026-05-30", "code": "003000", "name": "股票30",
            "resolved": True,
        })
        result = compute_full_metrics(trades)
        assert result["sample_confidence"] == "normal"  # 30 >= 30


# ═════════════════════════════════════════════════════════════════════════════
# 测试 2: 辅助函数
# ═════════════════════════════════════════════════════════════════════════════

class TestHelperFunctions:
    def test_empty_metrics_structure(self):
        m = _empty_metrics()
        assert m["trade_count"] == 0
        assert m["recommendation_accuracy"] == 0.0
        assert m["sample_confidence"] == "low"
        assert m["false_positive_notes"] == []
        # 至少包含 9-KPI 必须字段
        for k in ["trade_count", "recommendation_accuracy", "average_net_return",
                   "median_net_return", "profit_loss_ratio", "max_drawdown",
                   "positive_month_ratio", "benchmark_excess_return", "sample_confidence"]:
            assert k in m

    def test_positive_month_ratio_all_positive(self):
        trades = [
            {"trade_date": "2026-01-15", "net_return_pct": 5.0},
            {"trade_date": "2026-02-10", "net_return_pct": 3.0},
            {"trade_date": "2026-03-20", "net_return_pct": 1.0},
        ]
        assert _positive_month_ratio(trades) == 1.0

    def test_positive_month_ratio_mixed(self):
        trades = [
            {"trade_date": "2026-01-15", "net_return_pct": 5.0},
            {"trade_date": "2026-01-20", "net_return_pct": -10.0},  # same month → net -5
            {"trade_date": "2026-02-10", "net_return_pct": 3.0},
            {"trade_date": "2026-03-20", "net_return_pct": -2.0},
        ]
        # Jan: -5, Feb: +3, Mar: -2 → 1/3 positive
        assert _positive_month_ratio(trades) == pytest.approx(0.333, abs=0.01)

    def test_compound_return(self):
        returns = [10.0, -5.0, 8.0]
        # 1.10 * 0.95 * 1.08 = 1.1286 → 12.86%
        result = _compound_return(returns)
        assert result == pytest.approx(12.86, abs=0.01)

    def test_equity_max_drawdown(self):
        returns = [10.0, -15.0, 20.0, -5.0]
        # 1.10 → 0.935 → 1.122 → 1.066
        # peak = 1.122, dd at end = (1.066-1.122)/1.122 = -4.99%
        mdd = _equity_max_drawdown(returns)
        assert mdd < 0

    def test_false_positive_notes_aggregates_correctly(self):
        trades = [
            {"accurate": True, "code": "A", "trade_date": "01-01", "net_return_pct": 2.0, "false_positive_reason": None},
            {"accurate": False, "code": "B", "trade_date": "01-02", "net_return_pct": -1.0, "false_positive_reason": "开盘走弱"},
            {"accurate": False, "code": "C", "trade_date": "01-03", "net_return_pct": -2.0, "false_positive_reason": "开盘走弱"},
            {"accurate": False, "code": "D", "trade_date": "01-04", "net_return_pct": -0.5, "false_positive_reason": "承接不足"},
        ]
        notes = _false_positive_notes(trades)
        assert len(notes) == 2
        assert notes[0]["reason"] == "开盘走弱"
        assert notes[0]["count"] == 2


# ═════════════════════════════════════════════════════════════════════════════
# 测试 3: 从 ORM → TradeRecord 转换 + persistence
# ═════════════════════════════════════════════════════════════════════════════

class TestBackendIntegration:
    """需要数据库（test env 中的 MySQL 测试库）。"""

    @pytest.fixture(autouse=True)
    def setup_db(self, db: Any):
        self.db = db
        yield
        # 清理测试数据
        self.db.query(BacktestRun).filter(BacktestRun.skill_id == "_test_eval_").delete()
        self.db.query(RecommendationOutcome).filter(
            RecommendationOutcome.recommendation_id.like("_test_rec_%")
        ).delete()
        self.db.query(Recommendation).filter(
            Recommendation.recommendation_id.like("_test_rec_%")
        ).delete()
        self.db.query(RecommendationRun).filter(
            RecommendationRun.run_id.like("_test_run_%")
        ).delete()
        self.db.commit()

    def test_save_backtest_summary_roundtrip(self):
        """验证回测结果被正确持久化并可通过 backtest_history_service 读取。"""
        from app.services.backtest_service import _save_backtest_summary

        fake_metrics = compute_full_metrics([
            {"net_return_pct": 3.0, "gross_return_pct": 3.5, "accurate": True,
             "trade_date": "2026-05-22", "code": "000001", "name": "A", "resolved": True},
            {"net_return_pct": -1.0, "gross_return_pct": -0.5, "accurate": False,
             "trade_date": "2026-05-21", "code": "000002", "name": "B", "resolved": True,
             "false_positive_reason": "开盘走弱"},
        ])
        result = {
            "status": "completed",
            "dates_scanned": 5,
            "total_recommendations": 2,
            "aggregate_metrics": fake_metrics,
        }

        backtest_id = _save_backtest_summary(
            self.db, "_test_eval_", "2026-05-20", "2026-05-22", result,
        )
        assert backtest_id.startswith("bt__test_eval_")

        # 通过 backtest_history_service 读取
        from app.services.backtest_history_service import get_backtest_run, list_backtest_runs
        detail = get_backtest_run(self.db, backtest_id)
        assert detail is not None
        assert detail["skill_id"] == "_test_eval_"
        assert detail["date_from"] == "2026-05-20"
        assert detail["date_to"] == "2026-05-22"

        metrics = detail["metrics"]
        assert metrics["trade_count"] == 2
        assert metrics["recommendation_accuracy"] == 0.5
        assert metrics["average_net_return"] == 1.0
        assert metrics["median_net_return"] == pytest.approx(1.0, abs=0.01)
        assert metrics["max_drawdown"] < 0
        assert metrics["positive_month_ratio"] == 1.0
        assert metrics["sample_confidence"] == "low"

        # 列表查询
        runs = list_backtest_runs(self.db, skill_id="_test_eval_")
        assert any(r["backtest_id"] == backtest_id for r in runs)

    def test_overview_metrics_includes_9kpi(self):
        """验证 outcome_service.overview_metrics 现在返回 9-KPI 字段。"""
        from app.services.outcome_service import overview_metrics

        metrics = overview_metrics(self.db, "formal")
        for k in ["recommendation_accuracy", "average_net_return", "median_net_return",
                   "profit_loss_ratio", "max_drawdown", "positive_month_ratio",
                   "benchmark_excess_return", "sample_confidence", "false_positive_notes"]:
            assert k in metrics, f"缺少 9-KPI 字段: {k}"
