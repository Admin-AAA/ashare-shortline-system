from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.tables import DailyKbar, Stock
from app.services.market_pool.core import _persist_kbars
from app.services.overnight_holding_service import (
    BASELINE_VARIANT,
    CANDIDATE_VARIANT,
    _benchmark_required_dates,
    _choose_variant,
    _build_data_coverage,
    _build_walk_forward_analysis,
    _locate_entry_signal,
    run_overnight_holding_backtest,
)


def _unique_main_board_code(db) -> str:
    base = uuid.uuid4().int % 1000
    for offset in range(1000):
        code = f"003{(base + offset) % 1000:03d}"
        stock_exists = db.scalar(select(Stock.code).where(Stock.code == code).limit(1))
        bar_exists = db.scalar(select(DailyKbar.code).where(DailyKbar.code == code).limit(1))
        if not stock_exists and not bar_exists:
            return code
    raise AssertionError("无法找到未占用的隔夜回测 demo 股票代码")


def _seed_stock(db, code: str):
    db.merge(Stock(code=code, name="隔夜样本", market="主板", industry="银行", is_st=False))
    bars = [
        ("2026-05-12", 10.0, 10.1, 9.9, 10.0, 1000, 10000, 0.0, 6.0),
        ("2026-05-13", 10.0, 10.1, 9.9, 10.0, 1000, 10000, 0.0, 6.0),
        ("2026-05-14", 10.0, 10.1, 9.9, 10.0, 1000, 10000, 0.0, 6.0),
        ("2026-05-15", 10.0, 11.0, 9.9, 11.0, 1000, 10000, 10.0, 6.0),
        ("2026-05-16", 11.0, 11.1, 10.9, 11.0, 1000, 10000, 0.0, 6.0),
        ("2026-05-19", 11.0, 11.5, 10.9, 11.4, 1000, 10000, 4.0, 6.0),
        ("2026-05-20", 11.4, 11.6, 11.2, 11.5, 1000, 10000, 3.5, 6.0),
        ("2026-05-21", 11.5, 11.8, 11.3, 11.9, 1000, 10000, 4.0, 6.0),
        ("2026-05-22", 11.9, 12.0, 11.0, 11.2, 1000, 10000, -5.9, 6.0),
    ]
    _persist_kbars(
        db,
        [
            {
                "code": code,
                "trade_date": trade_date,
                "open": open_p,
                "high": high_p,
                "low": low_p,
                "close": close_p,
                "volume": volume,
                "amount": amount,
                "pct_chg": pct,
                "turnover_rate": turnover,
            }
            for trade_date, open_p, high_p, low_p, close_p, volume, amount, pct, turnover in bars
        ],
    )
    db.commit()


def _load_seeded_window(db, code: str, start_date: str, end_date: str):
    rows = db.scalars(
        select(DailyKbar)
        .where(DailyKbar.code == code, DailyKbar.trade_date >= start_date, DailyKbar.trade_date <= end_date)
        .order_by(DailyKbar.trade_date.asc())
    ).all()
    return (
        {str(row.trade_date): {code: row} for row in rows},
        {code: list(rows)},
    )


def _minute(trade_time: str, close: float, high: float, low: float, volume: float = 1000) -> dict:
    return {
        "trade_time": trade_time,
        "close": close,
        "high": high,
        "low": low,
        "volume": volume,
        "amount": close * volume * 100,
    }


