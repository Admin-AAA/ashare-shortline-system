"""盘中买入确认覆盖率：入场日漏查要告警、进数据健康，过期原因写明轮询缺口。"""
import json
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


# ── 第 5 条：轮询缺口只认入场日 09:30-15:00 的非过期检查；标记不被后续写入覆盖 ──

def _assessment(code, checked_at, *, reason="今日已收盘", summary=None):
    return {"recommendation_id": "rec_a", "code": "300444", "trade_date": "2026-10-14", "data_as_of": "2026-10-15",
            "status": "ok", "fresh": True, "checked_at": checked_at.isoformat(),
            "entry_decision": {"code": code, "label": code, "reason": reason},
            "condition_summary": dict(summary or {"scope": "entry_day"})}


def test_after_close_or_expired_checks_do_not_count_as_session_check(db):
    # 15:30 北京（07:30 UTC）写入的 wait 不算盘中检查
    db.add(RecommendationExecutionState(recommendation_id="rec_a", latest_decision_code="wait",
                                        latest_checked_at=datetime(2026, 10, 15, 7, 30)))
    db.commit()
    state = db.get(RecommendationExecutionState, "rec_a")
    assert cov.was_checked_in_session(state, "2026-10-15") is False
    # 盘中 10:00 但结论是过期，也不算
    state.latest_decision_code, state.latest_checked_at = "expired", datetime(2026, 10, 15, 2, 0)
    assert cov.was_checked_in_session(state, "2026-10-15") is False


def test_session_check_survives_later_expired_write(db):
    from app.services.execution_confirmation_state import persist_execution_assessments

    persist_execution_assessments(db, [_assessment("wait", datetime(2026, 10, 15, 2, 0))])  # 10:00 北京
    persist_execution_assessments(db, [_assessment("expired", datetime(2026, 10, 15, 8, 0))])  # 16:00 北京
    state = db.get(RecommendationExecutionState, "rec_a")
    assert cov.was_checked_in_session(state, "2026-10-15") is True


def test_coverage_gap_flag_not_overwritten_by_later_writes(db):
    from app.services.execution_confirmation_state import persist_execution_assessments, serialize_execution_state

    item = _assessment("expired", datetime(2026, 10, 15, 8, 0))
    assert cov.annotate_unchecked_expiries(db, [item]) == 1
    persist_execution_assessments(db, [item])
    # 之后一次普通过期写入（单票确认不走 annotate）
    persist_execution_assessments(db, [_assessment("expired", datetime(2026, 10, 16, 8, 0), reason="确认窗口已过期")])
    out = serialize_execution_state(db.get(RecommendationExecutionState, "rec_a"))
    assert out["condition_summary"]["coverage_gap"] is True
    assert out["latest_decision_label"] == "盘中漏查已过期"
    assert "轮询缺口" in out["latest_reason"]
    # 再走一遍批量 annotate：保留原因，不重复发告警
    emits_before = sum(1 for a in db.info["alerts"] if a[0] == "emit")
    again = _assessment("expired", datetime(2026, 10, 16, 8, 5))
    cov.annotate_unchecked_expiries(db, [again])
    assert again["condition_summary"]["coverage_gap"] is True
    assert sum(1 for a in db.info["alerts"] if a[0] == "emit") == emits_before


# ── 2 日窗口：第 1 日被挡时，入场日是第 2 日；ST 一字板要按 ST 幅度识别 ──────────

def _add_two_day_st_rec(db):
    # 2026-07-06 前主板 ST 幅度 5%：缺 is_st 会把 5% 一字板误判成可买
    for day in (9, 10, 11):
        db.add(TradeCalendar(exchange="SSE", cal_date=f"202606{day:02d}", is_open=True))
    db.add(Recommendation(
        recommendation_id="rec_st", run_id="run_a", trade_date="2026-06-09", code="600555",
        name="*ST测试", recommend_price=10.0, final_score=70.0, bucket="daily-stock-analysis-skill",
        feature_snapshot_json=json.dumps({"is_st": True, "entry_mode": "target_touch"}),
        recommended_at=datetime(2026, 6, 9, 23, 0),
    ))
    # 第 1 日（06-10）ST 一字涨停 +5%：按 ST 幅度被挡，真正的入场日顺延到 06-11
    db.add(DailyKbar(code="600555", trade_date="2026-06-10", open=10.5, high=10.5, low=10.5, close=10.5,
                     volume=100, amount=1e6, pct_chg=5.0))
    db.commit()


def test_two_day_window_st_one_word_day1_is_blocked_and_day2_is_checked(db):
    _add_two_day_st_rec(db)
    unchecked = cov.entry_day_unchecked(db, "2026-06-11", run_ids=("run_a",))
    assert [u["recommendation_id"] for u in unchecked] == ["rec_st"]
    # 第 2 日（真实入场日）盘中已有检查 → 不再算漏查
    db.add(RecommendationExecutionState(recommendation_id="rec_st", latest_decision_code="wait",
                                        latest_checked_at=datetime(2026, 6, 11, 2, 0)))  # 10:00 北京
    db.commit()
    assert cov.entry_day_unchecked(db, "2026-06-11", run_ids=("run_a",)) == []


def test_expiry_annotation_checks_actual_second_entry_day(db):
    _add_two_day_st_rec(db)
    db.add(RecommendationExecutionState(recommendation_id="rec_st", latest_decision_code="wait",
                                        latest_checked_at=datetime(2026, 6, 11, 2, 0)))  # 第 2 日 10:00 北京
    db.commit()
    item = {"recommendation_id": "rec_st", "code": "600555", "trade_date": "2026-06-09",
            "data_as_of": "2026-06-11",
            "entry_decision": {"code": "expired", "label": "盘中买点已过执行窗口", "reason": "今日已收盘"},
            "condition_summary": {"scope": "entry_day"}}
    # 旧口径只看第 1 日（06-10 没检查）→ 误报轮询缺口；第 2 日已检查过，不该改原因
    assert cov.annotate_unchecked_expiries(db, [item]) == 0
    assert item["entry_decision"]["reason"] == "今日已收盘"
