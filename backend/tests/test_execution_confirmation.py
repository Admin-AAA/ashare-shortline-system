from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.tables import DailyKbar
from app.services.execution_confirmation import (
    _adjust_stop_loss_for_ex_right,
    _derive_decisions,
    _entry_window_closed,
)


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


def test_strategy_veto_cancels_entry_and_exits_holding():
    entry, holding = _derive_decisions(
        fresh=True,
        signal_age_trade_days=1,
        entry_window_closed=False,
        prefilter_ok=True,
        signal=_signal(hard_veto=True, veto_reason="非主线热点"),
        review={"stance": "veto", "action": "不做"},
        current_price=15.0,
        stop_loss=14.5,
    )

    assert entry == {"code": "cancel", "label": "取消买入", "reason": "非主线热点"}
    assert holding["code"] == "exit"


def test_prefilter_failure_explains_specific_condition_instead_of_generic_pool_reason():
    entry, _holding = _derive_decisions(
        fresh=True,
        signal_age_trade_days=1,
        entry_window_closed=False,
        prefilter_ok=False,
        signal=_signal(
            hard_veto=True,
            veto_reason="不在策略股票池",
            prefilter_reason="当日涨幅需为非负",
        ),
        review=None,
        current_price=8.34,
        stop_loss=8.0,
    )

    assert entry == {"code": "cancel", "label": "取消买入", "reason": "当日涨幅需为非负"}


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


def test_original_stop_loss_overrides_supportive_agent_review():
    entry, holding = _derive_decisions(
        fresh=True,
        signal_age_trade_days=1,
        entry_window_closed=False,
        prefilter_ok=True,
        signal=_signal(),
        review={"stance": "support", "action": "轻仓试错"},
        current_price=14.4,
        stop_loss=14.5,
    )

    assert entry["code"] == "cancel"
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
