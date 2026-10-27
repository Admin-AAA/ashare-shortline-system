"""结算规则升到 r20261027 后：旧版本（r20261019）已结算记录遇到日 K 改写，自动重算仍按它自己的版本算，结果不变。

- 规则版本落后本身不再触发重算；
- 日 K 改写触发的重算按记录自己的规则集（r20261019）算，写回的 rule_version 不变；
- 代码复现不了的版本：跳过重算并告警，不覆盖；
- 只有 upgrade_rule_version=True（用户批准的显式脚本）才换成新规则。
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.market import TradeCalendar
from app.models.recommendation import RecommendationExecutionState
from app.models.tables import DailyKbar, Recommendation, RecommendationOutcome, RecommendationRun
from app.services import outcome_service as svc
from app.services import outcome_simulation as osim

OLD = "outcome_simulation_v2+r20261019"
SID = "sector-relative-strength-leader"
# 板块龙头：旧规则碰理想买点 9.6 成交；新规则（回测开盘窗口）按开盘 10.1 成交——两套结果一定不同
SNAP = {"entry_mode": "target_touch", "ideal_buy": 9.6, "secondary_buy": 9.4, "stop_loss": 9.0,
        "recommend_price": 10.0}
BARS = [  # (日期, 开, 高, 低, 收)
    ("2026-10-15", 9.9, 10.1, 9.8, 10.0),
    ("2026-10-16", 10.1, 10.2, 9.5, 9.9),
    ("2026-10-19", 9.9, 10.4, 9.8, 10.3),
    ("2026-10-20", 10.3, 10.9, 10.2, 10.8),
    ("2026-10-21", 10.8, 11.0, 10.4, 10.5),
    ("2026-10-22", 10.5, 10.6, 10.0, 10.1),
    ("2026-10-23", 10.1, 10.3, 9.9, 10.2),
    ("2026-10-26", 10.2, 10.4, 10.0, 10.3),
]


def _rec(rid="rec_old"):
    return Recommendation(recommendation_id=rid, run_id="run_x", code="600123", name="测试", trade_date="2026-10-15",
                          recommend_price=10.0, final_score=75.0, bucket=SID, feature_snapshot_json=json.dumps(SNAP))


def _bars(updated_at):
    prev = 10.0
    out = []
    for d, o, h, lo, c in BARS:
        out.append(DailyKbar(code="600123", trade_date=d, open=o, high=h, low=lo, close=c,
                             pct_chg=round((c / prev - 1) * 100, 4), volume=1e6, amount=1e8, updated_at=updated_at))
        prev = c
    return out


def _sim(ruleset):
    bars = _bars(datetime(2026, 10, 26, 10, 0))
    return osim._simulate_outcome(_rec(), bars, [b.trade_date for b in bars], horizon_days=5, ruleset=ruleset)


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    for t in (Recommendation, RecommendationOutcome, RecommendationRun, DailyKbar, RecommendationExecutionState,
              TradeCalendar):
        t.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    applied: list[dict] = []

    def fake_apply(db_, rec, simulation, horizon_days, label_source="auto", execution_state=None,
                   market_bars=None, rule_version=svc.OUTCOME_RULE_VERSION):
        applied.append({"id": rec.recommendation_id, "sim": simulation, "rule_version": rule_version,
                        "state": execution_state, "label_source": label_source})
        return True

    monkeypatch.setattr(svc, "_apply_outcome_simulation", fake_apply)
    alerts: list[tuple] = []
    import app.core.alerts as alerts_mod
    monkeypatch.setattr(alerts_mod, "emit_alert", lambda *a, **k: alerts.append(a) or True)
    session.info.update(applied=applied, alerts=alerts)
    yield session
    session.close()


def _seed(db, *, rule_version=OLD, rid="rec_old"):
    old = _sim(osim.SETTLEMENT_RULESET_R20261019)  # 旧规则当时写下的结算
    db.add(_rec(rid))
    db.add(RecommendationOutcome(
        recommendation_id=rid, outcome_label=old["outcome_label"], horizon_days=5, rule_version=rule_version,
        entry_date=old["entry_date"], entry_price=old["entry_price"], exit_date=old["exit_date"],
        exit_price=old["exit_price"], executed_return=old["executed_return"], exit_reason=old["exit_reason"],
        entry_policy_id=old["entry_policy_id"], bars_verified_at=datetime(2026, 10, 26, 12, 0)))
    for b in _bars(datetime(2026, 10, 26, 10, 0)):
        db.add(b)
    db.commit()
    return old


def _rewrite_bars(db):
    """日 K 改写（回补）：值不变，updated_at 变新。"""
    for b in db.query(DailyKbar).all():
        b.updated_at = datetime(2026, 10, 28, 10, 0)
    db.commit()


def test_rule_version_bumped_and_parsed():
    assert svc.OUTCOME_RULE_VERSION == "outcome_simulation_v2+r20261027"
    assert svc.settlement_ruleset_for(OLD) == "r20261019"
    assert svc.settlement_ruleset_for(svc.OUTCOME_RULE_VERSION) == "r20261027"
    assert svc.settlement_ruleset_for("outcome_simulation_v2+r20260901") is None
    assert svc.settlement_ruleset_for(None) is None


def test_two_rulesets_really_differ_for_this_record():
    old, new = _sim("r20261019"), _sim("r20261027")
    assert old["entry_policy_id"] == "causal_strategy_entry_v1" and new["entry_policy_id"] == "backtest_open_window_v1"
    assert (old["entry_price"], old["executed_return"]) != (new["entry_price"], new["executed_return"])


def test_stale_version_alone_does_not_trigger_reprojection(db):
    _seed(db)
    assert svc.find_outcomes_needing_reprojection(db) == []
    assert svc.reproject_revised_outcomes(db)["candidates"] == 0
    assert db.info["applied"] == []


def test_old_record_on_bar_rewrite_recomputes_with_own_version_unchanged(db):
    old = _seed(db)
    _rewrite_bars(db)
    assert svc.find_outcomes_needing_reprojection(db) == ["rec_old"]
    svc.reproject_revised_outcomes(db)
    [call] = db.info["applied"]
    assert call["rule_version"] == OLD  # 版本不升
    sim = call["sim"]
    for key in ("entry_policy_id", "entry_date", "entry_price", "exit_date", "exit_price", "executed_return",
                "exit_reason", "outcome_label", "stop_loss_price"):
        assert sim[key] == old[key], key


def test_unreproducible_version_is_skipped_and_alerted(db):
    _seed(db, rule_version="outcome_simulation_v2+r20260901")
    _rewrite_bars(db)
    out = svc.reproject_revised_outcomes(db)
    assert out["skipped_unreproducible"] == 1 and db.info["applied"] == []
    assert db.info["alerts"] and db.info["alerts"][0][0] == svc.RULE_VERSION_UNREPRODUCIBLE_ALERT


def test_only_explicit_upgrade_uses_new_rules(db):
    _seed(db)
    svc.recompute_stale_outcomes(db, recommendation_ids=["rec_old"], upgrade_rule_version=True)
    [call] = db.info["applied"]
    assert call["rule_version"] == svc.OUTCOME_RULE_VERSION
    assert call["sim"]["entry_policy_id"] == "backtest_open_window_v1"


def test_manual_recompute_endpoint_default_keeps_old_version(db):
    _seed(db)
    svc.recompute_stale_outcomes(db)  # /api/outcomes/recompute 的默认调用
    [call] = db.info["applied"]
    assert call["rule_version"] == OLD and call["sim"]["entry_policy_id"] == "causal_strategy_entry_v1"


def test_old_records_settled_before_entry_low_exit_replay_without_it():
    """r20261019 号下 10-25 03:32 才上线「破买入日最低价出场」：之前结算的记录按没有这条复现。"""
    before = datetime(2026, 10, 20, 10, 0)
    after = datetime(2026, 10, 26, 10, 0)
    assert svc.settlement_ruleset_for(OLD, before) == osim.SETTLEMENT_RULESET_R20261019_PRE_ENTRY_LOW
    assert svc.settlement_ruleset_for(OLD, after) == osim.SETTLEMENT_RULESET_R20261019
    assert svc.settlement_ruleset_for("outcome_simulation_v2+r20261019.pre_el") == "r20261019.pre_el"

    snap = {"entry_mode": "next_open", "exit_strategy_id": "oversold-capital-reversal", "stop_loss": 9.0}
    rec = Recommendation(recommendation_id="r_el", run_id="x", code="600124", name="测试", trade_date="2026-10-15",
                         recommend_price=10.0, final_score=75.0, bucket="oversold-capital-reversal",
                         feature_snapshot_json=json.dumps(snap))
    rows = [("2026-10-16", 10.0, 10.1, 9.8, 10.0), ("2026-10-19", 9.9, 9.95, 9.6, 9.7),
            ("2026-10-20", 9.7, 10.2, 9.6, 10.1), ("2026-10-21", 10.1, 10.4, 10.0, 10.3),
            ("2026-10-22", 10.3, 10.5, 10.2, 10.4), ("2026-10-23", 10.4, 10.6, 10.3, 10.5)]
    bars, prev = [], 10.0
    for d, o, h, lo, c in rows:
        bars.append(DailyKbar(code="600124", trade_date=d, open=o, high=h, low=lo, close=c,
                              pct_chg=round((c / prev - 1) * 100, 4), volume=1e6, amount=1e8))
        prev = c
    dates = [b.trade_date for b in bars]
    with_el = osim._simulate_outcome(rec, bars, dates, horizon_days=5, ruleset="r20261019")
    pre_el = osim._simulate_outcome(rec, bars, dates, horizon_days=5, ruleset="r20261019.pre_el")
    assert with_el["exit_reason"] == "entry_low_close" and with_el["exit_date"] == "2026-10-19"
    assert pre_el["exit_reason"] != "entry_low_close" and pre_el["exit_date"] > "2026-10-19"


def test_resettled_record_keeps_new_confirmation_on_later_bar_rewrite(db):
    """显式重算过的记录：之后日 K 改写触发的重算，执行资格仍按新确认规则推出，不被投影回旧的「已取消」。"""
    from app.services.resettle_confirmation import RESETTLE_LABEL_SOURCE

    _seed(db, rule_version=svc.OUTCOME_RULE_VERSION)
    o = db.get(RecommendationOutcome, "rec_old")
    o.label_source = RESETTLE_LABEL_SOURCE
    db.add(RecommendationExecutionState(recommendation_id="rec_old", latest_decision_code="cancel",
                                        cancelled_before_entry=True, ever_buy_confirmed=False,
                                        latest_checked_at=datetime(2026, 10, 16, 2, 0)))
    db.commit()
    _rewrite_bars(db)
    svc.reproject_revised_outcomes(db)
    [call] = db.info["applied"]
    assert call["label_source"] == RESETTLE_LABEL_SOURCE
    assert call["state"].ever_buy_confirmed is True and call["rule_version"] == svc.OUTCOME_RULE_VERSION
