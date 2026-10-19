"""L-171：正式出票后异步 LLM 风险备注，不改 final_score。"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace


def test_attach_writes_notes_without_touching_score(monkeypatch):
    from app.services import async_llm_risk_notes as mod

    rec = SimpleNamespace(
        code="000001",
        name="测试",
        industry="银行",
        recommend_price=10.0,
        final_score=80.0,
        feature_snapshot_json=json.dumps({"code": "000001", "name": "测试"}),
        skill_breakdown_json=json.dumps([{"strategy_id": "chen-xiaoqun-skill", "score": 80}]),
        agent_summary_json="{}",
        risk_tags_json="[]",
    )
    run = SimpleNamespace(
        selected_skills_json=json.dumps([{"skill_id": "chen-xiaoqun-skill"}]),
        market_snapshot_json="{}",
    )

    class _DB:
        def get(self, _model, _rid):
            return run

        def query(self, _model):
            class _Q:
                def filter(self, *_a, **_k):
                    return self

                def all(self):
                    return [rec]

            return _Q()

        def commit(self):
            return None

    async def _fake_reviews(**_kwargs):
        return [
            {
                "skill_id": "chen-xiaoqun-skill",
                "review": {
                    "stance": "veto",
                    "summary": "风险过高",
                    "score": 20,
                    "score_delta": -10,
                    "action": "不做",
                    "risks": ["高潮回落"],
                    "supports": [],
                },
                "error": None,
            }
        ]

    class _Skill:
        skill_id = "chen-xiaoqun-skill"
        name = "陈小群"

    import app.agents.review_runner as rr
    import app.core.config as cfg
    import app.engine.skill_loader as sl
    import app.services.runs.skill_gate as sg

    monkeypatch.setattr(rr, "run_skill_reviews", _fake_reviews)
    monkeypatch.setattr(sl, "load_skills", lambda _p: [_Skill()])
    monkeypatch.setattr(sg, "resolve_skill_review_concurrency", lambda _p, _s: 1)
    monkeypatch.setattr(cfg, "get_settings", lambda: SimpleNamespace())

    out = asyncio.run(mod.attach_llm_risk_notes_for_run(_DB(), "run-x"))
    assert out["updated"] == 1
    assert rec.final_score == 80.0
    summary = json.loads(rec.agent_summary_json)
    assert summary["async_attached"] is True
    assert summary["stance"] == "veto"
    tags = json.loads(rec.risk_tags_json)
    assert any("LLM风险备注:veto" in t for t in tags)


def test_wait_llm_risk_note_threads_joins_and_reports_timeout():
    import threading
    import time
    from app.services.async_llm_risk_notes import wait_llm_risk_note_threads

    done = threading.Thread(target=lambda: time.sleep(0.05), name="fast", daemon=True)
    stuck = threading.Thread(target=lambda: time.sleep(5), name="slow", daemon=True)
    done.start(); stuck.start()
    out = wait_llm_risk_note_threads([done, None, stuck], timeout_seconds=0.3)
    assert not done.is_alive()
    assert out == {"waited": 2, "timed_out": ["slow"]}


def test_schedule_returns_thread(monkeypatch):
    import app.services.async_llm_risk_notes as mod

    async def fake(db, run_id):
        return {"ok": run_id}

    monkeypatch.setattr(mod, "attach_llm_risk_notes_for_run", fake)
    import app.db.session as sess

    class _S:
        def __enter__(self): return None
        def __exit__(self, *a): return False

    monkeypatch.setattr(sess, "SessionLocal", lambda: _S())
    t = mod.schedule_attach_llm_risk_notes("run-x")
    t.join(5)
    assert not t.is_alive()
