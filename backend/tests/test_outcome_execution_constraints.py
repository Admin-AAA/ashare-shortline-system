import json
from types import SimpleNamespace

from app.models.tables import DailyKbar
from app.services.outcome_service import (
    _resolve_entry,
    _resolve_exit,
    _resolve_stop_loss_price,
    _resolve_take_profit_price,
    _simulate_outcome,
    DEFAULT_STOP_LOSS_PCT,
    DEFAULT_TAKE_PROFIT_PCT,
    MAX_STOP_LOSS_PCT,
    MAX_TAKE_PROFIT_PCT,
)


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
    # exit=min(9.8, 10.22)=9.8（跨 bar 触发：跳空低开以 open 成交）
    bars = [
        _bar_trailing(trade_date="2026-07-02", open=10.25, high=10.3, low=10.25, close=10.28, pct_chg=3.0),
        _bar_trailing(trade_date="2026-07-03", open=9.8, high=9.9, low=9.7, close=9.8, pct_chg=-5.0),
    ]
    result = _resolve_exit(bars, stop_loss_price=9.0, take_profit_price=12.0, code="000001", entry_price=10.0)
    assert result[2] == "trailing_stop_hit"
    assert result[0] == 9.8  # 跨 bar 触发：跳空低开以 open 成交


def test_resolve_exit_trailing_same_bar_uses_trigger_price():
    """同 bar 触发（trailing 在本 bar 刚激活且 low 跌破触发价）：取触发价成交。

    因果必然：trailing 激活前提是 high 先达 hwm，故同 bar 触发时 high 必先于 low，
    价格路径是 high 见顶后回落到触发价成交。旧口径 min(open,触发价) 把"平开冲高回落"
    （open≈entry < 触发价）误判成跳空，以 open 出场，把盈利单标成 -0.20% 微亏假失败
    （D 项 28 笔 trailing failed 全落在 -0.5%~0 区间的根因）。

    复现 000962 2026-07-08 真实场景：entry=64.08，当天 open≈平开，high 冲到 +4%，
    low 回落到触发价下方 → 应以触发价成交（+3% 附近），不是 open（-0.1%）。
    """
    from app.services.outcome_service import _resolve_exit
    # entry=10.0, bar1: open=10.0（平开）, high=10.4（+4%，激活 trailing），触发价=10*(1+(4-0.8)/100)=10.32
    # bar1 low=10.1 ≤ 10.32 → 同 bar 触发。旧口径 min(10.0, 10.32)=10.0（-0.2% 假亏）；
    # 修复后取触发价 10.32（+3.2% 真盈）。
    bars = [
        _bar_trailing(trade_date="2026-07-02", open=10.0, high=10.4, low=10.1, close=10.2, pct_chg=2.0),
    ]
    result = _resolve_exit(bars, stop_loss_price=9.5, take_profit_price=12.0, code="000001", entry_price=10.0)
    assert result[2] == "trailing_stop_hit"
    assert result[0] == 10.32  # 同 bar 触发：以触发价成交，不以平开 open 成交


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


def test_resolve_stop_loss_caps_gap_up_entry_to_max():
    """高开入场场景止损 cap 在 MAX_STOP_LOSS_PCT（-5.7%），不再放宽到 -9%。

    回归第164轮 D 项 maxloss 根因：长裕集团 rec_price=79.32，次日竞价高开 entry=85.0，
    策略止损基于推荐价 74.96（相对入场 -11.9%）。旧 MAX=-9% 把它拉宽到 entry×-9%=77.35，
    单笔亏损放大到 -9.18%。收紧 MAX 到 -5.7% 后止损抬到 entry×-5.7%=80.16，封住尾部。
    """
    entry = 85.0
    raw_stop_based_on_rec_price = 74.96  # 策略止损基于推荐价 79.32（-5.6%），相对入场已 -11.9%
    sl = _resolve_stop_loss_price(entry, {"stop_loss": raw_stop_based_on_rec_price})
    # 止损相对入场亏损不应超过 MAX_STOP_LOSS_PCT（-5.7%）
    loss_pct = (sl / entry - 1) * 100
    assert loss_pct >= MAX_STOP_LOSS_PCT - 0.01, f"止损 {loss_pct:.2f}% 宽于 MAX {MAX_STOP_LOSS_PCT}%"
    # 具体值：entry × (1 - 5.7/100) = 80.165 → round 80.17
    assert sl == round(entry * (1 + MAX_STOP_LOSS_PCT / 100.0), 2)


def test_resolve_stop_loss_keeps_inrange_strategy_stop():
    """策略止损落在 [MAX, DEFAULT] 区间内时直接采用，不被归一化篡改。"""
    entry = 100.0
    # 策略止损 -5.6%（在 -5.7% ~ -5.5% 区间内）
    raw_stop = entry * (1 - 5.6 / 100.0)
    sl = _resolve_stop_loss_price(entry, {"stop_loss": raw_stop})
    assert sl == round(raw_stop, 2)


