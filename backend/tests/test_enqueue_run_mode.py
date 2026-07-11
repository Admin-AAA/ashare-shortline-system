"""enqueue_workbench_run 必须尊重调用方显式指定的 run_mode。

回归背景：原先 run_mode 硬编码为 "formal"，导致曝光补跑（strategy_exposure）
传入的 run_mode="experiment" 意图失效 —— 补跑 run 被错误创建为 formal，
污染记录中心与正式胜率统计（experiment 本应不进记录中心）。
"""
from app.models.tables import RecommendationRun
from app.services.workbench_service import enqueue_workbench_run


def test_enqueue_respects_experiment_run_mode(monkeypatch, db):
    # 避免真正派生子进程
    monkeypatch.setattr(
        "app.services.workbench_service._spawn_run_subprocess", lambda *a, **k: None
    )
    payload = {
        "single_skill_id": "chip-single-peak-breakout",
        "run_mode": "experiment",
        "run_name": "曝光补跑测试",
        "skip_aux_refresh": True,
        "disable_agent_review": True,
    }
    res = enqueue_workbench_run(db, payload)
    run_id = res["run"]["run_id"]
    run = db.get(RecommendationRun, run_id)
    assert run is not None
    assert run.run_mode == "experiment"


def test_enqueue_defaults_to_formal_run_mode(monkeypatch, db):
    monkeypatch.setattr(
        "app.services.workbench_service._spawn_run_subprocess", lambda *a, **k: None
    )
    payload = {
        "single_skill_id": "chip-single-peak-breakout",
        "run_name": "默认 formal 测试",
        "skip_aux_refresh": True,
        "disable_agent_review": True,
    }
    res = enqueue_workbench_run(db, payload)
    run_id = res["run"]["run_id"]
    run = db.get(RecommendationRun, run_id)
    assert run.run_mode == "formal"
