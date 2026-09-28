from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.tables import RecommendationExecutionState
from app.services.execution_confirmation_state import (
    load_execution_state_map,
    persist_execution_assessments,
)


def _assessment(decision: str, *, checked_at: str, price: float) -> dict:
    return {
        "status": "ok",
        "fresh": True,
        "recommendation_id": "rec_state_1",
        "strategy_id": "test-strategy",
        "checked_at": checked_at,
        "data_as_of": "2026-07-29",
        "quote_time": "2026-07-29T10:05:00",
        "current_price": price,
        "entry_decision": {"code": decision, "label": decision, "reason": "测试原因"},
        "condition_summary": {"passed": decision == "buy", "label": "平台突破"},
        "strategy_signal": {"setup_match": "平台突破"},
    }


def test_first_buy_persists_latest_and_ever_hit_state():
    engine = create_engine("sqlite:///:memory:")
    RecommendationExecutionState.__table__.create(engine)

    with Session(engine) as db:
        persisted = persist_execution_assessments(
            db,
            [_assessment("buy", checked_at="2026-07-29T10:05:01", price=8.41)],
        )
        db.commit()
        state = db.get(RecommendationExecutionState, "rec_state_1")

        assert persisted == 1
        assert state is not None
        assert state.latest_decision_code == "buy"
        assert state.ever_buy_confirmed is True
        assert state.first_buy_confirmed_at == datetime(2026, 7, 29, 10, 5, 1)
        assert state.first_buy_confirmed_price == 8.41


def test_later_cancel_updates_latest_but_preserves_first_buy():
    engine = create_engine("sqlite:///:memory:")
    RecommendationExecutionState.__table__.create(engine)

    with Session(engine) as db:
        persist_execution_assessments(
            db,
            [_assessment("buy", checked_at="2026-07-29T10:05:01", price=8.41)],
        )
        persist_execution_assessments(
            db,
            [_assessment("cancel", checked_at="2026-07-29T10:20:00", price=8.10)],
        )
        db.commit()
        state = db.get(RecommendationExecutionState, "rec_state_1")

        assert state.latest_decision_code == "cancel"
        assert state.latest_price == 8.10
        assert state.ever_buy_confirmed is True
        assert state.first_buy_confirmed_at == datetime(2026, 7, 29, 10, 5, 1)
        assert state.first_buy_confirmed_price == 8.41


def test_not_ready_does_not_overwrite_last_valid_state():
    engine = create_engine("sqlite:///:memory:")
    RecommendationExecutionState.__table__.create(engine)

    with Session(engine) as db:
        persist_execution_assessments(
            db,
            [_assessment("buy", checked_at="2026-07-29T10:05:01", price=8.41)],
        )
        not_ready = _assessment("not_ready", checked_at="2026-07-29T10:06:00", price=0)
        not_ready["status"] = "not_ready"
        persisted = persist_execution_assessments(db, [not_ready])
        db.commit()
        state = db.get(RecommendationExecutionState, "rec_state_1")

        assert persisted == 0
        assert state.latest_decision_code == "buy"
        assert state.latest_price == 8.41
        assert state.ever_buy_confirmed is True


def test_history_projection_exposes_condition_and_first_buy_fact():
    engine = create_engine("sqlite:///:memory:")
    RecommendationExecutionState.__table__.create(engine)

    with Session(engine) as db:
        persist_execution_assessments(
            db,
            [_assessment("buy", checked_at="2026-07-29T10:05:01", price=8.41)],
        )
        db.commit()

        projected = load_execution_state_map(db, ["rec_state_1"])["rec_state_1"]

        assert projected["latest_decision_code"] == "buy"
        assert projected["condition_summary"]["label"] == "平台突破"
        assert projected["ever_buy_confirmed"] is True
        assert projected["first_buy_confirmed_at"] == "2026-07-29T10:05:01"
        assert projected["first_buy_confirmed_price"] == 8.41


