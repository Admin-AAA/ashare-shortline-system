from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.tables import DailyKbar
from app.services.entry_day_checks import EntryDayCheck
from app.services.execution_confirmation import (
    _adjust_stop_loss_for_ex_right,
    _condition_summary,
    _derive_decisions,
    _entry_window_closed,
)

# 入场日检查通过（非一字涨停、可成交、开盘相对推荐价 ≤6%）
_OK = EntryDayCheck(False, "entry_day_checks_passed", "入场日检查通过", observed={"gap_pct": 0.5})


def _signal(**overrides):
    signal = {
        "hard_veto": False,
        "veto_reason": "",
        "score": 72.0,
        "setup_match": "平台突破",
        "entry_rule": "放量突破平台",
    }
    signal.update(overrides)
    return signal


def test_fresh_next_day_support_allows_entry_and_holding():
    entry, holding = _derive_decisions(
        fresh=True,
        signal_age_trade_days=1,
        entry_window_closed=False,
        prefilter_ok=True,
        signal=_signal(),
        review={"stance": "support", "action": "轻仓试错", "summary": "主线和买点仍成立"},
        current_price=15.2,
        stop_loss=14.5,
        entry_check=_OK,
    )

    assert entry["code"] == "buy"
    assert holding["code"] == "hold"


def test_intraday_recommendation_can_confirm_buy_on_signal_day():
    """盘中推荐无需等待次日；推荐当日实时策略仍命中即可确认买入。"""
    entry, _holding = _derive_decisions(
        fresh=True,
        signal_age_trade_days=0,
        entry_window_closed=False,
        prefilter_ok=True,
        signal=_signal(),
        review=None,
        current_price=15.2,
        stop_loss=14.5,
        is_intraday_recommendation=True,
    )

    assert entry["code"] == "buy"


def test_intraday_recommendation_expires_after_signal_day_closes():
    """盘中推荐只在推荐当日有效，收盘后不追认买点。"""
    entry, _holding = _derive_decisions(
        fresh=True,
        signal_age_trade_days=0,
        entry_window_closed=True,
        prefilter_ok=True,
        signal=_signal(),
        review=None,
        current_price=15.2,
        stop_loss=14.5,
        is_intraday_recommendation=True,
    )

    assert entry == {
        "code": "expired",
        "label": "盘中买点已过执行窗口",
        "reason": "今日已收盘，本次买点不再有效，请勿追入",
    }


def test_intraday_recommendation_does_not_reopen_next_day():
    """盘中推荐到了下一交易日也不能重新变成可买。"""
    entry, _holding = _derive_decisions(
        fresh=True,
        signal_age_trade_days=1,
        entry_window_closed=False,
        prefilter_ok=True,
        signal=_signal(),
        review=None,
        current_price=15.2,
        stop_loss=14.5,
        is_intraday_recommendation=True,
    )

    assert entry == {
        "code": "expired",
        "label": "盘中买点已过期",
        "reason": "盘中买点仅在推荐当日有效，请勿追入",
    }


def test_after_hours_recommendation_waits_on_signal_day():
    """盘后推荐在推荐日只提示等待，不能误报 buy。"""
    entry, _holding = _derive_decisions(
        fresh=True,
        signal_age_trade_days=0,
        entry_window_closed=False,
        prefilter_ok=True,
        signal=_signal(),
        review=None,
        current_price=15.2,
        stop_loss=14.5,
        is_intraday_recommendation=False,
    )

    assert entry == {
        "code": "not_ready",
        "label": "盘后候选，下一交易日确认",
        "reason": "盘后生成，当前不可买；请在下一交易日按实时条件确认",
    }


def test_strategy_veto_no_longer_cancels_entry_but_still_exits_holding():
    """入场日重跑的策略硬否决不再取消买入（回测没有这条入场规则）；持仓提示仍按否决退出。"""
    entry, holding = _derive_decisions(
        fresh=True,
        signal_age_trade_days=1,
        entry_window_closed=False,
        prefilter_ok=True,
        signal=_signal(hard_veto=True, veto_reason="非主线热点"),
        review={"stance": "veto", "action": "不做"},
        current_price=15.0,
        stop_loss=14.5,
        entry_check=_OK,
    )

    assert entry["code"] == "buy"
    assert holding["code"] == "exit"


