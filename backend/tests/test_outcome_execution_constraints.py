import json
from types import SimpleNamespace

from app.models.tables import DailyKbar
from app.services.outcome_service import _resolve_entry, _resolve_exit, _simulate_outcome


def _bar(
    *,
    trade_date: str,
    open: float,
    high: float,
    low: float,
    close: float,
    pct_chg: float,
    volume: float = 1000,
    amount: float = 10000,
) -> DailyKbar:
    return DailyKbar(
        code="000001",
        trade_date=trade_date,
        open=open,
        high=high,
        low=low,
        close=close,
        pct_chg=pct_chg,
        volume=volume,
        amount=amount,
    )


def test_resolve_entry_blocks_one_word_limit_up_as_unexecutable():
    rec = SimpleNamespace(code="000001")
    one_word_limit_up = _bar(
        trade_date="2026-05-13",
        open=11.0,
        high=11.0,
        low=11.0,
        close=11.0,
        pct_chg=10.0,
    )

    result = _resolve_entry(
        rec,
        {"ideal_buy": 11.0},
        [one_word_limit_up],
        horizon_days=3,
    )

    assert result["triggered"] is False
    assert "一字涨停" in result["reason"]


def test_resolve_entry_blocks_no_liquidity_bar():
    rec = SimpleNamespace(code="000001")
    no_liquidity = _bar(
        trade_date="2026-05-13",
        open=10.1,
        high=10.2,
        low=9.9,
        close=10.0,
        pct_chg=0.0,
        volume=0,
        amount=0,
    )

    result = _resolve_entry(
        rec,
        {"ideal_buy": 10.0},
        [no_liquidity],
        horizon_days=3,
    )

    assert result["triggered"] is False
    assert "无成交量" in result["reason"]


def test_resolve_entry_defers_blocked_limit_up_to_next_executable_bar():
    rec = SimpleNamespace(code="000001")
    one_word_limit_up = _bar(
        trade_date="2026-05-13",
        open=11.0,
        high=11.0,
        low=11.0,
        close=11.0,
        pct_chg=10.0,
    )
    executable_pullback = _bar(
        trade_date="2026-05-14",
        open=10.9,
        high=11.2,
        low=10.8,
        close=11.0,
        pct_chg=0.0,
    )

    result = _resolve_entry(
        rec,
        {"ideal_buy": 11.0},
        [one_word_limit_up, executable_pullback],
        horizon_days=3,
    )

    assert result["triggered"] is True
    assert result["date"] == "2026-05-14"
    assert result["price"] == 10.9


def test_simulate_outcome_respects_recommendation_time_execution_block():
    rec = SimpleNamespace(
        code="000001",
        trade_date="2026-05-12",
        recommend_price=11.0,
        feature_snapshot_json=json.dumps(
            {
                "ideal_buy": 11.0,
                "execution_viability": {
                    "status": "blocked",
                    "label": "入场不可成交",
                    "block_reason": "入场日一字涨停，按真实成交约束视为买不进",
                },
            },
            ensure_ascii=False,
        ),
    )
    executable_pullback = _bar(
        trade_date="2026-05-13",
        open=10.9,
        high=11.2,
        low=10.8,
        close=11.0,
        pct_chg=0.0,
    )

    blocked = _simulate_outcome(
        rec,
        [executable_pullback],
        ["2026-05-13"],
        horizon_days=3,
    )

    assert blocked is not None
    assert blocked["entry_triggered"] is False
    assert blocked["exit_reason"] == "recommendation_execution_blocked"
    assert blocked["executed_return"] == 0.0
    assert "推荐时已判定不可真实成交" in blocked["outcome_reason"]

    rec_without_block = SimpleNamespace(
        code="000001",
        trade_date="2026-05-12",
        recommend_price=11.0,
        feature_snapshot_json=json.dumps({"ideal_buy": 11.0}, ensure_ascii=False),
    )
    executable = _simulate_outcome(
        rec_without_block,
        [executable_pullback],
        ["2026-05-13"],
        horizon_days=3,
    )

    assert executable is not None
    assert executable["entry_triggered"] is True
    assert executable["entry_date"] == "2026-05-13"


def test_resolve_exit_marks_fully_locked_limit_down_horizon():
    locked_day_1 = _bar(
        trade_date="2026-05-13",
        open=9.0,
        high=9.0,
        low=9.0,
        close=9.0,
        pct_chg=-10.0,
    )
    locked_day_2 = _bar(
        trade_date="2026-05-14",
        open=8.1,
        high=8.1,
        low=8.1,
        close=8.1,
        pct_chg=-10.0,
    )

    price, trade_date, reason = _resolve_exit(
        [locked_day_1, locked_day_2],
        stop_loss_price=9.5,
        take_profit_price=12.0,
        code="000001",
    )

    assert price == 8.1
    assert trade_date == "2026-05-14"
    assert reason == "limit_down_locked_horizon_mark"


def test_resolve_exit_defers_locked_limit_down_until_sellable_stop():
    locked_day = _bar(
        trade_date="2026-05-13",
        open=9.0,
        high=9.0,
        low=9.0,
        close=9.0,
        pct_chg=-10.0,
    )
    sellable_stop_day = _bar(
        trade_date="2026-05-14",
        open=9.4,
        high=9.6,
        low=9.2,
        close=9.3,
        pct_chg=3.3,
    )

    price, trade_date, reason = _resolve_exit(
        [locked_day, sellable_stop_day],
        stop_loss_price=9.5,
        take_profit_price=12.0,
        code="000001",
    )

    assert price == 9.4
    assert trade_date == "2026-05-14"
    assert reason == "stop_loss_gap"