def test_run_overnight_holding_backtest_computes_accuracy(monkeypatch, db):
    code = _unique_main_board_code(db)
    _seed_stock(db, code)

    minute_map = {
        (code, "2026-05-19"): [
            _minute("2026-05-19 14:29:00", 11.30, 11.30, 11.28),
            _minute("2026-05-19 14:30:00", 11.38, 11.39, 11.36),
            _minute("2026-05-19 14:31:00", 11.37, 11.38, 11.35),
        ],
        (code, "2026-05-20"): [
            _minute("2026-05-20 09:35:00", 11.58, 11.60, 11.52),
            _minute("2026-05-20 14:29:00", 11.70, 11.71, 11.68),
            _minute("2026-05-20 14:30:00", 11.75, 11.76, 11.73),
            _minute("2026-05-20 14:31:00", 11.74, 11.75, 11.72),
        ],
        (code, "2026-05-21"): [
            _minute("2026-05-21 09:35:00", 11.88, 11.90, 11.86),
            _minute("2026-05-21 14:29:00", 11.78, 11.78, 11.75),
            _minute("2026-05-21 14:30:00", 11.90, 11.92, 11.88),
            _minute("2026-05-21 14:31:00", 11.89, 11.90, 11.87),
        ],
        (code, "2026-05-22"): [
            _minute("2026-05-22 09:35:00", 11.18, 11.20, 11.15),
        ],
    }
    benchmark_bundle = [
        {"trade_date": "2026-05-19", "trade_time": "2026-05-19 14:29:00", "close": 100.8, "pre_close": 100.0},
        {"trade_date": "2026-05-19", "trade_time": "2026-05-19 14:30:00", "close": 100.9, "pre_close": 100.0},
        {"trade_date": "2026-05-19", "trade_time": "2026-05-19 14:31:00", "close": 100.9, "pre_close": 100.0},
        {"trade_date": "2026-05-20", "trade_time": "2026-05-20 09:35:00", "close": 101.1, "pre_close": 101.0},
        {"trade_date": "2026-05-20", "trade_time": "2026-05-20 14:29:00", "close": 101.2, "pre_close": 101.0},
        {"trade_date": "2026-05-20", "trade_time": "2026-05-20 14:30:00", "close": 101.2, "pre_close": 101.0},
        {"trade_date": "2026-05-20", "trade_time": "2026-05-20 14:31:00", "close": 101.2, "pre_close": 101.0},
        {"trade_date": "2026-05-21", "trade_time": "2026-05-21 09:35:00", "close": 101.0, "pre_close": 101.2},
        {"trade_date": "2026-05-21", "trade_time": "2026-05-21 14:29:00", "close": 101.3, "pre_close": 101.2},
        {"trade_date": "2026-05-21", "trade_time": "2026-05-21 14:30:00", "close": 101.3, "pre_close": 101.2},
        {"trade_date": "2026-05-21", "trade_time": "2026-05-21 14:31:00", "close": 101.3, "pre_close": 101.2},
        {"trade_date": "2026-05-22", "trade_time": "2026-05-22 09:35:00", "close": 100.6, "pre_close": 101.3},
    ]

    monkeypatch.setattr(
        "app.services.overnight_holding_service._load_daily_basic_map",
        lambda trade_date, cache: {code: {"turnover_rate": 6.0, "total_mv_yi": 120.0, "circ_mv_yi": 110.0}},
    )
    date_lookup = [
        "2026-05-12",
        "2026-05-13",
        "2026-05-14",
        "2026-05-15",
        "2026-05-16",
        "2026-05-19",
        "2026-05-20",
        "2026-05-21",
        "2026-05-22",
    ]
    monkeypatch.setattr(
        "app.services.overnight_holding_service._signal_trade_dates",
        lambda db, start_date, end_date: (["2026-05-19", "2026-05-20", "2026-05-21"], date_lookup),
    )
    monkeypatch.setattr(
        "app.services.overnight_holding_service._load_daily_window",
        lambda db, start_date, end_date: _load_seeded_window(db, code, start_date, end_date),
    )
    monkeypatch.setattr(
        "app.services.overnight_holding_service._load_stock_meta",
        lambda db, codes: {code: {"name": "隔夜样本", "industry": "银行", "is_st": False}},
    )
    monkeypatch.setattr("app.services.overnight_holding_service.MINUTE_GATE_MIN_READY_CANDIDATES", 1)
    monkeypatch.setattr(
        "app.services.overnight_holding_service._load_stock_minutes",
        lambda db, cache, code, trade_date, minute_source_trace=None: minute_map.get((code, trade_date), []),
    )
    monkeypatch.setattr(
        "app.services.overnight_holding_service._load_benchmark_bundle",
        lambda db, cache, signal_dates=None, date_lookup=None, minute_source_trace=None: benchmark_bundle,
    )
    monkeypatch.setattr(
        "app.services.overnight_holding_service._persist_backtest_run",
        lambda db, start_date, end_date, result, status="completed": None,
    )
    monkeypatch.setattr(
        "app.services.overnight_holding_service._run_overnight_temporal_check",
        lambda db, signal_dates: {"status": "completed", "passed": True, "violation_count": 0, "error_count": 0, "warning_count": 0},
    )

    result = run_overnight_holding_backtest(db, "2026-05-19", "2026-05-21")

    assert result["status"] == "completed"
    assert result["strategy_id"] == "overnight-holding-method"
    assert result["aggregate_metrics"]["trade_count"] == 3
    assert result["aggregate_metrics"]["recommendation_accuracy"] == pytest.approx(0.667, abs=0.001)
    assert result["aggregate_metrics"]["average_net_return"] != 0
    assert result["comparison"]["selected_variant"] in {"baseline", "candidate", "relaxed_pct"}
    assert len(result["false_positive_samples"]) == 1


