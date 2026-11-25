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


def test_attach_disabled_writes_nothing(monkeypatch):
    """停用时直接返回 disabled，不读库也不改任何推荐。"""
    from app.services import async_llm_risk_notes as mod

    monkeypatch.setattr(mod, "LLM_RISK_NOTES_ENABLED", False)
    out = asyncio.run(mod.attach_llm_risk_notes("run-x"))
    assert out == {"run_id": "run-x", "status": "disabled", "updated": 0}


def test_attach_llm_risk_notes_failure_does_not_raise(monkeypatch):
    """备注失败不得向上抛：出票已经落库，备注只是附加信息。"""
    from app.services import async_llm_risk_notes as mod

    async def _boom(_db, _rid):
        raise RuntimeError("upstream 502")

    monkeypatch.setattr(mod, "attach_llm_risk_notes_for_run", _boom)
    out = asyncio.run(mod.attach_llm_risk_notes("run-y"))
    assert out["status"] == "failed"
    assert out["updated"] == 0
    assert "upstream 502" in out["error"]


def test_standalone_runner_attaches_notes_after_completion():
    """出票调度必须在出票落库后挂备注，不得依赖 daemon 线程（会随 Prefect 子进程被杀）。"""
    import inspect
    import app.services.scheduler_service as ss

    runner = inspect.getsource(ss._run_standalone_recommendations)
    helper = inspect.getsource(ss._attach_risk_notes_concurrently)
    assert "_attach_risk_notes_concurrently(" in runner
    assert "attach_llm_risk_notes(" in helper
    assert "asyncio.gather(" in helper
    assert "schedule_attach_llm_risk_notes" not in runner + helper
    assert "threading.Thread" not in runner + helper


def test_attach_risk_notes_concurrently_isolates_failures(monkeypatch):
    """单个 run 的备注异常不得中断其它 run，也不得改出票状态。"""
    import asyncio
    import app.services.async_llm_risk_notes as notes
    import app.services.scheduler_service as ss

    async def _fake(run_id):
        if run_id == "bad":
            raise RuntimeError("upstream 502")
        return {"run_id": run_id, "status": "ok", "updated": 2}

    monkeypatch.setattr(notes, "attach_llm_risk_notes", _fake)
    monkeypatch.setattr(notes, "LLM_RISK_NOTES_ENABLED", True)
    rows = [
        {"skill_id": "a", "run_id": "ok-1", "status": "completed", "recommended_count": 2},
        {"skill_id": "b", "run_id": "bad", "status": "completed", "recommended_count": 1},
        {"skill_id": "c", "run_id": "failed-1", "status": "failed"},
    ]
    asyncio.run(ss._attach_risk_notes_concurrently(rows))
    assert rows[0]["llm_risk_notes"] == "ok"
    assert rows[0]["llm_risk_notes_updated"] == 2
    assert rows[1]["llm_risk_notes"] == "failed"
    assert "llm_risk_notes" not in rows[2]
    assert all(r["status"] != "failed" or r["skill_id"] == "c" for r in rows)
