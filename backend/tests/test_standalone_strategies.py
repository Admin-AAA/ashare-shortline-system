"""单策略独立运行（standalone_strategies + 调度 + 跨 run 去重）单测；只用临时 sqlite。"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.engine.production_policy import load_production_selection_policy
from app.services import standalone_strategies as ss


def _skill(sid, enabled=True, executable=True, name=None):
    return SimpleNamespace(skill_id=sid, enabled=enabled, executable=executable, name=name or sid)


def test_strategy_ids_exclude_retired_disabled_and_warning():
    skills = [
        _skill("chen-xiaoqun-skill"),
        _skill("within-industry-reversal"),
        _skill("trend-pullback-continuation"),  # retired even if enabled
        _skill("overnight-holding-method"),
        _skill("daily-stock-analysis-skill"),
        _skill("high-position-volume-stall-warning"),
        _skill("sector-laggard-catchup", enabled=False),
        _skill("no-contract", executable=False),
    ]
    assert ss.standalone_strategy_ids(skills) == ["chen-xiaoqun-skill", "within-industry-reversal"]


def test_run_settings_respect_formal_floor(monkeypatch):
    monkeypatch.setitem(ss.STRATEGY_RUN_OVERRIDES, "x", {"score_line": 50, "output_count": 5})
    assert ss.standalone_run_settings("x") == (72.0, 5)
    assert ss.standalone_run_settings("y") == (ss.DEFAULT_SCORE_LINE, ss.DEFAULT_OUTPUT_COUNT)


def test_standalone_skill_id_and_run_matching():
    assert ss.standalone_skill_id([{"skill_id": "a"}]) == "a"
    assert ss.standalone_skill_id([{"skill_id": "a"}, {"skill_id": "b"}]) is None
    assert ss.is_run_of_strategy(json.dumps([{"skill_id": "a", "weight": 1}]), "a")
    assert not ss.is_run_of_strategy(json.dumps([{"skill_id": "a"}, {"skill_id": "b"}]), "a")
    assert not ss.is_run_of_strategy("not json", "a")


def test_standalone_policy_drops_portfolio_gates():
    base = load_production_selection_policy()
    assert base.consensus_min_signals == 2
    pol = ss.standalone_selection_policy(base, "chen-xiaoqun-skill", 3)
    assert pol.consensus_min_signals == 0
    assert pol.daily_output_limit == 3
    assert pol.high_turnover_veto_pct == base.high_turnover_veto_pct  # 风险否决保留
    sb = ss.standalone_selection_policy(base, "second-board-leader", 4)
    assert sb.high_turnover_veto_pct is None and sb.high_chg20_veto is None
    assert sb.max_dd60_veto is None and sb.weak_low_ivoldn20_veto_enabled is False


@pytest.fixture()
def db(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models.tables import Base

    engine = create_engine(f"sqlite:///{tmp_path/'t.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _add_run(db, run_id, skills, codes, trade_date="2026-10-13"):
    from app.models.tables import Recommendation, RecommendationRun

    db.add(RecommendationRun(
        run_id=run_id, run_mode="formal", trade_date=trade_date, status="completed",
        evaluation_scope="live_forward", evidence_class="live_forward",
        selected_skills_json=json.dumps([{"skill_id": s} for s in skills]),
    ))
    for code in codes:
        db.add(Recommendation(
            recommendation_id=f"{run_id}-{code}", run_id=run_id, trade_date=trade_date,
            code=code, name=code, industry="银行", final_score=80.0,
        ))
    db.commit()


def test_same_day_dedup_is_scoped_to_strategy(db, monkeypatch):
    from app.services import workbench_service as ws

    monkeypatch.setattr(ws, "live_forward_run_conditions", lambda: ())
    _add_run(db, "r-a", ["a"], ["000001"])
    _add_run(db, "r-b", ["b"], ["000002"])
    _add_run(db, "r-ab", ["a", "b"], ["000003"])
    assert ws._same_day_recommended_codes(db, "2026-10-13") == {"000001", "000002", "000003"}
    assert ws._same_day_recommended_codes(db, "2026-10-13", skill_scope="a") == {"000001"}
    assert ws._recent_recommended_codes(db, "2026-10-13", skill_scope="b") == {"000002"}
    assert sum(ws._same_day_industry_counts(db, "2026-10-13", skill_scope="a").values()) == 1


def test_run_standalone_recommendations_one_run_per_strategy(monkeypatch):
    from app.services import scheduler_service as sched
    from app.services import workbench_service as ws

    monkeypatch.setattr(ws, "sync_skills", lambda db: [
        _skill("a", name="甲"), _skill("b", name="乙"), _skill("trend-pullback-continuation"),
    ])
    monkeypatch.setattr(sched, "STANDALONE_ENQUEUE_STAGGER_SECONDS", 0)
    sent = []

    async def fake_run(db, payload):
        sent.append(payload)
        if payload["single_skill_id"] == "b":
            raise RuntimeError("boom")
        return {"related_run_id": f"run-{payload['single_skill_id']}"}

    async def fake_wait(db, run_id, timeout_seconds=0):
        return {"status": "completed", "recommended_count": 2}

    monkeypatch.setattr(sched, "_run_recommendation", fake_run)
    monkeypatch.setattr(sched, "_wait_for_recommendation_run", fake_wait)

    class _DB:
        def rollback(self):
            pass

    out = asyncio.run(sched._run_standalone_recommendations(
        _DB(), {"preset_id": "preset_formal_opening", "selected_skill_ids": ["a", "b"], "score_line": 60, "output_count": 6},
    ))
    assert [p["single_skill_id"] for p in sent] == ["a", "b"]
    for p in sent:
        assert "selected_skill_ids" not in p and "skills" not in p
        assert p["run_mode"] == "formal" and p["score_line"] >= 72 and p["output_count"] == ss.DEFAULT_OUTPUT_COUNT
    assert out["strategy_count"] == 2 and out["completed_count"] == 1
    assert out["final_run"]["status"] == "completed" and out["recommended_count"] == 2
    assert {r["skill_id"]: r["status"] for r in out["strategy_runs"]} == {"a": "completed", "b": "failed"}