def test_locate_entry_signal_uses_tail_window_vwap_rules():
    candidate = {
        "code": "001234",
        "name": "隔夜样本",
        "industry": "银行",
        "trade_date": "2026-05-20",
        "avg_prev5_volume": 2000.0,
        "prev_close": 10.0,
        "turnover_rate": 6.0,
        "total_mv_yi": 120.0,
        "recent_limit_up_count": 1,
        "daily_volume": 2600.0,
    }
    minutes = [
        {"trade_time": "2026-05-20 09:30:00", "close": 10.00, "high": 10.01, "low": 9.99, "volume": 100, "amount": 100000},
        {"trade_time": "2026-05-20 09:31:00", "close": 9.90, "high": 9.91, "low": 9.88, "volume": 100, "amount": 99000},
        {"trade_time": "2026-05-20 09:32:00", "close": 9.85, "high": 9.86, "low": 9.83, "volume": 100, "amount": 98500},
        {"trade_time": "2026-05-20 14:30:00", "close": 10.28, "high": 10.29, "low": 10.25, "volume": 1000, "amount": 1028000},
        {"trade_time": "2026-05-20 14:31:00", "close": 10.34, "high": 10.36, "low": 10.34, "volume": 1000, "amount": 1034000},
        {"trade_time": "2026-05-20 14:32:00", "close": 10.33, "high": 10.34, "low": 10.30, "volume": 1000, "amount": 1033000},
    ]
    benchmark_minutes = [
        {"trade_time": "2026-05-20 09:30:00", "close": 100.0, "pre_close": 100.0},
        {"trade_time": "2026-05-20 09:31:00", "close": 99.9, "pre_close": 100.0},
        {"trade_time": "2026-05-20 09:32:00", "close": 99.8, "pre_close": 100.0},
        {"trade_time": "2026-05-20 14:30:00", "close": 100.7, "pre_close": 100.0},
        {"trade_time": "2026-05-20 14:31:00", "close": 100.8, "pre_close": 100.0},
        {"trade_time": "2026-05-20 14:32:00", "close": 100.8, "pre_close": 100.0},
    ]

    signal = _locate_entry_signal(
        candidate=candidate,
        minutes=minutes,
        benchmark_minutes=benchmark_minutes,
        variant=BASELINE_VARIANT,
    )

    assert signal is not None
    assert signal["entry_time"] == "2026-05-20 14:30:00"
    assert signal["conditions"]["above_vwap_ratio"] == pytest.approx(1.0, abs=0.001)
    # 计算：尾段首分钟累积VWAP≈10.196，low=10.25，(10.25/10.196-1)*10000≈53bp
    assert signal["conditions"]["tail_vwap_gap_bps"] == pytest.approx(53.0, abs=5.0)