def test_cancel_before_any_buy_persists_terminal_cancel_fact():
    engine = create_engine("sqlite:///:memory:")
    RecommendationExecutionState.__table__.create(engine)

    with Session(engine) as db:
        persist_execution_assessments(
            db,
            [_assessment("cancel", checked_at="2026-07-29T10:20:00", price=8.10)],
        )
        db.commit()

        projected = load_execution_state_map(db, ["rec_state_1"])["rec_state_1"]

        assert projected["cancelled_before_entry"] is True
        assert projected["first_cancelled_at"] == "2026-07-29T10:20:00"
        assert projected["first_cancelled_price"] == 8.10
        assert projected["first_cancel_reason"] == "测试原因"


def test_cancel_after_buy_is_not_classified_as_cancelled_before_entry():
    engine = create_engine("sqlite:///:memory:")
    RecommendationExecutionState.__table__.create(engine)

    with Session(engine) as db:
        persist_execution_assessments(
            db,
            [_assessment("buy", checked_at="2026-07-29T10:05:01", price=8.41)],
        )
        persist_execution_assessments(
            db,
            [_assessment("cancel", checked_at="2026-07-29T10:20:00", price=8.10)],
        )
        db.commit()

        projected = load_execution_state_map(db, ["rec_state_1"])["rec_state_1"]

        assert projected["ever_buy_confirmed"] is True
        assert projected["cancelled_before_entry"] is False
        assert projected["first_cancelled_at"] is None


def test_fresh_cancel_fills_reference_fields_for_migration_backfill():
    engine = create_engine("sqlite:///:memory:")
    RecommendationExecutionState.__table__.create(engine)

    with Session(engine) as db:
        db.add(RecommendationExecutionState(
            recommendation_id="rec_state_1",
            latest_decision_code="cancel",
            latest_checked_at=datetime(2026, 7, 29, 10, 0),
            latest_price=8.20,
            latest_reason="迁移前最后一次取消",
            cancelled_before_entry=True,
            ever_buy_confirmed=False,
        ))
        db.commit()

        legacy_projection = load_execution_state_map(db, ["rec_state_1"])["rec_state_1"]
        assert legacy_projection["first_cancelled_at"] == "2026-07-29T10:00:00"
        assert legacy_projection["first_cancelled_price"] == 8.20
        assert legacy_projection["first_cancel_reason"] == "迁移前最后一次取消"

        persist_execution_assessments(
            db,
            [_assessment("cancel", checked_at="2026-07-29T10:20:00", price=8.10)],
        )
        db.commit()
        projected = load_execution_state_map(db, ["rec_state_1"])["rec_state_1"]

        assert projected["cancelled_before_entry"] is True
        assert projected["first_cancelled_at"] == "2026-07-29T10:20:00"
        assert projected["first_cancelled_price"] == 8.10


def test_buy_after_earlier_cancel_restores_buy_confirmed_fact():
    """同一入场窗口内先取消后恢复买入：买入确认是有效事实，不被先前的取消屏蔽。

    取消判断是窗口内某一时刻的采样（开盘累计成交额未达标等），若一次采样就永久屏蔽后续
    买入确认，这笔推荐会被 execution_outcome_policy 永久踢出交易胜负统计。
    """
    engine = create_engine("sqlite:///:memory:")
    RecommendationExecutionState.__table__.create(engine)

    with Session(engine) as db:
        persist_execution_assessments(
            db,
            [_assessment("cancel", checked_at="2026-07-29T09:33:00", price=8.10)],
        )
        persist_execution_assessments(
            db,
            [_assessment("buy", checked_at="2026-07-29T13:40:00", price=8.55)],
        )
        db.commit()

        projected = load_execution_state_map(db, ["rec_state_1"])["rec_state_1"]

        assert projected["latest_decision_code"] == "buy"
        assert projected["ever_buy_confirmed"] is True
        assert projected["first_buy_confirmed_at"] == "2026-07-29T13:40:00"
        assert projected["first_buy_confirmed_price"] == 8.55
        # 取消发生过的事实仍然保留，供复盘；但不再冒充「买入前已取消」的终态
        assert projected["first_cancelled_at"] == "2026-07-29T09:33:00"
