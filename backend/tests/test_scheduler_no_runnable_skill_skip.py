"""T12-adjacent: 预设无可用 Skill 时，定时推荐任务应优雅跳过而非报错。

回归保护：preclose_overnight_holding_recommendation 指向的预设 ef480d33af98
仅含已禁用(下线)的 overnight-holding-method，曾每日 14:30 抛
ValueError("没有可运行的 Skill") 污染 ERROR 日志并标记为 failed。
"""

import pytest

from app.services import scheduler_service as ss


def test_no_runnable_skill_valueerror_becomes_skip():
    payload = {"preset_id": "ef480d33af98"}
    exc = ValueError("没有可运行的 Skill，请检查预设配置或 Skill 状态")
    skip = ss._recommendation_run_error_to_skip("preclose_overnight_holding_recommendation", payload, exc)
    assert skip is not None
    assert skip.get("status") == "skipped"
    assert skip.get("skipped") is True
    assert "ef480d33af98" in skip.get("summary_text", "")


def test_other_valueerror_propagates_none():
    payload = {"preset_id": "preset_formal_opening"}
    exc = ValueError("预设不存在，请刷新后重试")
    assert ss._recommendation_run_error_to_skip("some_task", payload, exc) is None


def test_non_valueerror_propagates_none():
    payload = {"preset_id": "preset_formal_opening"}
    exc = RuntimeError("boom")
    assert ss._recommendation_run_error_to_skip("some_task", payload, exc) is None


def test_task_result_status_skipped():
    assert ss._task_result_status({"status": "skipped"}) == "skipped"


def test_task_result_status_unknown_stays_completed():
    # 非 skip 的普通结果不应被误判为 skipped
    assert ss._task_result_status({"status": "completed"}) == "completed"