def test_locate_entry_signal_regression_new_high_tracking():
    """回归验证：修复后盘中新高追踪不再被尾盘起始边界截断。

    旧 bug：idx < tail_start_index 的 continue 跳过导致全天未记录新高，
    last_new_high_index 为 None，全天信号被跳过，交易数从9→1。
    """
    candidate = {
        "code": "001234", "name": "测试", "industry": "银行",
        "trade_date": "2026-05-20", "avg_prev5_volume": 5000.0,
        "prev_close": 10.0, "turnover_rate": 6.0, "total_mv_yi": 120.0,
        "recent_limit_up_count": 0, "daily_volume": 6000.0,
    }
    # 构造：09:35创全天新高(10.60)，14:30满足VWAP条件且pct=5%在范围内
    # 旧代码因last_new_high_index为None→跳过全天信号
    minutes = [
        {"trade_time": "2026-05-20 09:35:00", "close": 10.40, "high": 10.60, "low": 10.35, "volume": 2000, "amount": 2080000},
        {"trade_time": "2026-05-20 14:30:00", "close": 10.50, "high": 10.52, "low": 10.48, "volume": 3000, "amount": 3150000},
        {"trade_time": "2026-05-20 14:31:00", "close": 10.52, "high": 10.53, "low": 10.50, "volume": 2000, "amount": 2104000},
    ]
    benchmark_minutes = [
        {"trade_time": "2026-05-20 09:35:00", "close": 100.0, "pre_close": 100.0},
        {"trade_time": "2026-05-20 14:30:00", "close": 100.5, "pre_close": 100.0},
    ]

    signal = _locate_entry_signal(
        candidate=candidate,
        minutes=minutes,
        benchmark_minutes=benchmark_minutes,
        variant=BASELINE_VARIANT,
    )

    assert signal is not None, "修复后盘中新高应被正确追踪，信号不丢失"
    assert signal["entry_time"] == "2026-05-20 14:30:00"
    # tail_new_high_retest_minutes = idx(14:30) - last_new_high_index(09:35) = 1 - 0 = 1
    assert signal["conditions"]["tail_new_high_retest_minutes"] == 1
    assert signal["conditions"]["above_vwap_ratio"] == 1.0


def test_build_data_coverage_summarizes_minute_gaps():
    result = _build_data_coverage(
        ["2026-05-19", "2026-05-20"],
        [
            {
                "trade_date": "2026-05-19",
                "candidate_count": 3,
                "minute_ready_candidate_count": 1,
                "benchmark_minute_covered": True,
                "missing_minute_codes": ["000001", "000002"],
            },
            {
                "trade_date": "2026-05-20",
                "candidate_count": 2,
                "minute_ready_candidate_count": 2,
                "benchmark_minute_covered": False,
                "missing_minute_codes": [],
            },
        ],
        {
            "trade_date_start": "2026-05-19",
            "trade_date_end": "2026-05-25",
            "trade_date_count": 6,
            "code_count": 319,
        },
        [
            {"source": "local_minute_db", "scope": "stock", "code": "000003", "status": "success", "row_count": 240},
            {"source": "tushare_stk_mins", "scope": "stock", "code": "000001", "status": "permission_denied", "row_count": 0, "reason": "没有该接口权限"},
            {"source": "sina_minute", "scope": "stock", "code": "000001", "status": "success", "row_count": 240},
        ],
    )

    assert result["candidate_count"] == 5
    assert result["minute_ready_candidate_count"] == 3
    assert result["minute_missing_candidate_count"] == 2
    assert result["candidate_minute_coverage_rate"] == pytest.approx(0.6, abs=0.001)
    assert result["benchmark_minute_coverage_rate"] == pytest.approx(0.5, abs=0.001)
    assert result["missing_minute_examples"] == ["2026-05-19 000001", "2026-05-19 000002"]
    assert result["minute_source_diagnostics"]["tushare_permission_denied"] is True
    assert result["minute_source_diagnostics"]["fallback_used"] is True
    assert "sina_minute" in result["minute_source_diagnostics"]["successful_sources"]


def test_build_walk_forward_analysis_uses_time_order_windows():
    trades = [
        {"trade_date": "2026-05-19", "code": "000001", "resolved": True, "accurate": True, "net_return_pct": 1.2, "benchmark_return_pct": 0.4},
        {"trade_date": "2026-05-20", "code": "000002", "resolved": True, "accurate": False, "net_return_pct": -0.5, "benchmark_return_pct": -0.1},
        {"trade_date": "2026-05-21", "code": "000003", "resolved": True, "accurate": True, "net_return_pct": 0.8, "benchmark_return_pct": 0.2},
    ]

    result = _build_walk_forward_analysis(
        ["2026-05-19", "2026-05-20", "2026-05-21"],
        trades,
    )

    assert result["eligible"] is True
    assert result["window_count"] == 1
    assert result["windows"][0]["train_end"] == "2026-05-20"
    assert result["windows"][0]["test_start"] == "2026-05-21"
    assert result["test_aggregate"]["trade_count"] == 1
    assert result["test_aggregate"]["recommendation_accuracy"] == pytest.approx(1.0, abs=0.001)