def test_resolve_stop_loss_caps_normal_stop_to_default():
    """策略止损比 DEFAULT(-5.5%) 还紧时不收紧到更紧（最紧 -5.5%）。"""
    entry = 100.0
    raw_stop = entry * (1 - 3.0 / 100.0)  # -3%，过紧
    sl = _resolve_stop_loss_price(entry, {"stop_loss": raw_stop})
    assert sl == round(entry * (1 + DEFAULT_STOP_LOSS_PCT / 100.0), 2)


def test_resolve_stop_loss_max_not_wider_than_default_by_much():
    """MAX_STOP_LOSS_PCT 不应远宽于 DEFAULT——否则高开入场尾部风险失控。

    第164轮硬约束：MAX 与 DEFAULT 差距 ≤ 0.3pp（-5.7% vs -5.5%），保证止损几乎固定在 -5.5%~-5.7%，
    杜绝 -9% 那种把高开入场亏损放大到 -9%+ 的尾部。注意 MAX(-5.7) 比 DEFAULT(-5.5) 更负=更宽。
    """
    assert DEFAULT_STOP_LOSS_PCT - MAX_STOP_LOSS_PCT <= 0.3  # DEFAULT - MAX ≤ 0.3pp
    assert MAX_STOP_LOSS_PCT < DEFAULT_STOP_LOSS_PCT  # MAX 更负 = 最宽边界


# --------------------------------------------------------------------------- #
# 止盈价按比例重算（与止损对称）：第186轮修复 bug
# 根因：跳空低开后止盈价只裁剪不重算，把设计 +5% 抬到 +14.5%，固定止盈失效→trailing 兜底错标 success
# --------------------------------------------------------------------------- #
def test_resolve_take_profit_recalculates_on_gap_down():
    """跳空低开 -8.31% 场景：止盈价必须按隐含比例重算到 ~entry×1.05，而非保留推荐时的 176.6。

    复现 docs/bugs/take-profit-not-rebased-on-actual-entry-price.md 的 603991 至正股份案例：
    recommend_price=168.19，take_profit=176.60（+5%），次日跳空低开 entry=154.21。
    旧实现保留 176.6（相对 entry +14.5%），固定止盈路径失效 → trailing 兜底错标 success。
    修复后应返回 entry×1.05 ≈ 161.92（相对 entry +5%）。
    """
    recommend_price = 168.19
    feature_snapshot = {"take_profit": 176.60}
    entry = 154.21  # 跳空低开 -8.31% 后的实际开盘价
    tp = _resolve_take_profit_price(entry, feature_snapshot, recommend_price=recommend_price)
    expected = round(entry * (1 + DEFAULT_TAKE_PROFIT_PCT / 100.0), 2)
    assert tp == expected, f"跳空低开止盈价应重算为 {expected}，实际 {tp}"
    # 相对入场价必须是设计比例 +5%，而非被抬高的 +14.5%
    assert (tp / entry - 1) * 100 < 6.0


def test_resolve_take_profit_no_gap_unchanged():
    """无跳空（entry == recommend_price）时结果应与旧实现一致：直接采用 take_profit。"""
    recommend_price = 168.19
    feature_snapshot = {"take_profit": 176.60}
    entry = 168.19
    tp = _resolve_take_profit_price(entry, feature_snapshot, recommend_price=recommend_price)
    # 无跳空：隐含比例 0.05 → entry×1.05 = 176.599 → 176.6
    assert tp == round(176.60, 2)


def test_resolve_take_profit_caps_implied_ratio_to_max():
    """隐含比例过高时 clamp 到 MAX_TAKE_PROFIT_PCT（+15%），不被异常 take_profit 抬高。"""
    recommend_price = 100.0
    feature_snapshot = {"take_profit": 130.0}  # 隐含 +30%，远超设计
    entry = 95.0  # 跳空低开
    tp = _resolve_take_profit_price(entry, feature_snapshot, recommend_price=recommend_price)
    assert tp == round(entry * (1 + MAX_TAKE_PROFIT_PCT / 100.0), 2)


def test_resolve_take_profit_floors_implied_ratio_to_default():
    """隐含比例过低（take_profit 异常低于入场）时 clamp 到 DEFAULT_TAKE_PROFIT_PCT（+5%）。"""
    recommend_price = 100.0
    feature_snapshot = {"take_profit": 98.0}  # 隐含 -2%，异常
    entry = 95.0
    tp = _resolve_take_profit_price(entry, feature_snapshot, recommend_price=recommend_price)
    assert tp == round(entry * (1 + DEFAULT_TAKE_PROFIT_PCT / 100.0), 2)


def test_resolve_take_profit_without_recommend_price_keeps_legacy_behavior():
    """未传 recommend_price（静态/跳空高开拦截路径）时退回旧行为：仅上限裁剪、不重算。"""
    feature_snapshot = {"take_profit": 176.60}
    entry = 154.21  # 此路径下 entry 为 None，这里仅验证 None 分支退回原值
    tp = _resolve_take_profit_price(None, feature_snapshot)
    assert tp == round(176.60, 2)
