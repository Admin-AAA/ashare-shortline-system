"""T12 稳定性：scheduled formal run 必须走独立子进程，而非后端进程内同步执行。

回归背景：原先 scheduler_service._run_recommendation 用 asyncio.run(run_workbench(...))
在后端进程内同步执行，后端一旦重启/热重载会杀死正在跑的 formal run
（error=「服务重启或进程退出」），导致 A 项（每日可见推荐）断档。

现改为 enqueue_workbench_run 路径（与 API 手动触发一致），由
_spawn_run_subprocess(start_new_session=True) 派发到独立子进程，后端重启后子进程
继续更新心跳，recover_interrupted_runs 不会误杀存活任务。

这些测试 mock 掉 enqueue/run_workbench，仅验证路由逻辑（不真正派生子进程、不写库）。
"""

import asyncio

from app.services import scheduler_service


def test_run_recommendation_enqueues_detached_subprocess_when_preset_present(monkeypatch, db):
    enqueue_calls = []
    run_wb_calls = []

    def fake_enqueue(_db, payload):
        enqueue_calls.append(dict(payload))
        return {
            "run": {"run_id": "run_detached_abc", "recommended_count": 0},
            "recommendations": [],
            "queued": True,
        }

    async def fake_run_workbench(_db, payload):
        run_wb_calls.append(dict(payload))
        return {"run": {"run_id": "run_inline_xyz", "recommended_count": 3}, "recommended_count": 3}

    monkeypatch.setattr("app.services.workbench_service.enqueue_workbench_run", fake_enqueue)
    monkeypatch.setattr("app.services.workbench_service.run_workbench", fake_run_workbench)

    payload = {"preset_id": "preset_formal_opening", "data_source": "local_pool_preferred", "score_line": 72}
    result = asyncio.run(scheduler_service._run_recommendation(db, payload))

    # 关键断言：有 preset_id 时必须走 enqueue（独立子进程），绝不走进程内 run_workbench
    assert enqueue_calls, "expected enqueue_workbench_run to be called for preset-based formal run"
    assert not run_wb_calls, "in-process run_workbench must NOT run when preset_id is present (T12 回归)"
    assert result["queued"] is True
    assert result["recommended_count"] == 0, "入队即返回，实际数量由子进程完成后写入"
    assert result["related_run_id"] == "run_detached_abc"
    assert "独立子进程" in result["summary_text"]


def test_run_recommendation_falls_back_to_inline_when_no_preset(monkeypatch, db):
    enqueue_calls = []
    run_wb_calls = []

    def fake_enqueue(_db, payload):
        enqueue_calls.append(dict(payload))
        return {"run": {"run_id": "run_detached_abc"}, "queued": True}

    async def fake_run_workbench(_db, payload):
        run_wb_calls.append(dict(payload))
        return {"run": {"run_id": "run_inline_xyz", "recommended_count": 3}, "recommended_count": 3}

    monkeypatch.setattr("app.services.workbench_service.enqueue_workbench_run", fake_enqueue)
    monkeypatch.setattr("app.services.workbench_service.run_workbench", fake_run_workbench)

    payload = {"data_source": "local_pool_preferred"}  # 无 preset_id
    result = asyncio.run(scheduler_service._run_recommendation(db, payload))

    # 无 preset_id 时 enqueue 前置条件不满足，退化为进程内执行（保留历史行为）
    assert run_wb_calls, "expected in-process run_workbench fallback when preset_id missing"
    assert not enqueue_calls, "enqueue must NOT be called without preset_id"
    assert result.get("queued") is not True
    assert result["recommended_count"] == 3


def test_run_recommendation_passes_through_run_params(monkeypatch, db):
    captured = {}

    def fake_enqueue(_db, payload):
        captured.update(payload)
        return {"run": {"run_id": "run_params_1"}, "queued": True}

    monkeypatch.setattr("app.services.workbench_service.enqueue_workbench_run", fake_enqueue)
    monkeypatch.setattr("app.services.workbench_service.run_workbench", lambda _db, _p: {"run": {}, "recommended_count": 0})

    payload = {
        "preset_id": "preset_formal_opening",
        "run_name": "参数透传测试",
        "data_source": "local_pool_preferred",
        "risk_template": "balanced",
        "score_line": 72,
        "output_count": 6,
        "skip_aux_refresh": True,
    }
    asyncio.run(scheduler_service._run_recommendation(db, payload))

    assert captured["preset_id"] == "preset_formal_opening"
    assert captured["run_name"] == "参数透传测试"
    assert captured["data_source"] == "local_pool_preferred"
    assert captured["risk_template"] == "balanced"
    assert captured["score_line"] == 72
    assert captured["output_count"] == 6
    assert captured["skip_aux_refresh"] is True
