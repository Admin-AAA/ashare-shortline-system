"""结果模拟引擎（`app.services.outcome_simulation`）单测（任务 6.1）。

**Validates: Requirements 7.1, 1.1**

本文件从 `tests/test_outcome_execution_constraints.py` 迁移而来（module extraction 后
的测试迁移，需求 10.2：接口搬迁后绑定旧路径的测试改写为新路径，不留双份覆盖）。
迁移前该文件从 `app.services.outcome_service` import 这些函数（该模块只是 re-export），
迁移后直接从权威实现 `app.services.outcome_simulation` import，验证同一套纯计算逻辑：
入场判定（`_resolve_entry`）、出场判定（`_resolve_exit` / trailing stop）、止损价归一化
（`_resolve_stop_loss_price`）、核心模拟入口（`_simulate_outcome`）在各分支下的行为。
"""
import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.models.tables import DailyKbar
from app.services.outcome_simulation import (
    DEFAULT_STOP_LOSS_PCT,
    MAX_STOP_LOSS_PCT,
    TRAILING_STOP_ACTIVATE_PCT,
    TRAILING_STOP_CALLBACK_PCT,
    _bar_tradeable,
    _resolve_entry,
    _resolve_exit,
    _resolve_exit_causal,
    _resolve_stop_loss_price,
    _resolve_take_profit_price,
    _simulate_outcome,
    _static_window_metrics,
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
    """trailing 不抢跑 take_profit 路径：TP 触发时直接返回，不走 trailing。

    T+1 口径：bars[0] 是入场日（不参与离场），从 bars[1] 起扫。
    """
    # 入场日（不触发离场）+ 次日 high 触及 take_profit → 应返回 take_profit_hit
    bars = [
        _bar_trailing(trade_date="2026-07-01", open=10.0, high=10.1, low=9.9, close=10.0, pct_chg=0.0),
        _bar_trailing(trade_date="2026-07-02", open=10.0, high=11.5, low=10.0, close=11.0, pct_chg=10.0),
    ]
    result = _resolve_exit(bars, stop_loss_price=9.5, take_profit_price=11.0, code="000001", entry_price=10.0)
    assert result[2] == "take_profit_hit"
    assert result[0] == 11.0  # 以 take_profit 价成交


def test_resolve_exit_trailing_uses_min_open_on_gap_down():
    """利润地板生效后次日跳空跌穿保护价，以 open 成交并标记 gap。

    回归测试：之前用 max 会虚假高估出场价（86 笔虚假 100% 胜率）。
    """
    # entry=10.0；bar2 high=10.3（+3%）生成次日 +1.5% 利润地板 10.15。
    # bar2 当日不能用新保护价退出；bar3 跳空低开 open=9.8 ≤ 10.15，按 open 成交。
    bars = [
        _bar_trailing(trade_date="2026-07-01", open=10.0, high=10.1, low=9.9, close=10.0, pct_chg=0.0),
        _bar_trailing(trade_date="2026-07-02", open=10.25, high=10.3, low=10.25, close=10.28, pct_chg=3.0),
        _bar_trailing(trade_date="2026-07-03", open=9.8, high=9.9, low=9.7, close=9.8, pct_chg=-5.0),
    ]
    result = _resolve_exit(bars, stop_loss_price=9.0, take_profit_price=12.0, code="000001", entry_price=10.0)
    assert result[2] == "trailing_stop_gap"
    assert result[0] == 9.8  # 跨 bar 触发：跳空低开以 open 成交


def test_resolve_exit_same_day_new_high_only_updates_next_day():
    """生产因果口径禁止用当日新高生成的保护价在同一根日 K 退出。"""
    # bar2 high=10.4（+4%）只能生成下一交易日 +2% 的利润地板；窗口在 bar2 结束，
    # 即使当日 low=10.1，也只能按 horizon close=10.2 标记。
    bars = [
        _bar_trailing(trade_date="2026-07-01", open=10.0, high=10.1, low=9.9, close=10.0, pct_chg=0.0),
        _bar_trailing(trade_date="2026-07-02", open=10.0, high=10.4, low=10.1, close=10.2, pct_chg=2.0),
    ]
    result = _resolve_exit(bars, stop_loss_price=9.5, take_profit_price=12.0, code="000001", entry_price=10.0)
    assert result == (10.2, "2026-07-02", "horizon_close")


def test_resolve_exit_gap_profit_floor_precedes_intraday_hard_stop():
    """开盘先跌穿已生效的利润地板时，应在盘中固定止损前按开盘退出。"""
    # bar2 high=10.3（+3%）生成次日利润地板 10.15；bar3 open=9.6 跌穿利润地板但仍高于
    # hard stop=9.5，因此开盘先按 trailing_stop_gap 卖出，不等待盘中触发硬止损。
    bars = [
        _bar_trailing(trade_date="2026-07-01", open=10.0, high=10.1, low=9.9, close=10.0, pct_chg=0.0),
        _bar_trailing(trade_date="2026-07-02", open=10.25, high=10.3, low=10.25, close=10.28, pct_chg=3.0),
        _bar_trailing(trade_date="2026-07-03", open=9.6, high=9.7, low=9.5, close=9.6, pct_chg=-5.0),
    ]
    result = _resolve_exit(bars, stop_loss_price=9.5, take_profit_price=12.0, code="000001", entry_price=10.0)
    assert result[2] == "trailing_stop_gap"
    assert result[0] == 9.6


def test_resolve_exit_same_bar_high_surge_then_stop_loss_uses_stop_loss():
    """同 bar high 冲高激活 trailing 又 low 触止损 → 仍 stop_loss_hit（不 trailing 锁利）。

    回归锁定 002674 @ 2026-07-08 场景：单日 high 冲到 +14%（激活 trailing），同日 low 触 -5.7%
    止损。日线无法判断 high/low 时序，stop_loss 优先是保守口径。未来轮次若被此类"浮盈 14%
    却止损"案例诱导想加 trailing 优先规则，本测试挡住——见 verify_trailing_priority.py
    反事实验证：同 bar trailing 优先变体 +11pp 胜率全是过拟合，跨 bar 保守口径 0 改善。
    """
    # entry=10.0, bar1（入场日，不触发）；bar2 单 bar: high=11.4（+14% 激活 trailing，hwm=14），
    # low=9.43（触 -5.7% 止损 9.43），trailing 触发价=10*(1+(14-0.8)/100)=11.32，
    # low=9.43 ≤ 11.32 但 low ≤ stop_loss 9.43 先返回
    bars = [
        _bar_trailing(trade_date="2026-07-08", open=10.0, high=10.1, low=9.9, close=10.0, pct_chg=0.0),
        _bar_trailing(trade_date="2026-07-09", open=10.0, high=11.4, low=9.43, close=9.5, pct_chg=-5.0),
    ]
    result = _resolve_exit(bars, stop_loss_price=9.43, take_profit_price=12.0, code="002674", entry_price=10.0)
    assert result[2] == "stop_loss_hit"
    assert result[0] == 9.43  # 保守止损，不取 trailing 触发价 11.32


def test_resolve_exit_trailing_not_activated_below_threshold():
    """high 未达激活阈值（+2%）时 trailing 不激活，走 horizon_close。"""
    # bar1（入场日，不触发）；bar2 high=10.1（+1%，未达 2% 激活阈值）, bar3 回落 → horizon_close
    bars = [
        _bar_trailing(trade_date="2026-07-01", open=10.0, high=10.1, low=9.9, close=10.0, pct_chg=0.0),
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

    回归第164轮 D 项修复根因：长裕集团 rec_price=79.32，次日竞价高开 entry=85.0，
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


# --- 止盈价重算（修复 docs/bugs/take-profit-not-rebased-on-actual-entry-price） ---
# 86ad8fc 修复曾因 module extraction（d29e326 把函数搬到 outcome_simulation.py）丢失，
# 搬过去的是修复前版本。下方 5 个测试钉死重算逻辑，防止再次被重构静默覆盖。


def test_resolve_take_profit_rebases_on_gap_down():
    """跳空低开后按推荐时隐含止盈比例重算：603991 案例。

    recommend_price=168.19，take_profit=176.60（设计 +5%）；次日跳空低开 entry=154.21。
    旧实现保留 176.60 → 相对入场 +14.5%，固定止盈路径失效被 trailing 兜底、错标 success。
    修复后按 implied_tp=176.60/168.19-1=+5.0% 重算 entry×1.05=161.92，high 164.42 可触发。
    """
    tp = _resolve_take_profit_price(
        154.21, {"take_profit": 176.60}, recommend_price=168.19
    )
    assert tp == 161.92  # 154.21 × (176.60/168.19) ≈ 161.92


def test_resolve_take_profit_no_rebase_when_no_gap():
    """无跳空（入场价=推荐价）时重算结果与原值一致。"""
    tp = _resolve_take_profit_price(
        100.0, {"take_profit": 105.0}, recommend_price=100.0
    )
    assert tp == 105.0


def test_resolve_take_profit_clamps_to_max():
    """重算后超过 +15% 上限时裁剪到 entry×1.15。"""
    # recommend=100, tp=130（隐含 +30%），低开入场 100 → 重算 130 > MAX，clamp
    tp = _resolve_take_profit_price(
        100.0, {"take_profit": 130.0}, recommend_price=100.0
    )
    assert tp == 115.0  # entry × 1.15


def test_resolve_take_profit_clamps_to_min():
    """重算后低于 +5% 下限时抬到 entry×1.05。"""
    # recommend=100, tp=103（隐含 +3%），低开入场 100 → 重算 103 < fallback，clamp
    tp = _resolve_take_profit_price(
        100.0, {"take_profit": 103.0}, recommend_price=100.0
    )
    assert tp == 105.0  # entry × 1.05


def test_resolve_take_profit_without_recommend_keeps_old_behavior():
    """不传 recommend_price 时退回旧行为：只做上限裁剪、不按比例重算。"""
    tp = _resolve_take_profit_price(154.21, {"take_profit": 176.60})
    assert tp == 176.60  # 旧实现保留原值（入场价偏离时仍是绝对价，不重算）


def test_bar_tradeable_rejects_zero_ohlc_placeholder():
    """停牌占位行（OHLC 全 0）不可交易；任一字段为 0 即判定为占位。"""
    assert _bar_tradeable(_bar(trade_date="d1", open=10, high=11, low=9, close=10, pct_chg=0)) is True
    # backfill 对停牌日写入 OHLC=0 占位行
    assert _bar_tradeable(_bar(trade_date="d2", open=0, high=0, low=0, close=0, pct_chg=0, volume=0, amount=0)) is False
    # 部分为 0 同样不可信（A股最小价格 >0）
    assert _bar_tradeable(_bar(trade_date="d3", open=10, high=11, low=0, close=10, pct_chg=0)) is False


def test_static_window_metrics_skips_suspended_zero_bars():
    """停牌占位行（OHLC=0）不参与 max/min_return 统计。

    回归根因：2026-06-15 全市场 1149/5508 停牌占位行 low=0 被当成真实最低点，
    算出 min_return=-100%（如 002463 沪电股份 entry=135.07，06-15 low=0 → -100%）。
    修复后停牌 bar 被过滤，min_return 反映真实交易日的回撤。
    """
    rec = SimpleNamespace(code="002463", trade_date="2026-06-08", recommend_price=135.07)
    # 5 个交易日窗口，第 5 日 06-15 停牌（OHLC=0）
    future_bars = [
        _bar(trade_date="2026-06-09", open=138.0, high=142.3, low=133.01, close=141.09, pct_chg=2.7),
        _bar(trade_date="2026-06-10", open=136.0, high=136.99, low=127.22, close=130.6, pct_chg=-7.4),
        _bar(trade_date="2026-06-11", open=129.07, high=130.82, low=124.22, close=126.91, pct_chg=-2.8),
        _bar(trade_date="2026-06-12", open=131.66, high=134.6, low=125.0, close=125.2, pct_chg=-1.3),
        _bar(trade_date="2026-06-15", open=0.0, high=0.0, low=0.0, close=0.0, pct_chg=0.0, volume=0, amount=0),
    ]
    m = _static_window_metrics(rec, signal_bar=None, future_bars=future_bars, horizon_days=5)
    # 修复前：min_return=(0-135.07)/135.07=-100%；修复后：真实最低 124.22 → (124.22-135.07)/135.07
    assert m["min_return"] > -10.0, f"停牌 low=0 不应污染 min_return，got {m['min_return']}"
    expected_min = round((124.22 - 135.07) / 135.07 * 100, 2)
    assert abs(m["min_return"] - expected_min) < 0.01
    # max_return 也用真实高点 142.3，不被停牌 high=0 拉低
    expected_max = round((142.3 - 135.07) / 135.07 * 100, 2)
    assert abs(m["max_return"] - expected_max) < 0.01
    # max_drawdown 与 min_return 对齐（同口径）
    assert m["max_drawdown"] == m["min_return"]
    # close_return 用末日有效收盘（06-15 停牌 → 回退到 06-12 的 125.2）
    expected_close = round((125.2 - 135.07) / 135.07 * 100, 2)
    assert abs(m["close_return"] - expected_close) < 0.01


def test_static_window_metrics_all_suspended_returns_missed():
    """窗口内全部停牌（极罕见）不产生 -100% 噪声，按 OUTCOME_MISSED 处理。"""
    rec = SimpleNamespace(code="000001", trade_date="2026-06-08", recommend_price=10.0)
    future_bars = [
        _bar(trade_date="2026-06-09", open=0, high=0, low=0, close=0, pct_chg=0, volume=0, amount=0),
        _bar(trade_date="2026-06-10", open=0, high=0, low=0, close=0, pct_chg=0, volume=0, amount=0),
    ]
    m = _static_window_metrics(rec, signal_bar=None, future_bars=future_bars, horizon_days=2)
    assert m["min_return"] == 0.0
    assert m["max_return"] == 0.0
    assert m["outcome_label_legacy"] == "missed"


def test_static_window_metrics_normal_bars_unchanged():
    """无停牌占位行时行为不变（回归保护：修复不误伤正常窗口）。"""
    rec = SimpleNamespace(code="000001", trade_date="2026-05-12", recommend_price=11.0)
    future_bars = [
        _bar(trade_date="2026-05-13", open=10.9, high=11.2, low=10.8, close=11.0, pct_chg=0.0),
        _bar(trade_date="2026-05-14", open=11.0, high=11.5, low=10.9, close=11.3, pct_chg=2.7),
    ]
    m = _static_window_metrics(rec, signal_bar=None, future_bars=future_bars, horizon_days=2)
    expected_max = round((11.5 - 11.0) / 11.0 * 100, 2)
    expected_min = round((10.8 - 11.0) / 11.0 * 100, 2)
    expected_close = round((11.3 - 11.0) / 11.0 * 100, 2)
    assert abs(m["max_return"] - expected_max) < 0.01
    assert abs(m["min_return"] - expected_min) < 0.01
    assert abs(m["close_return"] - expected_close) < 0.01


def test_simulate_outcome_20cm_limit_up_not_misjudged_as_ex_right():
    """20cm 创业板涨停日 pct_chg≈20% 不被误判为「pct_chg 字段未除权」。

    回归根因（2026-07-22）：300065 海兰信 entry=23.79，07-10 涨停 pct_chg=20.0083%、
    high=28.79 触及 tp=24.98 → take_profit_hit +4.79%。旧除权检测用固定 `abs(cur_pct)>20`
    且只认科创板（68 前缀），把创业板（300）当 10cm，致涨停日 pct_chg=20.0083>20 触发
    pct_chg_unreliable，用 pct_chg 链重算把 +4.79% 覆盖成 -19.28% / failed。
    修复：limit_pct_for_code 按板块判定（创业/科创 20）+ +2pp 容差覆盖涨停浮点误差。
    """
    snapshot_dict = {"ideal_buy": 23.79, "stop_loss": 22.48, "take_profit": 25.36}
    rec = SimpleNamespace(
        code="300065",  # 创业板 20cm
        trade_date="2026-07-08",
        recommend_price=24.15,
        feature_snapshot_json=json.dumps(snapshot_dict, ensure_ascii=False),
    )
    bars = [
        _bar(trade_date="2026-07-08", open=22.91, high=24.5, low=22.61, close=24.15, pct_chg=4.36),
        _bar(trade_date="2026-07-09", open=24.48, high=24.5, low=23.1, close=23.99, pct_chg=-0.66),
        # 07-10 涨停 20cm：pct_chg=20.0083 略超 20，high=28.79 触及 tp=24.98
        _bar(trade_date="2026-07-10", open=23.57, high=28.79, low=23.32, close=28.79, pct_chg=20.0083),
        _bar(trade_date="2026-07-13", open=28.0, high=28.0, low=25.19, close=25.81, pct_chg=-10.35),
        _bar(trade_date="2026-07-14", open=24.62, high=24.86, low=20.58, close=21.01, pct_chg=-18.6),
        _bar(trade_date="2026-07-15", open=21.22, high=21.88, low=19.4, close=19.56, pct_chg=-7.21),
    ]
    dates = [str(b.trade_date) for b in bars]
    sim = _simulate_outcome(rec, bars, dates, horizon_days=5)
    # take_profit 在涨停日 07-10 触及，exit_reason=take_profit_hit，正收益
    assert sim["exit_reason"] == "take_profit_hit"
    assert sim["executed_return"] > 0, f"涨停日不应触发除权误判覆盖 take_profit 收益，got {sim['executed_return']}"
    assert sim["outcome_label"] == "success"
    # 旧 bug 下 executed_return=-19.28%（pct_chg 链重算），修复后应≈+4.79%
    assert sim["executed_return"] > 3.0, f"应接近 take_profit 收益 +4.79%，got {sim['executed_return']}"


def test_simulate_outcome_real_ex_right_day_still_corrected():
    """真实除权日（价格跳变>20% 且 pct_chg 正常）仍触发 pct_chg 链重算（修复不误伤除权修正）。

    回归保护：688012 2026-05-29 除权场景（raw -32% → 真实 -5.47%）不被本次修复破坏。
    构造：科创板除权日 open 跳变 -32% 但 pct_chg 在涨停幅度内（已除权调整）。
    """
    snapshot_dict = {"ideal_buy": 10.0, "stop_loss": 9.0, "take_profit": 11.0}
    rec = SimpleNamespace(
        code="688012",  # 科创板 20cm
        trade_date="2026-05-28",
        recommend_price=10.0,
        feature_snapshot_json=json.dumps(snapshot_dict, ensure_ascii=False),
    )
    bars = [
        _bar(trade_date="2026-05-28", open=10.0, high=10.2, low=9.8, close=10.0, pct_chg=0.0),
        # 05-29 除权：open=6.8（跳变 -32%）但 pct_chg=-5.0（已除权调整，在 20cm 涨停幅度内）
        _bar(trade_date="2026-05-29", open=6.8, high=7.0, low=6.6, close=6.9, pct_chg=-5.0),
        _bar(trade_date="2026-06-01", open=6.9, high=7.2, low=6.7, close=7.1, pct_chg=2.9),
        _bar(trade_date="2026-06-02", open=7.1, high=7.3, low=6.9, close=7.0, pct_chg=-1.4),
        _bar(trade_date="2026-06-03", open=7.0, high=7.1, low=6.8, close=6.9, pct_chg=-1.4),
    ]
    dates = [str(b.trade_date) for b in bars]
    sim = _simulate_outcome(rec, bars, dates, horizon_days=5)
    # 除权日 price_jump=-32% > 20 且 pct_chg=-5 < 20*0.6=12 → has_ex_right_day=True，
    # 触发 pct_chg 链重算（不被本次 +2pp 容差修复破坏）
    assert sim is not None
    # 除权修正后 executed_return 应基于 pct_chg 链（非价格跳变的虚假 -32%）
    assert sim["executed_return"] > -32.0, "除权日价格跳变不应造成虚假 -32% 收益"


def test_simulate_outcome_min_return_truncated_to_exit_not_horizon():
    """min/max_return 只算 entry→exit 真实持仓期，不算到 horizon 末。

    回归根因（2026-07-22）：_static_window_metrics 在 entry/exit 解析前调用，用整个
    future_bars[:horizon] 算 min/max_return。止损/止盈后的 bar 被计入 → stop_loss_hit @ -5.7%
    的票 min_return 算到 horizon 末 -34.83%（688636 实证，154 笔 clean≥72 outcome 的
    min_return/max_drawdown 被虚构回撤污染）。修复：主路径用 holding_bars[:exit_idx+1] 重算。
    """
    snapshot_dict = {"ideal_buy": 40.0, "stop_loss": 38.0, "take_profit": 44.0}
    rec = SimpleNamespace(
        code="688636",  # 科创板 20cm
        trade_date="2026-07-10",
        recommend_price=40.0,
        recommended_at=datetime(2026, 7, 10, 2, 0),  # 盘中推荐（UTC 02:00=北京10:00）：T 当天入场、T+1 离场
        feature_snapshot_json=json.dumps(snapshot_dict, ensure_ascii=False),
    )
    bars = [
        _bar(trade_date="2026-07-10", open=40.0, high=41.0, low=39.5, close=40.5, pct_chg=0.0),
        # 07-13 触及 stop_loss（low=37.0 < sl≈37.8）→ stop_loss_hit，exit 在 07-13
        _bar(trade_date="2026-07-13", open=40.0, high=40.5, low=37.0, close=38.5, pct_chg=-4.9),
        # 07-14 起：持仓已结束，这些 bar 的 low 不该计入 min_return
        _bar(trade_date="2026-07-14", open=38.0, high=38.5, low=31.61, close=32.85, pct_chg=-15.0),
        _bar(trade_date="2026-07-15", open=33.0, high=33.5, low=31.81, close=31.86, pct_chg=-3.0),
        _bar(trade_date="2026-07-16", open=31.5, high=32.0, low=30.35, close=30.6, pct_chg=-4.0),
        _bar(trade_date="2026-07-17", open=30.5, high=30.6, low=27.04, close=27.1, pct_chg=-11.0),
    ]
    dates = [str(b.trade_date) for b in bars]
    sim = _simulate_outcome(rec, bars, dates, horizon_days=5)
    assert sim["exit_reason"] == "stop_loss_hit"
    assert str(sim["exit_date"]) == "2026-07-13"
    # 旧 bug：min_return 算到 07-17 low=27.04 → (27.04-40)/40=-32%；修复后只算到 07-13 low=37.0
    assert sim["min_return"] > -10.0, f"止损后窗口未截断，min_return 虚构回撤 got {sim['min_return']}"
    # 07-13 low=37.0 vs entry≈40 → 约 -7.5%
    assert sim["min_return"] == sim["max_drawdown"], "max_drawdown 应与截断后 min_return 对齐"
    # max_return 也只算 entry→exit（07-10/07-13 的 high），不含 07-14 之后
    assert sim["max_return"] < 5.0, f"max_return 不应含 exit 后的 bar，got {sim['max_return']}"


def test_simulate_outcome_take_profit_min_return_truncated():
    """take_profit_hit 后 min_return 也只算 entry→exit，不算 exit 后的回撤。

    001270 实证：take_profit_hit @ 07-10（+4.79%），旧 min_return=-27.98%（算到 07-14 low）。
    """
    snapshot_dict = {"ideal_buy": 30.0, "stop_loss": 28.5, "take_profit": 33.0}
    rec = SimpleNamespace(
        code="300270",  # 创业板 20cm，避免 -12.65% 跌幅触发主板除权检测
        trade_date="2026-07-09",
        recommend_price=30.0,
        recommended_at=datetime(2026, 7, 9, 2, 0),  # 盘中推荐（UTC 02:00=北京10:00）：T 当天入场、T+1 离场
        feature_snapshot_json=json.dumps(snapshot_dict, ensure_ascii=False),
    )
    bars = [
        # 收盘相对实际入场不足 +2%，避免激活利润地板；本测试只验证 TP 后统计截断。
        _bar(trade_date="2026-07-09", open=30.5, high=31.0, low=29.8, close=30.5, pct_chg=0.0),
        # 07-10: 入场次日，high=33.5 触及 tp=33（take_profit_hit）
        _bar(trade_date="2026-07-10", open=31.0, high=33.5, low=29.5, close=33.2, pct_chg=7.8),
        # exit 后温和下跌（pct_chg 与 price_jump 一致，不触发除权检测），但 low=28 低于 exit 日 low
        _bar(trade_date="2026-07-13", open=33.0, high=33.2, low=28.0, close=29.0, pct_chg=-12.65),
        _bar(trade_date="2026-07-14", open=29.0, high=29.5, low=27.0, close=27.5, pct_chg=-5.17),
    ]
    dates = [str(b.trade_date) for b in bars]
    sim = _simulate_outcome(rec, bars, dates, horizon_days=4)
    assert sim["exit_reason"].startswith("take_profit"), f"应在 07-10 触及 tp，got {sim['exit_reason']}"
    assert str(sim["exit_date"]) == "2026-07-10"
    # 旧 bug：min_return 算到 07-13/14 low=27-28 → 约 -10%；修复后只算 entry→exit（07-10 low=29.5）
    # exit 日 07-10 low=29.5 vs entry≈30 → 约 -1.7%，远高于 -10%
    assert sim["min_return"] > -5.0, f"止盈后窗口未截断，min_return 虚构回撤 got {sim['min_return']}"
    assert sim["executed_return"] > 0  # take_profit 正收益


# --- 动态退出状态机（_resolve_exit_causal）MVP 测试 ---
# Spec: docs/superpowers/specs/2026-07-26-dynamic-exit-state-machine.md
# 与生产 _resolve_exit 的核心差异：trailing 保护价只用截至上一交易日收盘已知的信息，
# 当日新形成的最高价只能更新下一交易日的保护价，不得反过来触发当日退出。
# entry=10.0、sl=9.5(-5%)、tp=11.0(+10%) 为基准场景；2% 激活阈值对应 high≥10.2。


def test_causal_entry_day_never_exits_even_if_tp_or_sl_touched():
    """测试 1：入场日 holding_bars[0] 触及止盈或止损仍不退出（T+1）。

    入场日 high=11.5（触 tp=11.0）、low=9.4（触 sl=9.5），但当日不退出；
    次日 horizon_close 按次日收盘退出。
    """
    bars = [
        _bar_trailing(trade_date="2026-07-01", open=10.0, high=11.5, low=9.4, close=10.0, pct_chg=0.0),
        _bar_trailing(trade_date="2026-07-02", open=10.0, high=10.1, low=9.9, close=10.05, pct_chg=0.5),
    ]
    price, date, reason = _resolve_exit_causal(
        bars, stop_loss_price=9.5, take_profit_price=11.0, code="000001", entry_price=10.0,
    )
    assert reason == "horizon_close"
    assert date == "2026-07-02"
    assert price == 10.05


def test_causal_entry_day_high_over_2pct_but_close_below_2pct_no_activation():
    """测试 2：入场日 high 超 2% 但收盘浮盈不足 2%，次日不激活移动保护。

    入场日 high=10.3(+3%) 但 close=10.1(+1%)——只用 close 判激活，high 不算。
    次日 trailing 不激活，low 跌穿 10.1 也不触发 trailing（仅可能触发 sl/tp/horizon_close）。
    """
    bars = [
        _bar_trailing(trade_date="2026-07-01", open=10.0, high=10.3, low=9.95, close=10.1, pct_chg=1.0),
        # 次日：trailing 未激活，low=10.0 > sl 9.5，high=10.1 < tp 11.0 → horizon_close
        _bar_trailing(trade_date="2026-07-02", open=10.05, high=10.1, low=10.0, close=10.02, pct_chg=-0.8),
    ]
    price, date, reason = _resolve_exit_causal(
        bars, stop_loss_price=9.5, take_profit_price=11.0, code="000001", entry_price=10.0,
    )
    assert reason == "horizon_close"
    assert not reason.startswith("trailing")


def test_causal_entry_day_close_over_2pct_activates_next_day_protection():
    """测试 3：入场日收盘浮盈超 2%，次日移动保护生效。

    入场日 close=10.3(+3%) → 激活，次日保护价 = 10*(1+(3-0.8)/100) = 10.22。
    次日 low=10.20 ≤ 10.22 → trailing_stop_hit @ 10.22。
    """
    bars = [
        _bar_trailing(trade_date="2026-07-01", open=10.0, high=10.4, low=9.98, close=10.3, pct_chg=3.0),
        _bar_trailing(trade_date="2026-07-02", open=10.3, high=10.35, low=10.20, close=10.25, pct_chg=-0.5),
    ]
    price, date, reason = _resolve_exit_causal(
        bars, stop_loss_price=9.5, take_profit_price=11.0, code="000001", entry_price=10.0,
    )
    assert reason == "trailing_stop_hit"
    assert date == "2026-07-02"
    assert price == 10.22  # 入场日收盘确定的次日保护价


def test_causal_same_day_surge_cannot_exit_with_same_day_protection():
    """测试 4：当日冲高激活后又回落，不允许用当日新保护价退出；保护价只能下一日生效。

    入场日 close=10.0（不激活）。次日 high=10.4(+4%) 激活 trailing，次日新保护价应为
    10*(1+(4-0.8)/100)=10.32，但**当日不得用这个新保护价退出**——即使次日 low=10.1
    < 10.32，也不能在次日 trailing 出场（次日保护价要到第三日才生效）。
    若 trailing 因果被破坏（用当日 high 当场判 trailing），会错误在次日 trailing 退出。
    """
    bars = [
        _bar_trailing(trade_date="2026-07-01", open=10.0, high=10.1, low=9.99, close=10.0, pct_chg=0.0),
        # 次日冲高又回落：high=10.4(+4%) 激活，low=10.1。当日不该 trailing 退出。
        _bar_trailing(trade_date="2026-07-02", open=10.05, high=10.4, low=10.1, close=10.15, pct_chg=1.5),
        # 第三日：保护价 10.32 已在次日收盘后生成，open=10.40 > 10.32（不跳空），
        # low=10.20 ≤ 10.32 → trailing_stop_hit @ 10.32
        _bar_trailing(trade_date="2026-07-03", open=10.40, high=10.45, low=10.20, close=10.25, pct_chg=-0.5),
    ]
    price, date, reason = _resolve_exit_causal(
        bars, stop_loss_price=9.5, take_profit_price=11.0, code="000001", entry_price=10.0,
    )
    # 关键：不能在 07-02（冲高当日）trailing 退出；应在 07-03（保护价生效次日）退出
    assert reason == "trailing_stop_hit"
    assert date == "2026-07-03"
    assert price == 10.32


def test_causal_prior_day_protection_triggers_intraday_next_day():
    """测试 5：前一日已生成保护价，次日盘中触及时正确退出。

    入场日 close=10.3(+3%) → 次日(07-02)保护价 10.22 生成。
    07-02 未触及（low=10.25 > 10.22），收盘后 high=10.5(+5%) 更新次日保护价 10*(1+(5-0.8)/100)=10.42。
    07-03 low=10.40 ≤ 10.42 → trailing_stop_hit @ 10.42。
    验证保护价跨日生效、且按最新峰值更新。
    """
    bars = [
        _bar_trailing(trade_date="2026-07-01", open=10.0, high=10.4, low=9.98, close=10.3, pct_chg=3.0),
        _bar_trailing(trade_date="2026-07-02", open=10.3, high=10.5, low=10.25, close=10.45, pct_chg=1.5),
        _bar_trailing(trade_date="2026-07-03", open=10.45, high=10.48, low=10.40, close=10.41, pct_chg=-0.4),
    ]
    price, date, reason = _resolve_exit_causal(
        bars, stop_loss_price=9.5, take_profit_price=11.0, code="000001", entry_price=10.0,
    )
    assert reason == "trailing_stop_hit"
    assert date == "2026-07-03"
    assert price == 10.42  # 07-02 收盘后按 high=10.5(+5%) 更新的保护价


def test_causal_gap_down_through_protection_uses_open_as_trailing_stop_gap():
    """测试 6：次日跳空跌穿移动保护价时按开盘价退出，标记 trailing_stop_gap。

    入场日 close=10.3(+3%) → 次日保护价 10.22。
    次日 open=10.0 ≤ 10.22 → 跳空跌穿保护价，按 open 成交，标记 trailing_stop_gap。
    """
    bars = [
        _bar_trailing(trade_date="2026-07-01", open=10.0, high=10.4, low=9.98, close=10.3, pct_chg=3.0),
        _bar_trailing(trade_date="2026-07-02", open=10.0, high=10.1, low=9.95, close=10.02, pct_chg=-2.7),
    ]
    price, date, reason = _resolve_exit_causal(
        bars, stop_loss_price=9.5, take_profit_price=11.0, code="000001", entry_price=10.0,
    )
    assert reason == "trailing_stop_gap"
    assert date == "2026-07-02"
    assert price == 10.0  # 跳空以 open 成交（min(open, 保护价) = open）


def test_causal_same_day_both_sl_and_tp_triggered_uses_stop_loss():
    """测试 7：同日固定止损与固定止盈都触发时，固定止损优先。

    次日 low=9.4（触 sl=9.5）、high=11.2（触 tp=11.0）→ 按 Spec §4.5 保守止损。
    """
    bars = [
        _bar_trailing(trade_date="2026-07-01", open=10.0, high=10.1, low=9.99, close=10.0, pct_chg=0.0),
        _bar_trailing(trade_date="2026-07-02", open=10.0, high=11.2, low=9.4, close=9.6, pct_chg=-4.0),
    ]
    price, date, reason = _resolve_exit_causal(
        bars, stop_loss_price=9.5, take_profit_price=11.0, code="000001", entry_price=10.0,
    )
    assert reason == "stop_loss_intraday_both_hit"
    assert price == 9.5
    assert date == "2026-07-02"


def test_causal_stop_and_trailing_protection_only_move_up():
    """测试 8：止损价和移动保护价在持仓期间只能上移，不能下调。

    跨日因果 + 单调性：保护价由「截至上一交易日收盘已知最高浮盈」生成，下一交易日生效。
    场景严格 4 日：第二日形成较低候选值（不超前高）→ 保护价维持不动；第三日继续不退出；
    第四日 low 触及「此前较高的保护价」→ 退出。若保护价被错误下调，第四日不会以该高价退出。

    构造（entry=10.0, sl=9.5, tp=15.0）：
      07-01: close=10.5(+5%) → 激活，observed=5%，次日保护价=10*(1+(5-0.8)/100)=10.42
      07-02: high=10.45(+4.5%, 低于前高 5%) → 候选保护价 10.37 < 10.42，但**不得下调**；
             low=10.43 > 10.42 不退出；收盘后保护价仍维持 10.42
      07-03: open=10.44 > 10.42（不跳空），high=10.48(+4.8% 仍 < 5% 不更新)，low=10.43 > 10.42 不退出
      07-04: open=10.44 > 10.42（不跳空），low=10.40 ≤ 10.42 → trailing_stop_hit @ 10.42
    所有 bar 的 OHLC 合法（open∈[low,high]、close∈[low,high]）。
    """
    bars = [
        _bar_trailing(trade_date="2026-07-01", open=10.0, high=10.6, low=9.98, close=10.5, pct_chg=5.0),
        _bar_trailing(trade_date="2026-07-02", open=10.45, high=10.45, low=10.43, close=10.44, pct_chg=-0.6),
        _bar_trailing(trade_date="2026-07-03", open=10.44, high=10.48, low=10.43, close=10.45, pct_chg=0.1),
        _bar_trailing(trade_date="2026-07-04", open=10.44, high=10.46, low=10.40, close=10.42, pct_chg=-0.3),
    ]
    price, date, reason = _resolve_exit_causal(
        bars, stop_loss_price=9.5, take_profit_price=15.0, code="000001", entry_price=10.0,
    )
    assert reason == "trailing_stop_hit"
    assert price == 10.42  # 维持入场日 close 生成的较高保护价，未因 07-02/03 未创新高而下调
    assert date == "2026-07-04"  # 第四日才触发此前较高的保护价

    # 对照：若保护价被错误下调到 07-02 候选值 10.32，则 07-03 low=10.25 ≤ 10.32 会在 07-03 退出
    # 此处断言 07-04 退出即证明保护价未下调


def test_causal_one_word_limit_down_unsellable_defers():
    """测试 9：一字跌停不可卖时继续递延。

    次日一字跌停（OHLC 全 9.0，pct_chg=-10%）不可卖，跳过退出判断；
    第三日恢复可卖并触及 stop_loss → 退出。
    """
    bars = [
        _bar_trailing(trade_date="2026-07-01", open=10.0, high=10.1, low=9.99, close=10.0, pct_chg=0.0),
        # 一字跌停：OHLC 齐 9.0，不可卖
        _bar_trailing(trade_date="2026-07-02", open=9.0, high=9.0, low=9.0, close=9.0, pct_chg=-10.0, volume=0, amount=0),
        # 第三日跳空低开 open=8.8 ≤ sl 9.5 → stop_loss_gap
        _bar_trailing(trade_date="2026-07-03", open=8.8, high=9.2, low=8.7, close=8.9, pct_chg=-1.1),
    ]
    price, date, reason = _resolve_exit_causal(
        bars, stop_loss_price=9.5, take_profit_price=11.0, code="000001", entry_price=10.0,
    )
    assert date == "2026-07-03"
    assert reason == "stop_loss_gap"
    # 跳空止损 cap：max(open, entry*(1-15/100)=8.5) = max(8.8, 8.5) = 8.8
    assert price == 8.8


def test_causal_dynamic_trailing_false_gives_strict_fixed_only_path():
    """测试 10：dynamic_trailing=False 时得到严格单路径的固定止盈止损结果。

    入场日 close=10.5(+5%，本应激活 trailing），但 dynamic_trailing=False → 不激活移动保护。
    次日 low=10.1（若 trailing 激活本应触保护价 10.42），但固定口径下不触发；
    horizon_close 按末日收盘。
    """
    bars = [
        _bar_trailing(trade_date="2026-07-01", open=10.0, high=10.6, low=9.98, close=10.5, pct_chg=5.0),
        _bar_trailing(trade_date="2026-07-02", open=10.5, high=10.7, low=10.1, close=10.4, pct_chg=-1.0),
        _bar_trailing(trade_date="2026-07-03", open=10.4, high=10.5, low=10.2, close=10.3, pct_chg=-1.0),
    ]
    price, date, reason = _resolve_exit_causal(
        bars, stop_loss_price=9.5, take_profit_price=11.0, code="000001", entry_price=10.0,
        dynamic_trailing=False,
    )
    assert reason == "horizon_close"
    assert date == "2026-07-03"
    assert price == 10.3
    # 同样输入，dynamic_trailing=True 应走 trailing 路径（对照：差异来自 trailing）
    _, _, reason_dyn = _resolve_exit_causal(
        bars, stop_loss_price=9.5, take_profit_price=11.0, code="000001", entry_price=10.0,
        dynamic_trailing=True,
    )
    assert reason_dyn.startswith("trailing")


def test_causal_profit_floor_locks_fraction_of_prior_peak_profit():
    """利润地板只锁住前一日最高浮盈的一部分，给龙头正常波动留空间。

    07-02 最高浮盈 4%，25% 锁利比例生成 07-03 生效的 +1% 保护价 10.10；
    07-03 盘中回落到 10.05 时，应按 10.10 退出，而不是旧 callback 的 10.32。
    """
    bars = [
        _bar_trailing(trade_date="2026-07-01", open=10.0, high=10.1, low=9.98, close=10.0, pct_chg=0.0),
        _bar_trailing(trade_date="2026-07-02", open=10.1, high=10.4, low=10.05, close=10.3, pct_chg=3.0),
        _bar_trailing(trade_date="2026-07-03", open=10.2, high=10.25, low=10.05, close=10.1, pct_chg=-1.9),
    ]

    price, date, reason = _resolve_exit_causal(
        bars,
        stop_loss_price=9.5,
        take_profit_price=11.0,
        code="000001",
        entry_price=10.0,
        trailing_policy="profit_floor",
        profit_lock_ratio=0.25,
    )

    assert (price, date, reason) == (10.10, "2026-07-03", "trailing_stop_hit")


def test_causal_rejects_unknown_trailing_policy():
    """配置错误必须立即失败，不能静默退回旧 callback 口径污染反事实结果。"""
    bars = [
        _bar_trailing(trade_date="2026-07-01", open=10.0, high=10.1, low=9.98, close=10.0, pct_chg=0.0),
        _bar_trailing(trade_date="2026-07-02", open=10.1, high=10.4, low=10.05, close=10.3, pct_chg=3.0),
    ]

    with pytest.raises(ValueError, match="trailing_policy"):
        _resolve_exit_causal(
            bars,
            stop_loss_price=9.5,
            take_profit_price=11.0,
            code="000001",
            entry_price=10.0,
            trailing_policy="unknown",
        )


def test_production_exit_uses_next_day_profit_floor_50_policy():
    """生产默认退出必须是单路径因果的 50% 利润地板，而不是旧两轮事后择优。"""
    bars = [
        _bar_trailing(trade_date="2026-07-01", open=10.0, high=10.1, low=9.98, close=10.0, pct_chg=0.0),
        # 当日最高浮盈 4%，只生成次日 +2% 的利润地板；当日不得退出。
        _bar_trailing(trade_date="2026-07-02", open=10.1, high=10.4, low=10.05, close=10.3, pct_chg=3.0),
        # 次日保护价 10.20 生效，盘中触及退出。
        _bar_trailing(trade_date="2026-07-03", open=10.25, high=10.3, low=10.15, close=10.2, pct_chg=-1.0),
    ]

    price, date, reason = _resolve_exit(
        bars,
        stop_loss_price=9.5,
        take_profit_price=11.0,
        code="000001",
        entry_price=10.0,
    )

    assert (price, date, reason) == (10.20, "2026-07-03", "trailing_stop_hit")


def test_simulate_outcome_defaults_to_production_profit_floor_policy():
    """核心模拟入口未注入 resolver 时，也必须走生产利润地板。"""
    rec = SimpleNamespace(
        code="000001",
        trade_date="2026-07-01",
        recommend_price=10.0,
        recommended_at=datetime(2026, 7, 1, 2, 0),
        feature_snapshot_json=json.dumps(
            {"ideal_buy": 10.0, "stop_loss": 9.5, "take_profit": 11.0},
            ensure_ascii=False,
        ),
    )
    bars = [
        _bar_trailing(trade_date="2026-07-01", open=10.0, high=10.1, low=9.98, close=10.0, pct_chg=0.0),
        _bar_trailing(trade_date="2026-07-02", open=10.1, high=10.4, low=10.05, close=10.3, pct_chg=3.0),
        _bar_trailing(trade_date="2026-07-03", open=10.25, high=10.3, low=10.15, close=10.2, pct_chg=-1.0),
    ]

    sim = _simulate_outcome(rec, bars, [str(bar.trade_date) for bar in bars], horizon_days=3)

    assert sim is not None
    assert sim["exit_reason"] == "trailing_stop_hit"
    assert sim["exit_date"] == "2026-07-03"
