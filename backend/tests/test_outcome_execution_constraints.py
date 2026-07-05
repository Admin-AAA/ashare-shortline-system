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


def _bar_trailing(
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


def test_resolve_exit_take_profit_path_preserved():
    """trailing 不抢跑 take_profit 路径：TP 触发时直接返回，不走 trailing。"""
    from app.services.outcome_service import _resolve_exit
    # holding 期内 high 触及 take_profit → 应返回 take_profit_hit
    bars = [
        _bar_trailing(trade_date="2026-07-02", open=10.0, high=11.5, low=10.0, close=11.0, pct_chg=10.0),
    ]
    result = _resolve_exit(bars, stop_loss_price=9.5, take_profit_price=11.0, code="000001", entry_price=10.0)
    assert result[2] == "take_profit_hit"
    assert result[0] == 11.0  # 以 take_profit 价成交


def test_resolve_exit_trailing_uses_min_open_on_gap_down():
    """trailing 触发价取 min(open, 触发价)——跳空低开时以 open 成交（更差）。

    回归测试：之前用 max 会虚假高估出场价（86 笔虚假 100% 胜率）。
    """
    from app.services.outcome_service import _resolve_exit
    # entry=10.0, bar1 high=10.3（+3%，激活 trailing），触发价=10*(1+(3-0.8)/100)=10.22
    # bar1 low=10.25 > 10.22 不触发；bar2 跳空低开 open=9.8, low=9.7 ≤ 10.22 → trailing 触发
    # exit=min(9.8, 10.22)=9.8（跳空低开以 open 成交）
    bars = [
        _bar_trailing(trade_date="2026-07-02", open=10.25, high=10.3, low=10.25, close=10.28, pct_chg=3.0),
        _bar_trailing(trade_date="2026-07-03", open=9.8, high=9.9, low=9.7, close=9.8, pct_chg=-5.0),
    ]
    result = _resolve_exit(bars, stop_loss_price=9.0, take_profit_price=12.0, code="000001", entry_price=10.0)
    assert result[2] == "trailing_stop_hit"
    assert result[0] == 9.8  # min(open, 触发价)，跳空低开以 open 成交


def test_resolve_exit_stop_loss_priority_over_trailing():
    """固定止损优先于 trailing：low 触及 stop_loss 时返回 stop_loss_hit，不走 trailing。"""
    from app.services.outcome_service import _resolve_exit
    # bar1 high=10.3（激活 trailing），low=10.25 > 触发价 10.22 不触发
    # bar2 low=9.5 触及 stop_loss → stop_loss 优先于 trailing 返回 stop_loss_hit
    bars = [
        _bar_trailing(trade_date="2026-07-02", open=10.25, high=10.3, low=10.25, close=10.28, pct_chg=3.0),
        _bar_trailing(trade_date="2026-07-03", open=9.6, high=9.7, low=9.5, close=9.6, pct_chg=-5.0),
    ]
    result = _resolve_exit(bars, stop_loss_price=9.5, take_profit_price=12.0, code="000001", entry_price=10.0)
    assert result[2] == "stop_loss_hit"
    assert result[0] == 9.5


def test_resolve_exit_trailing_not_activated_below_threshold():
    """high 未达激活阈值（+2%）时 trailing 不激活，走 horizon_close。"""
    from app.services.outcome_service import _resolve_exit
    # bar1 high=10.1（+1%，未达 2% 激活阈值）, bar2 回落 → horizon_close
    bars = [
        _bar_trailing(trade_date="2026-07-02", open=10.0, high=10.1, low=10.0, close=10.05, pct_chg=1.0),
        _bar_trailing(trade_date="2026-07-03", open=10.0, high=10.1, low=9.9, close=9.95, pct_chg=0.0),
    ]
    result = _resolve_exit(bars, stop_loss_price=9.0, take_profit_price=12.0, code="000001", entry_price=10.0)
    assert result[2] == "horizon_close"
    assert result[0] == 9.95  # 最后一根 bar 的 close


def test_simulate_outcome_feature_snapshot_preparse_consistency():
    """回归测试：_simulate_outcome 传入预解析 feature_snapshot 与内部解析结果一致。

    上轮 T4 优化加 feature_snapshot 可选参数（534→215ms，3 horizon 共享解析）。
    防止未来回退：移除参数或改解析逻辑时，本测试确保两种调用方式结果一致。
    """
    snapshot_dict = {"ideal_buy": 11.0, "stop_loss": 10.0, "take_profit": 12.0}
    rec = SimpleNamespace(
        code="000001",
        trade_date="2026-05-12",
        recommend_price=11.0,
        feature_snapshot_json=json.dumps(snapshot_dict, ensure_ascii=False),
    )
    bars = [
        _bar(trade_date="2026-05-13", open=10.9, high=11.2, low=10.8, close=11.0, pct_chg=0.0),
        _bar(trade_date="2026-05-14", open=11.0, high=11.5, low=10.9, close=11.3, pct_chg=2.7),
        _bar(trade_date="2026-05-15", open=11.3, high=11.6, low=11.1, close=11.4, pct_chg=0.9),
    ]
    dates = ["2026-05-13", "2026-05-14", "2026-05-15"]

    # 1. 不传 feature_snapshot（内部解析）
    sim_internal = _simulate_outcome(rec, bars, dates, horizon_days=3)
    assert sim_internal is not None

    # 2. 传预解析 feature_snapshot
    sim_preparsed = _simulate_outcome(rec, bars, dates, horizon_days=3, feature_snapshot=snapshot_dict)
    assert sim_preparsed is not None

    # 两种方式结果必须一致
    assert sim_internal["entry_triggered"] == sim_preparsed["entry_triggered"]
    assert sim_internal["entry_date"] == sim_preparsed["entry_date"]
    assert sim_internal["executed_return"] == sim_preparsed["executed_return"]
    assert sim_internal["outcome_label"] == sim_preparsed["outcome_label"]

    # 3. 传 None 应回退到内部解析（向后兼容）
    sim_none = _simulate_outcome(rec, bars, dates, horizon_days=3, feature_snapshot=None)
    assert sim_none is not None
    assert sim_none["entry_triggered"] == sim_internal["entry_triggered"]