def test_signal_day_prefilter_rerun_does_not_cancel_entry():
    """信号日形态条件（如「当日涨幅≥3」）拿入场日盘中数据重判不过，不再取消买入。"""
    entry, _holding = _derive_decisions(
        fresh=True,
        signal_age_trade_days=1,
        entry_window_closed=False,
        prefilter_ok=False,
        signal=_signal(
            hard_veto=True,
            veto_reason="未命中任何入场 setup（策略要求 require_setup）",
            prefilter_reason="当日涨幅（%）需不低于 3（当前 -9.039）",
        ),
        review=None,
        current_price=35.0,
        stop_loss=35.3,
        entry_check=_OK,
    )

    assert entry["code"] == "buy"


def test_stale_snapshot_never_emits_actionable_buy_or_sell():
    entry, holding = _derive_decisions(
        fresh=False,
        signal_age_trade_days=1,
        entry_window_closed=False,
        prefilter_ok=True,
        signal=_signal(),
        review={"stance": "support", "action": "轻仓试错"},
        current_price=15.2,
        stop_loss=14.5,
    )

    assert entry["code"] == "not_ready"
    assert holding["code"] == "observe"


def test_old_recommendation_cannot_be_reopened_even_if_strategy_recovers():
    entry, holding = _derive_decisions(
        fresh=True,
        signal_age_trade_days=2,
        entry_window_closed=False,
        prefilter_ok=True,
        signal=_signal(),
        review={"stance": "support", "action": "轻仓试错"},
        current_price=15.2,
        stop_loss=14.5,
    )

    assert entry["code"] == "expired"
    assert holding["code"] == "hold"


def test_price_below_recommend_stop_does_not_cancel_entry_but_exits_holding():
    """回测止损按实际买入价设定，低开照样开盘买入；止损只用于持仓提示。"""
    entry, holding = _derive_decisions(
        fresh=True,
        signal_age_trade_days=1,
        entry_window_closed=False,
        prefilter_ok=True,
        signal=_signal(),
        review={"stance": "support", "action": "轻仓试错"},
        current_price=14.4,
        stop_loss=14.5,
        entry_check=_OK,
    )

    assert entry["code"] == "buy"
    assert holding["code"] == "exit"


def test_agent_failure_uses_high_confidence_rule_signal():
    entry, holding = _derive_decisions(
        fresh=True,
        signal_age_trade_days=1,
        entry_window_closed=False,
        prefilter_ok=True,
        signal=_signal(score=75.0),
        review={"status": "fallback", "error": "provider unavailable"},
        current_price=15.2,
        stop_loss=14.5,
        entry_check=_OK,
    )

    assert entry["code"] == "buy"
    assert holding["code"] == "hold"


# ── #6: 周末入场窗口不应重开 ────────────────────────────────────────────────

def test_entry_window_closed_when_snapshot_before_today():
    """周末快照 data_as_of=周五 < 今天=周六：窗口必须关闭，否则周末会重出 buy。"""
    saturday = datetime(2026, 7, 25, 10, 0)  # 周六
    assert _entry_window_closed("2026-07-24", saturday) is True


def test_entry_window_open_on_same_day_before_close():
    """当日盘前/盘中（<15:00）且快照=今天：窗口未关。"""
    weekday_noon = datetime(2026, 7, 27, 11, 30)  # 周一盘中
    assert _entry_window_closed("2026-07-27", weekday_noon) is False


def test_entry_window_closed_same_day_after_close():
    """当日盘后（≥15:00）且快照=今天：窗口关闭。"""
    weekday_evening = datetime(2026, 7, 27, 16, 0)  # 周一盘后
    assert _entry_window_closed("2026-07-27", weekday_evening) is True


# ── #7: 止损基准跨除权修正 ──────────────────────────────────────────────────

