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


# ── 第 4 条：告警跨日解除 / 条件还在每天重发 ─────────────────────────────

@pytest.fixture()
def real_alerts(db, monkeypatch):
    """用真实的告警登记（pytest 下只放内存），看活跃名单本身。"""
    import app.core.alerts as alerts_mod
    monkeypatch.undo()
    monkeypatch.setattr(cov, "_eligible_run_ids", lambda _db: ("run_a",))
    alerts_mod.reset_alerts()
    yield alerts_mod
    alerts_mod.reset_alerts()


def _keys(alerts_mod, kind=cov.ALERT_KIND):
    return sorted(a["key"] for a in alerts_mod.active_alert_records(kind))


def test_past_day_unchecked_alert_resolves_next_day(db, real_alerts):
    real_alerts.emit_alert(cov.ALERT_KIND, "2026-10-15", "10-15 漏查")
    real_alerts.emit_alert(cov.ALERT_KIND, "2026-10-16", "10-16 漏查")
    cov.sweep_coverage_alerts(db, "2026-10-16")
    assert _keys(real_alerts) == ["2026-10-16"]  # 今天的留给 report_coverage 判
    cov.sweep_coverage_alerts(db, "2026-10-19")
    assert _keys(real_alerts) == []


def test_expired_gap_alert_reemits_daily_while_recent_then_resolves(db, real_alerts, monkeypatch):
    db.add(RecommendationExecutionState(recommendation_id="rec_a", latest_decision_code="expired",
                                        ever_buy_confirmed=False, latest_checked_at=datetime(2026, 10, 15, 8, 0)))
    db.commit()
    real_alerts.emit_alert(cov.ALERT_KIND, "expired:rec_a", "300444 漏查过期")
    # 冷却内不重发
    assert cov.sweep_coverage_alerts(db, "2026-10-16")["reemitted"] == 0
    assert _keys(real_alerts) == ["expired:rec_a"]
    # 一天后条件还在 → 重发一次
    import time as _time
    later = _time.time() + cov.DAILY_REEMIT_SECONDS + 60
    monkeypatch.setattr(real_alerts.time, "time", lambda: later)
    real_alerts._last_emitted.clear()
    assert cov.sweep_coverage_alerts(db, "2026-10-19")["reemitted"] == 1
    # 入场日 10-15 掉出近 5 个交易日 → 解除
    assert cov.sweep_coverage_alerts(db, "2026-10-23")["resolved"] == 1
    assert _keys(real_alerts) == []


def test_expired_gap_alert_resolves_when_state_changes(db, real_alerts):
    db.add(RecommendationExecutionState(recommendation_id="rec_a", latest_decision_code="buy",
                                        ever_buy_confirmed=True, latest_checked_at=datetime(2026, 10, 15, 2, 0)))
    db.commit()
    real_alerts.emit_alert(cov.ALERT_KIND, "expired:rec_a", "300444 漏查过期")
    cov.sweep_coverage_alerts(db, "2026-10-16")
    assert _keys(real_alerts) == []


def test_alert_cooldown_persists_across_processes(real_alerts):
    assert real_alerts.emit_alert("k_test", "x", "m", cooldown=3600) is True
    real_alerts._last_emitted.clear()  # 新子进程：进程内记录为空
    assert real_alerts.emit_alert("k_test", "x", "m", cooldown=3600) is False


def test_day1_missing_alert_stays_while_missing_then_resolves(db, real_alerts, monkeypatch):
    from app.services import entry_window_state as ews
    from app.services.execution_confirmation import DAY1_MISSING_ALERT_KIND

    real_alerts.emit_alert(DAY1_MISSING_ALERT_KIND, "rec_a", "第 1 日缺 K")
    real_alerts.emit_alert(DAY1_MISSING_ALERT_KIND, "rec_gone", "推荐已删")
    cov.sweep_coverage_alerts(db, "2026-10-16")
    # 推荐不存在 → 解除；rec_a 第 1 日（10-15）全市场日 K 仍不足 → 保留
    assert _keys(real_alerts, DAY1_MISSING_ALERT_KIND) == ["rec_a"]
    # 第 1 日日 K 补齐 → 解除
    monkeypatch.setattr(ews, "MARKET_MIN_ROWS", 1)
    db.add(DailyKbar(code="300444", trade_date="2026-10-15", open=8.9, high=9.1, low=8.8, close=9.0,
                     volume=100, amount=1e6, pct_chg=1.0))
    db.commit()
    cov.sweep_coverage_alerts(db, "2026-10-16")
    assert _keys(real_alerts, DAY1_MISSING_ALERT_KIND) == []
