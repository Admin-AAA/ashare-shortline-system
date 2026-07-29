from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.tables import DailyKbar
from app.services.execution_outcome_policy import (
    apply_execution_outcome_policy,
    build_cancel_observations,
)


def _successful_simulation() -> dict:
    return {
        "entry_triggered": True,
        "entry_date": "2026-07-30",
        "entry_price": 10.0,
        "exit_date": "2026-08-03",
        "exit_price": 10.8,
        "executed_return": 7.8,
        "weighted_return": 3.9,
        "position_size": 0.5,
        "holding_days": 3,
        "entry_slippage_bps": 5.0,
        "exit_slippage_bps": 5.0,
        "exit_reason": "horizon_close",
        "outcome_label": "success",
        "outcome_label_legacy": "success",
        "outcome_reason": "模拟盈利",
        "close_return": 8.0,
    }


def test_cancelled_before_entry_is_excluded_from_trade_outcome():
    state = SimpleNamespace(cancelled_before_entry=True, ever_buy_confirmed=False)

    projected = apply_execution_outcome_policy(_successful_simulation(), state)

    assert projected["entry_triggered"] is False
    assert projected["entry_date"] is None
    assert projected["entry_price"] is None
    assert projected["executed_return"] == 0.0
    assert projected["weighted_return"] == 0.0
    assert projected["position_size"] == 0.0
    assert projected["outcome_label"] == "missed"
    assert projected["exit_reason"] == "execution_cancelled_before_entry"
    assert projected["close_return"] == 8.0


def test_expired_without_buy_confirmation_is_excluded_from_trade_outcome():
    state = SimpleNamespace(
        cancelled_before_entry=False,
        ever_buy_confirmed=False,
        latest_decision_code="expired",
    )

    projected = apply_execution_outcome_policy(_successful_simulation(), state)

    assert projected["entry_triggered"] is False
    assert projected["outcome_label"] == "missed"
    assert projected["executed_return"] == 0.0


def test_cancel_after_confirmed_buy_keeps_trade_outcome():
    simulation = _successful_simulation()
    state = SimpleNamespace(cancelled_before_entry=False, ever_buy_confirmed=True)

    projected = apply_execution_outcome_policy(simulation, state)

    assert projected == simulation


def test_cancel_observation_uses_future_trade_day_closes_and_separate_summary():
    engine = create_engine("sqlite:///:memory:")
    DailyKbar.__table__.create(engine)
    rec = SimpleNamespace(recommendation_id="rec_cancel_1", code="600000")
    state_map = {
        "rec_cancel_1": {
            "cancelled_before_entry": True,
            "ever_buy_confirmed": False,
            "first_cancelled_at": "2026-07-29T05:20:00",
            "first_cancelled_price": 10.0,
        }
    }

    with Session(engine) as db:
        for trade_date, close in [
            ("2026-07-29", 20.0),  # 取消当日不进入观察窗
            ("2026-07-30", 9.0),
            ("2026-07-31", 11.0),
            ("2026-08-03", 8.0),
            ("2026-08-04", 10.5),
            ("2026-08-05", 12.0),
        ]:
            db.add(DailyKbar(
                code="600000",
                trade_date=trade_date,
                open=close,
                high=close,
                low=close,
                close=close,
                volume=100,
                amount=1000,
                pct_chg=0,
                turnover_rate=1,
            ))
        db.commit()

        report = build_cancel_observations(db, [rec], state_map)

    item = report["items"]["rec_cancel_1"]
    assert item["status"] == "complete"
    assert item["horizons"]["1d"]["trade_date"] == "2026-07-30"
    assert item["horizons"]["1d"]["net_return_pct"] == -10.07
    assert item["horizons"]["3d"]["net_return_pct"] == -20.07
    assert item["horizons"]["5d"]["net_return_pct"] == 19.93
    assert report["summary"]["cancelled_count"] == 1
    assert report["summary"]["observed_5d_count"] == 1
    assert report["summary"]["average_5d_return"] == 19.93
    assert report["summary"]["avoided_loss_rate_5d"] == 0.0


def test_cancel_observation_keeps_unavailable_horizons_null():
    engine = create_engine("sqlite:///:memory:")
    DailyKbar.__table__.create(engine)
    rec = SimpleNamespace(recommendation_id="rec_cancel_2", code="600001")
    state_map = {
        "rec_cancel_2": {
            "cancelled_before_entry": True,
            "ever_buy_confirmed": False,
            "first_cancelled_at": "2026-07-29T05:20:00",
            "first_cancelled_price": 10.0,
        }
    }

    with Session(engine) as db:
        db.add(DailyKbar(
            code="600001",
            trade_date="2026-07-30",
            open=9.5,
            high=9.5,
            low=9.5,
            close=9.5,
            volume=100,
            amount=1000,
            pct_chg=0,
            turnover_rate=1,
        ))
        db.commit()

        report = build_cancel_observations(db, [rec], state_map)

    item = report["items"]["rec_cancel_2"]
    assert item["status"] == "partial"
    assert item["horizons"]["1d"]["net_return_pct"] == -5.07
    assert item["horizons"]["3d"] is None
    assert item["horizons"]["5d"] is None
    assert report["summary"]["observed_5d_count"] == 0
    assert report["summary"]["average_5d_return"] is None
