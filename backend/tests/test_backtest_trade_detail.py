"""
验证 backtest_service._build_trade_detail_list 的字段完整性与正确性。

检查项：
1. 有交易数据时返回正确的字段集合
2. 无推荐时返回空列表
3. 每笔交易的字段完整性（推荐日期/标的/入场价/出场价/净收益/触发条件/误判原因）
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


_FULL_FIELDS = {
    "recommendation_id", "trade_date", "code", "name", "industry",
    "entry_date", "entry_price", "exit_date", "exit_price",
    "net_return_pct", "gross_return_pct", "achievable_return_pct",
    "benchmark_return_pct", "morning_drawdown_pct", "holding_days",
    "setup_type", "entry_rule", "exit_reason", "final_score",
    "accurate", "signal_valid_at_recommend_time", "executed", "net_return_positive",
    "false_positive_reason", "signal_features", "resolved",
}


def _make_scalars_result(rows):
    """模拟 db.scalars(…).all() 链式调用。"""
    m = MagicMock()
    m.all.return_value = rows
    return m


def _mock_rec(code: str, trade_date: str, recommendation_id: str, run_id: str,
              setup_type: str = "尾盘买入", entry_rule: str = "14:30后站上均线",
              final_score: float = 75.0) -> MagicMock:
    rec = MagicMock()
    rec.recommendation_id = recommendation_id
    rec.run_id = run_id
    rec.trade_date = trade_date
    rec.code = code
    rec.name = f"{code}名称"
    rec.industry = "测试行业"
    rec.setup_type = setup_type
    rec.entry_rule = entry_rule
    rec.final_score = final_score
    rec.feature_snapshot_json = (
        '{"market_regime": "震荡", "trend_status": "上升", "volume_ratio_5d": 1.2}'
    )
    return rec


def _mock_outcome(rec_id: str, *, entry_triggered: bool = True,
                  executed_return: float = 2.5, entry_price: float = 10.0,
                  exit_price: float = 10.25, entry_date: str = "2026-05-19",
                  exit_date: str = "2026-05-25", outcome_reason: str | None = None,
                  exit_reason: str = "next_morning_exit") -> MagicMock:
    outcome = MagicMock()
    outcome.recommendation_id = rec_id
    outcome.entry_triggered = entry_triggered
    outcome.executed_return = executed_return
    outcome.close_return = executed_return + 0.3
    outcome.achievable_return = executed_return + 0.5
    outcome.max_drawdown = -1.0
    outcome.holding_days = 5
    outcome.entry_price = entry_price
    outcome.exit_price = exit_price
    outcome.entry_date = entry_date
    outcome.exit_date = exit_date
    outcome.exit_reason = exit_reason
    outcome.outcome_reason = outcome_reason
    return outcome


@pytest.fixture(autouse=True)
def _patch_deps():
    with patch("app.services.benchmark_service.get_benchmark_close_map", return_value={}):
        with patch("app.services.benchmark_service.compute_benchmark_return", return_value=0.0):
            yield


def _build_trade_detail_list(db, start, end, run_ids=None):
    from app.services.backtest_service import _build_trade_detail_list as fn
    return fn(db, start, end, run_ids)


def test_build_trade_detail_fields_complete():
    """返回的每笔交易应包含所有必要字段。"""
    run_id = "bt_test_001"
    recs = [
        _mock_rec("000001", "2026-05-18", "rec_1", run_id),
        _mock_rec("000002", "2026-05-18", "rec_2", run_id),
    ]
    outcomes = [
        _mock_outcome("rec_1", executed_return=2.5),
        _mock_outcome("rec_2", executed_return=-0.5, outcome_reason="次日开盘承接偏弱"),
    ]

    db = MagicMock()
    db.scalars.side_effect = [
        _make_scalars_result(recs),
        _make_scalars_result(outcomes),
    ]

    details = _build_trade_detail_list(db, "2026-05-18", "2026-05-25")

    assert len(details) == 2, "应有2笔交易"

    for i, detail in enumerate(details):
        missing = _FULL_FIELDS - set(detail.keys())
        assert not missing, f"交易 {i} 缺少字段: {missing}"

    # 验证字段内容
    detail = details[0]
    assert detail["trade_date"] == "2026-05-18"
    assert detail["code"] == "000001"
    assert detail["entry_price"] == 10.0
    assert detail["exit_price"] == 10.25
    assert detail["net_return_pct"] == 2.5
    assert detail["setup_type"] == "尾盘买入"
    assert detail["exit_reason"] == "next_morning_exit"
    assert detail["accurate"] is True
    assert detail["signal_valid_at_recommend_time"] is True
    assert detail["executed"] is True
    assert detail["net_return_positive"] is True
    assert detail["false_positive_reason"] is None
    assert detail["resolved"] is True


def test_build_trade_detail_no_recommendations():
    """无推荐时应返回空列表。"""
    db = MagicMock()
    db.scalars.return_value = _make_scalars_result([])
    details = _build_trade_detail_list(db, "2026-01-01", "2026-01-31")
    assert details == []


def test_build_trade_detail_no_executed_trades():
    """有推荐但无已执行交易时应返回空列表。"""
    run_id = "bt_test_002"
    recs = [
        _mock_rec("000001", "2026-05-18", "rec_1", run_id),
    ]
    outcomes = [
        _mock_outcome("rec_1", entry_triggered=False),
    ]

    db = MagicMock()
    db.scalars.side_effect = [
        _make_scalars_result(recs),
        _make_scalars_result(outcomes),
    ]

    details = _build_trade_detail_list(db, "2026-05-18", "2026-05-25")
    assert details == []


def test_build_trade_detail_false_positive_trade():
    """亏损交易应正确携带误判原因。"""
    run_id = "bt_test_003"
    recs = [
        _mock_rec("000003", "2026-05-18", "rec_fp", run_id, setup_type="开盘追涨"),
    ]
    outcomes = [
        _mock_outcome(
            "rec_fp", executed_return=-1.2, exit_price=9.88,
            outcome_reason="次日开盘承接偏弱，隔夜溢价被回吐",
            exit_reason="next_morning_exit",
        ),
    ]

    db = MagicMock()
    db.scalars.side_effect = [
        _make_scalars_result(recs),
        _make_scalars_result(outcomes),
    ]

    details = _build_trade_detail_list(db, "2026-05-18", "2026-05-25")

    assert len(details) == 1
    detail = details[0]
    assert detail["accurate"] is False
    assert detail["signal_valid_at_recommend_time"] is True
    assert detail["executed"] is True
    assert detail["net_return_positive"] is False
    assert detail["false_positive_reason"] == "次日开盘承接偏弱，隔夜溢价被回吐"
    assert detail["net_return_pct"] == -1.2
    assert detail["signal_features"]["market_regime"] == "震荡"


def test_persist_backtest_recommendations_keeps_execution_viability_snapshot():
    from app.services.backtest_service import _persist_backtest_recommendations

    db = MagicMock()
    card = {
        "code": "000001",
        "name": "测试股份",
        "trade_date": "2026-05-18",
        "recommend_price": 11.0,
        "stop_loss": 10.4,
        "take_profit": 12.2,
        "final_score": 88.0,
        "consensus_score": 82.0,
        "feature_snapshot": {"market_regime": "偏多·活跃"},
        "score_line_meta": {"execution_blocked": True},
        "execution_viability": {
            "status": "blocked",
            "label": "入场不可成交",
            "block_reason": "入场日一字涨停，按真实成交约束视为买不进",
        },
    }

    _persist_backtest_recommendations(db, "bt_execution_snapshot", [card], {"trade_date": "2026-05-18"})

    saved = db.add.call_args.args[0]
    snapshot = json.loads(saved.feature_snapshot_json)
    assert snapshot["market_regime"] == "偏多·活跃"
    assert snapshot["recommend_price"] == 11.0
    assert snapshot["score_line_meta"]["execution_blocked"] is True
    assert snapshot["execution_viability"]["status"] == "blocked"
    assert "一字涨停" in snapshot["execution_viability"]["block_reason"]


def test_backtest_recommendation_records_expose_accuracy_gates():
    from app.services.backtest_service import _backtest_false_positive_samples, _backtest_recommendation_records

    trades = [
        {
            "trade_date": "2026-05-18",
            "code": "000001",
            "name": "赢家",
            "entry_date": "2026-05-19",
            "entry_price": 10.0,
            "exit_date": "2026-05-20",
            "exit_price": 10.5,
            "net_return_pct": 5.0,
            "gross_return_pct": 5.2,
            "benchmark_return_pct": 1.0,
            "accurate": True,
            "signal_valid_at_recommend_time": True,
            "executed": True,
            "net_return_positive": True,
            "exit_reason": "take_profit",
            "false_positive_reason": None,
            "resolved": True,
        },
        {
            "trade_date": "2026-05-18",
            "code": "000002",
            "name": "未成交",
            "entry_date": "2026-05-19",
            "entry_price": 10.0,
            "exit_date": "2026-05-20",
            "exit_price": 10.0,
            "net_return_pct": 0.0,
            "gross_return_pct": 0.3,
            "benchmark_return_pct": 0.2,
            "accurate": False,
            "signal_valid_at_recommend_time": True,
            "executed": False,
            "net_return_positive": False,
            "exit_reason": "missed_entry",
            "false_positive_reason": "未触发买点或不可真实成交",
            "resolved": True,
        },
        {
            "trade_date": "2026-05-18",
            "code": "000003",
            "name": "信号失效",
            "entry_date": "2026-05-19",
            "entry_price": 10.0,
            "exit_date": "2026-05-20",
            "exit_price": 9.8,
            "net_return_pct": -2.0,
            "gross_return_pct": -1.6,
            "benchmark_return_pct": 0.0,
            "accurate": False,
            "signal_valid_at_recommend_time": False,
            "executed": True,
            "net_return_positive": False,
            "exit_reason": "stop_loss",
            "false_positive_reason": "开盘即失效",
            "resolved": True,
        },
    ]

    records = _backtest_recommendation_records(trades)
    failures = _backtest_false_positive_samples(trades)

    assert records[0]["accuracy_failure_label"] is None
    assert records[1]["accuracy_failure_label"] == "未触发买点或不可真实成交"
    assert records[2]["accuracy_failure_label"] == "信号当时真实不成立"
    assert failures[0]["code"] == "000003"
    assert failures[0]["accuracy_failure_label"] == "信号当时真实不成立"


def test_backtest_horizon_return_matrix_groups_by_strategy_and_trading_day():
    from app.services.backtest_service import _backtest_horizon_return_matrix

    def bar(code: str, trade_date: str, close: float):
        item = MagicMock()
        item.code = code
        item.trade_date = trade_date
        item.close = close
        return item

    rec_1 = _mock_rec("000001", "2026-05-18", "rec_1", "run_1")
    rec_1.recommend_price = 10.0
    rec_1.skill_breakdown_json = json.dumps([
        {"strategy_id": "dragon", "strategy_name": "龙头策略"},
    ])
    rec_2 = _mock_rec("000002", "2026-05-18", "rec_2", "run_1")
    rec_2.recommend_price = 20.0
    rec_2.skill_breakdown_json = json.dumps([
        {"strategy_id": "dragon", "strategy_name": "龙头策略"},
        {"strategy_id": "relay", "strategy_name": "接力策略"},
    ])
    bars = [
        bar("000001", "2026-05-18", 10.0),
        bar("000001", "2026-05-19", 11.0),
        bar("000001", "2026-05-20", 12.0),
        bar("000001", "2026-05-21", 13.0),
        bar("000002", "2026-05-18", 20.0),
        bar("000002", "2026-05-19", 18.0),
        bar("000002", "2026-05-20", 22.0),
        bar("000002", "2026-05-21", 24.0),
    ]
    db = MagicMock()
    db.scalars.side_effect = [
        _make_scalars_result([rec_1, rec_2]),
        _make_scalars_result(bars),
    ]

    matrix = _backtest_horizon_return_matrix(
        db,
        "2026-05-18",
        "2026-05-21",
        run_ids={"run_1"},
        horizons=(1, 3),
    )

    by_strategy = {row["strategy_id"]: row for row in matrix}
    assert by_strategy["dragon"]["strategy_name"] == "龙头策略"
    assert by_strategy["dragon"]["recommendation_count"] == 2
    assert by_strategy["dragon"]["horizons"]["1"]["sample_count"] == 2
    assert by_strategy["dragon"]["horizons"]["1"]["average_return_pct"] == pytest.approx(0.0)
    assert by_strategy["dragon"]["horizons"]["3"]["average_return_pct"] == pytest.approx(25.0)
    assert by_strategy["relay"]["recommendation_count"] == 1
    assert by_strategy["relay"]["horizons"]["1"]["average_return_pct"] == pytest.approx(-10.0)