@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:")
    DailyKbar.__table__.create(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    yield db
    db.close()


def test_stop_loss_adjusted_for_ex_right_day(db_session):
    """推荐日止损 18.0，期间 10送10 除权（pct_chg=0，价格腰斩），
    复权因子=0.5，止损基准缩放到 9.0；裸价 15.0 不再误判跌破。"""
    db_session.add(DailyKbar(code="600000", trade_date="2026-06-02", open=30.0, high=30.5,
                             low=29.5, close=30.0, volume=1000, amount=1e6, pct_chg=0.0))
    db_session.commit()
    # 除权日：价格腰斩，pct_chg≈0（除权调整）
    db_session.add(DailyKbar(code="600000", trade_date="2026-06-03", open=15.0, high=15.1,
                             low=14.9, close=15.0, volume=1000, amount=1e6, pct_chg=0.0))
    db_session.commit()

    adjusted = _adjust_stop_loss_for_ex_right(db_session, "600000", "2026-06-01", "2026-06-03", 18.0)
    assert adjusted == pytest.approx(9.0, abs=0.01)


def test_stop_loss_unchanged_without_ex_right(db_session):
    """无除权（正常涨跌，无 >20% 跳变）：因子=1，止损基准不变。"""
    db_session.add(DailyKbar(code="600000", trade_date="2026-06-02", open=20.0, high=20.5,
                             low=19.5, close=20.2, volume=1000, amount=1e6, pct_chg=1.0))
    db_session.add(DailyKbar(code="600000", trade_date="2026-06-01", open=19.0, high=20.0,
                             low=18.8, close=20.0, volume=1000, amount=1e6, pct_chg=0.0))
    db_session.commit()

    adjusted = _adjust_stop_loss_for_ex_right(db_session, "600000", "2026-06-01", "2026-06-02", 18.0)
    assert adjusted == pytest.approx(18.0, abs=0.01)  # 无除权跳变，基准不变


def test_stop_loss_unchanged_when_no_bars(db_session):
    """无中间日K：无法判断除权，原样返回止损基准。"""
    adjusted = _adjust_stop_loss_for_ex_right(db_session, "600000", "2026-06-01", "2026-06-03", 18.0)
    assert adjusted == 18.0


# ── #8: 全日累计量门槛在盘中不作为取消依据 ──────────────────────────────────

def _prefilter_detail(expression: str, observed: dict, label: str) -> dict:
    return {
        "passed": False,
        "scope": "universe_include",
        "code": "universe_include",
        "label": label,
        "expression": expression,
        "observed": observed,
    }


def test_session_amount_shortfall_does_not_block_entry_or_reduce_holding():
    """开盘十几分钟累计成交额 2.601 亿低于 3 亿下限：买入只看入场日检查；持仓也不据此减仓。"""
    entry, holding = _derive_decisions(
        fresh=True,
        signal_age_trade_days=1,
        entry_window_closed=False,
        prefilter_ok=False,
        signal=_signal(
            prefilter_reason="amount_too_low",
            prefilter_detail=_prefilter_detail(
                "amount_yi >= 3",
                {"amount_yi": 2.601},
                "成交额（亿元）需不低于 3（当前 2.601）",
            ),
        ),
        review=None,
        current_price=241.02,
        stop_loss=210.0,
        entry_check=_OK,
    )

    assert entry["code"] == "buy"
    # 盘中累计量未达标不构成降低仓位的依据
    assert holding["code"] != "reduce"


def test_entry_waits_when_entry_bar_missing():
    """取不到入场日开盘行情时不确认买入（也不取消）。"""
    entry, _holding = _derive_decisions(
        fresh=True,
        signal_age_trade_days=1,
        entry_window_closed=False,
        prefilter_ok=False,
        signal=_signal(
            score=88.0,
            prefilter_detail=_prefilter_detail(
                "amount_yi >= 3",
                {"amount_yi": 0.174},
                "成交额（亿元）需不低于 3（当前 0.174）",
            ),
        ),
        review={"status": "ok", "stance": "strong_support", "summary": "主线仍成立"},
        current_price=18.38,
        stop_loss=16.0,
    )

    assert entry["code"] == "wait"


def test_breached_cumulative_upper_bound_does_not_block_entry_only_reduces_holding():
    """累计成交额已越过 15 亿上限：回测没有入场日重判 prefilter，买入确认照常 buy；只影响持仓提示（reduce）。"""
    entry, holding = _derive_decisions(
        fresh=True,
        signal_age_trade_days=1,
        entry_window_closed=False,
        prefilter_ok=False,
        signal=_signal(
            prefilter_reason="成交额（亿元）需低于 15（当前 18.4）",
            prefilter_detail=_prefilter_detail(
                "amount_yi < 15",
                {"amount_yi": 18.4},
                "成交额（亿元）需低于 15（当前 18.4）",
            ),
        ),
        review=None,
        current_price=15.2,
        stop_loss=14.0,
        entry_check=_OK,
    )

    assert entry["code"] == "buy"
    assert holding["code"] == "reduce"


def test_structural_prefilter_failure_only_affects_holding():
    """长上影次数等信号日条件不再取消买入；持仓提示仍降低仓位。"""
    entry, holding = _derive_decisions(
        fresh=True,
        signal_age_trade_days=1,
        entry_window_closed=False,
        prefilter_ok=False,
        signal=_signal(
            prefilter_reason="20 日长上影次数需不高于 6（当前 10）",
            prefilter_detail=_prefilter_detail(
                "upper_shadow_count20 <= 6",
                {"upper_shadow_count20": 10},
                "20 日长上影次数需不高于 6（当前 10）",
            ),
        ),
        review=None,
        current_price=15.2,
        stop_loss=14.0,
        entry_check=_OK,
    )

    assert entry["code"] == "buy"
    assert holding["code"] == "reduce"


def test_stop_loss_break_exits_holding_under_cumulative_shortfall():
    """跌破止损是价格事实：持仓提示退出，不因累计量不可判定而降级。"""
    entry, holding = _derive_decisions(
        fresh=True,
        signal_age_trade_days=1,
        entry_window_closed=False,
        prefilter_ok=False,
        signal=_signal(
            prefilter_detail=_prefilter_detail(
                "amount_yi >= 3",
                {"amount_yi": 0.5},
                "成交额（亿元）需不低于 3（当前 0.5）",
            ),
        ),
        review=None,
        current_price=14.4,
        stop_loss=14.5,
    )

    assert holding["code"] == "exit"


def test_hard_veto_does_not_cancel_entry_under_cumulative_shortfall():
    """入场日重跑的硬否决不再取消买入。"""
    entry, _holding = _derive_decisions(
        fresh=True,
        signal_age_trade_days=1,
        entry_window_closed=False,
        prefilter_ok=False,
        signal=_signal(
            hard_veto=True,
            veto_reason="退潮期加速末端",
            prefilter_detail=_prefilter_detail(
                "amount_yi >= 3",
                {"amount_yi": 0.5},
                "成交额（亿元）需不低于 3（当前 0.5）",
            ),
        ),
        review=None,
        current_price=15.2,
        stop_loss=14.0,
        entry_check=_OK,
    )

    assert entry["code"] == "buy"


def test_condition_summary_records_entry_day_check_only():
    """条件证据只记录入场日检查，状态随买入决策：buy=passed、wait=pending、cancel=failed。"""
    gap = EntryDayCheck(True, "entry_gap_up", "开盘价高于推荐价 7.00%", observed={"gap_pct": 7.0})
    passed = _condition_summary(_OK, {"code": "buy", "reason": "入场日检查通过"})
    waited = _condition_summary(gap, {"code": "wait", "reason": "次日再确认"})
    failed = _condition_summary(gap, {"code": "cancel", "reason": "入场窗口最后一日"})

    assert passed["status"] == "passed" and passed["scope"] == "entry_day"
    assert waited["status"] == "pending" and waited["code"] == "entry_gap_up"
    assert failed["status"] == "failed" and failed["observed"]["gap_pct"] == 7.0


# ── #9: 原始止损高于推荐价（超跌反包，价远低于 MA20）时按结算口径归一化 ──────────

def _confirm_oversold(db_session, monkeypatch, current_price: float, open_price: float | None = None):
    import json
    from types import SimpleNamespace

    from app.services import execution_confirmation as ec

    class _Now(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 20, 9, 40)

    monkeypatch.setattr(ec, "datetime", _Now)
    monkeypatch.setattr(ec, "latest_allowed_quote_date", lambda: "2026-10-20")
    rec = SimpleNamespace(
        recommendation_id="rec_x", code="002850", name="科达利", trade_date="2026-10-19",
        recommended_at=datetime(2026, 10, 19, 10, 7),  # 18:07 CST 盘后
        recommend_price=147.13, bucket="oversold-capital-reversal",
        entry_rule="次日开盘介入", invalid_rule="",
        # market_data: max(ma20*0.95, 价*0.945) = 160.05*0.95 = 152.05 > 推荐价
        feature_snapshot_json=json.dumps({"stop_loss": 152.05}),
    )
    return ec.confirm_with_prepared_context(
        db_session, rec,
        snapshot={"trade_date": "2026-10-20"},
        current={"price": current_price},
        skill=SimpleNamespace(name="超跌资金反包"),
        signal=_signal(setup_match="超跌放量反包", score=75.0),
        prefilter_ok=True,
        signal_age_trade_days=1,
        entry_bar={"open": open_price or current_price, "high": max(current_price, open_price or 0) + 1,
                   "low": min(current_price, open_price or current_price) - 1, "close": current_price,
                   "volume": 1000, "amount": 1e7, "pre_close": 147.13},
    )


def test_stop_above_recommend_price_does_not_cancel_on_open(db_session, monkeypatch):
    result = _confirm_oversold(db_session, monkeypatch, current_price=149.5)
    assert result["entry_decision"]["code"] == "buy"
    assert result["original"]["stop_loss"] == pytest.approx(139.04, abs=0.01)  # 147.13*(1-5.5%)


def test_price_below_normalized_stop_still_buys_like_backtest(db_session, monkeypatch):
    """回测按开盘价买入、止损按买入价重设；入场前跌破推荐止损不取消，持仓提示退出。"""
    result = _confirm_oversold(db_session, monkeypatch, current_price=138.0)
    assert result["entry_decision"]["code"] == "buy"
    assert result["holding_decision"]["code"] == "exit"


def test_gap_up_over_limit_waits_on_day_one(db_session, monkeypatch):
    """开盘高于推荐价 >6%：回测跳过该日，第 1 日判「次日再确认」而不是取消。"""
    result = _confirm_oversold(db_session, monkeypatch, current_price=158.0, open_price=157.0)
    assert result["entry_decision"]["code"] == "wait"
    assert result["condition_summary"]["code"] == "entry_gap_up"


def test_holding_exits_from_stop_or_hard_veto_are_flagged_non_backtest():
    """第 8 条：持仓提示里用推荐止损 / 硬风控判的退出，字段与文字都标明「不是回测规则」。"""
    base = dict(fresh=True, signal_age_trade_days=1, entry_window_closed=False, prefilter_ok=True,
                review=None, is_intraday_recommendation=False)
    _e, by_stop = _derive_decisions(**base, signal={}, current_price=9.0, stop_loss=9.5)
    _e, by_veto = _derive_decisions(**base, signal={"hard_veto": True, "veto_reason": "放量滞涨"},
                                    current_price=10.0, stop_loss=9.5)
    for holding, basis in ((by_stop, "recommend_stop_loss"), (by_veto, "hard_risk_veto")):
        assert holding["code"] == "exit"
        assert holding["backtest_rule"] is False and holding["rule_basis"] == basis
        assert "非回测规则" in holding["label"] and "非回测规则" in holding["reason"]
    _e, hold = _derive_decisions(**base, signal={"setup_match": "x", "score": 80}, current_price=10.0, stop_loss=9.5)
    assert hold["code"] == "hold" and "backtest_rule" not in hold
