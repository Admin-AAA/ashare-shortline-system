from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from app.orchestration import api


def test_manual_scheduled_task_trigger_uses_one_prefect_deployment(monkeypatch) -> None:
    calls = []
    flow_run_id = uuid4()

    def fake_run_deployment(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(id=flow_run_id)

    monkeypatch.setattr(api, "run_deployment", fake_run_deployment)

    ref = api.trigger_scheduled_task("post_sync_formal_recommendation")

    assert ref.run_id == str(flow_run_id)
    assert ref.deployment_name == "scheduled-task/post_sync_formal_recommendation"
    assert calls == [
        {
            "name": "scheduled-task/post_sync_formal_recommendation",
            "parameters": {
                "task_id": "post_sync_formal_recommendation",
                "trigger_source": "manual",
            },
            "timeout": 0,
        }
    ]


def test_recommendation_trigger_carries_only_durable_run_id(monkeypatch) -> None:
    calls = []
    flow_run_id = uuid4()
    monkeypatch.setattr(
        api,
        "run_deployment",
        lambda **kwargs: (calls.append(kwargs), SimpleNamespace(id=flow_run_id))[1],
    )

    ref = api.trigger_recommendation("run_20260730_demo")

    assert ref.run_id == str(flow_run_id)
    assert calls[0]["parameters"] == {"run_id": "run_20260730_demo"}
    assert calls[0]["timeout"] == 0