def test_benchmark_required_dates_include_signal_and_next_exit_dates():
    result = _benchmark_required_dates(
        ["2026-05-19", "2026-05-20"],
        ["2026-05-16", "2026-05-19", "2026-05-20", "2026-05-21"],
    )

    assert result == {"2026-05-19", "2026-05-20", "2026-05-21"}


def test_build_walk_forward_analysis_expands_all_eligible_windows():
    signal_dates = [
        "2026-05-14",
        "2026-05-15",
        "2026-05-18",
        "2026-05-19",
        "2026-05-20",
        "2026-05-21",
    ]
    trades = [
        {"trade_date": "2026-05-18", "code": "000001", "resolved": True, "accurate": False, "net_return_pct": -0.3, "benchmark_return_pct": -0.1},
        {"trade_date": "2026-05-20", "code": "000002", "resolved": True, "accurate": True, "net_return_pct": 0.4, "benchmark_return_pct": 0.1},
        {"trade_date": "2026-05-20", "code": "000003", "resolved": True, "accurate": True, "net_return_pct": 0.2, "benchmark_return_pct": 0.0},
        {"trade_date": "2026-05-21", "code": "000004", "resolved": True, "accurate": False, "net_return_pct": -0.1, "benchmark_return_pct": -0.2},
    ]

    result = _build_walk_forward_analysis(signal_dates, trades)

    assert result["eligible"] is True
    assert result["min_train_dates"] == 2
    assert result["window_count"] == 4
    assert [item["test_start"] for item in result["windows"]] == signal_dates[2:]
    assert result["test_aggregate"]["trade_count"] == 4
    assert result["test_aggregate"]["recommendation_accuracy"] == pytest.approx(0.5, abs=0.001)
    assert result["notes"][0] == "滚动验证按最少 2 个训练信号日构建 expanding windows。"


def test_choose_variant_prefers_walk_forward_metrics_over_single_split():
    comparisons = {
        "baseline": {
            "variant": BASELINE_VARIANT,
            "split_metrics": {
                "test": {
                    "trade_count": 2,
                    "recommendation_accuracy": 0.50,
                    "average_net_return": 0.10,
                    "max_drawdown": -0.40,
                }
            },
            "walk_forward": {
                "test_aggregate": {
                    "trade_count": 3,
                    "recommendation_accuracy": 0.60,
                    "average_net_return": 0.18,
                    "max_drawdown": -0.35,
                }
            },
        },
        "candidate": {
            "variant": CANDIDATE_VARIANT,
            "split_metrics": {
                "test": {
                    "trade_count": 2,
                    "recommendation_accuracy": 0.80,
                    "average_net_return": 0.25,
                    "max_drawdown": -0.30,
                }
            },
            "walk_forward": {
                "test_aggregate": {
                    "trade_count": 3,
                    "recommendation_accuracy": 0.55,
                    "average_net_return": 0.12,
                    "max_drawdown": -0.90,
                }
            },
        },
    }

    selected, reason = _choose_variant([], comparisons)

    assert selected == "baseline"
    assert "滚动样本外" in reason
    assert "单次时间切分" not in reason


def test_choose_variant_falls_back_to_single_split_when_walk_forward_has_no_comparable_trades():
    comparisons = {
        "baseline": {
            "variant": BASELINE_VARIANT,
            "split_metrics": {
                "test": {
                    "trade_count": 3,
                    "recommendation_accuracy": 0.40,
                    "average_net_return": 0.05,
                    "max_drawdown": -0.60,
                }
            },
            "walk_forward": {
                "test_aggregate": {
                    "trade_count": 0,
                    "recommendation_accuracy": 0.0,
                    "average_net_return": 0.0,
                    "max_drawdown": 0.0,
                }
            },
        },
        "candidate": {
            "variant": CANDIDATE_VARIANT,
            "split_metrics": {
                "test": {
                    "trade_count": 3,
                    "recommendation_accuracy": 0.70,
                    "average_net_return": 0.12,
                    "max_drawdown": -0.40,
                }
            },
            "walk_forward": {
                "test_aggregate": {
                    "trade_count": 0,
                    "recommendation_accuracy": 0.0,
                    "average_net_return": 0.0,
                    "max_drawdown": 0.0,
                }
            },
        },
    }

    selected, reason = _choose_variant([], comparisons)

    assert selected == "candidate"
    assert "单次时间切分" in reason
