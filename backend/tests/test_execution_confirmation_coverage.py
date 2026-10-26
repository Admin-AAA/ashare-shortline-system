"""盘中买入确认覆盖率：入场日漏查要告警、进数据健康，过期原因写明轮询缺口。"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.market import TradeCalendar
from app.models.recommendation import RecommendationExecutionState
from app.models.tables import DailyKbar, Recommendation, RecommendationOutcome, RecommendationRun
from app.services import execution_confirmation_coverage as cov


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    for table in (DailyKbar, Recommendation, RecommendationOutcome, RecommendationRun, RecommendationExecutionState,
                  TradeCalendar):
        table.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(cov, "_eligible_run_ids", lambda _db: ("run_a",))
    alerts: list[tuple] = []
    import app.core.alerts as alerts_mod
    monkeypatch.setattr(alerts_mod, "emit_alert", lambda *a, **k: alerts.append(("emit", *a)) or True)
    monkeypatch.setattr(alerts_mod, "resolve_alert", lambda *a, **k: alerts.append(("resolve", *a)) or True)
    session.info["alerts"] = alerts
    for day in range(12, 24):  # 交易所日历：10-17/18 周末
        cal = f"202610{day:02d}"
        session.add(TradeCalendar(exchange="SSE", cal_date=cal, is_open=day not in (17, 18)))
    for day in ("2026-10-14", "2026-10-15", "2026-10-16"):
        session.add(DailyKbar(code="600000", trade_date=day, open=10, high=10.3, low=9.9, close=10.1,
                              volume=100, amount=1e6, pct_chg=1.0))
    session.add(RecommendationRun(run_id="run_a", trade_date="2026-10-14"))
    session.add(Recommendation(recommendation_id="rec_a", run_id="run_a", trade_date="2026-10-14", code="300444",
                               name="双杰电气", recommend_price=8.87, final_score=74.2, bucket="chao-gu-yang-jia-skill",
                               recommended_at=datetime(2026, 10, 14, 23, 0)))  # 盘后（次日 07:00 北京）补跑
    session.commit()
    yield session
    session.close()


def _cst(y, m, d, hh, mm):
    from datetime import timedelta
    return datetime(y, m, d, hh, mm, tzinfo=timezone(timedelta(hours=8)))


def test_session_start_is_0930_beijing_in_utc():
    assert cov.session_start_utc("2026-10-15") == datetime(2026, 10, 15, 1, 30)


def test_unchecked_on_entry_day_is_reported(db):
    unchecked = cov.entry_day_unchecked(db, "2026-10-15", run_ids=("run_a",))
    assert [u["code"] for u in unchecked] == ["300444"]


def test_checked_in_session_is_not_reported(db):
    db.add(RecommendationExecutionState(recommendation_id="rec_a", latest_decision_code="wait",
                                        latest_checked_at=datetime(2026, 10, 15, 2, 0)))  # 10:00 北京
    db.commit()
    assert cov.entry_day_unchecked(db, "2026-10-15", run_ids=("run_a",)) == []


def test_report_coverage_alerts_after_grace_and_skips_before(db):
    assert cov.report_coverage(db, now=_cst(2026, 10, 15, 9, 40))["checked"] is False
    result = cov.report_coverage(db, now=_cst(2026, 10, 15, 10, 0))
    assert len(result["unchecked"]) == 1
    assert db.info["alerts"][-1][0] == "emit" and db.info["alerts"][-1][1] == cov.ALERT_KIND


def test_expiry_without_session_check_is_annotated(db):
    item = {"recommendation_id": "rec_a", "code": "300444", "trade_date": "2026-10-14", "data_as_of": "2026-10-15",
            "entry_decision": {"code": "expired", "label": "盘中买点已过执行窗口", "reason": "今日已收盘"},
            "condition_summary": {"scope": "entry_day"}}
    assert cov.annotate_unchecked_expiries(db, [item]) == 1
    assert "盘中未被检查（轮询缺口）" in item["entry_decision"]["reason"]
    assert item["condition_summary"]["coverage_gap"] is True


def test_expiry_after_session_check_keeps_reason(db):
    db.add(RecommendationExecutionState(recommendation_id="rec_a", latest_decision_code="wait",
                                        latest_checked_at=datetime(2026, 10, 15, 5, 0)))  # 13:00 北京
    db.commit()
    item = {"recommendation_id": "rec_a", "code": "300444", "trade_date": "2026-10-14", "data_as_of": "2026-10-15",
            "entry_decision": {"code": "expired", "label": "盘中买点已过执行窗口", "reason": "今日已收盘"}}
    assert cov.annotate_unchecked_expiries(db, [item]) == 0
    assert item["entry_decision"]["reason"] == "今日已收盘"


def test_data_health_item_red_when_unchecked(db, monkeypatch):
    from app.services import data_quality_monitor as dqm
    item = dqm.check_confirmation_coverage(db, ["2026-10-14", "2026-10-15"], now=_cst(2026, 10, 15, 10, 30))
    assert item["severity"] == dqm.RED and "300444" in item["detail"]


def test_data_health_item_yellow_for_recent_silent_expiry(db):
    from app.services import data_quality_monitor as dqm
    db.add(RecommendationExecutionState(recommendation_id="rec_a", latest_decision_code="expired",
                                        latest_checked_at=datetime(2026, 10, 15, 13, 15),
                                        created_at=datetime(2026, 10, 15, 13, 15)))  # 21:15 北京，收盘后首建
    db.commit()
    item = dqm.check_confirmation_coverage(db, ["2026-10-14", "2026-10-15", "2026-10-16"], now=_cst(2026, 10, 16, 16, 0))
    assert item["severity"] == dqm.YELLOW and "300444" in item["detail"]
