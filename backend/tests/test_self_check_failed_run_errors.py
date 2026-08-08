"""T12 failed run 错误类型分类测试（_failed_run_error_types）。

锁定 A 项 failed run 归因：区分代码 bug（TypeError/AttributeError）vs 数据缺失 vs
LLM 超时 vs 运维问题 vs 管线契约问题。07-22 03:52 NoneType __round__ 是代码 bug（5bb9f74 已修），
self_check 原只报「failed rec_n=0」不分类型，用户难快速定位根因。
"""
import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = BACKEND_ROOT.parent / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import self_check  # type: ignore[import-not-found]


def test_code_bug_typeerror():
    types = self_check._failed_run_error_types(["type NoneType doesn't define __round__ method"])
    assert types == {"code_bug": 1}


def test_code_bug_attributeerror():
    types = self_check._failed_run_error_types(["AttributeError: 'NoneType' object has no attribute 'split'"])
    assert types == {"code_bug": 1}


def test_data_missing():
    types = self_check._failed_run_error_types(["构建特征失败：daily_kbars 缺失"])
    assert types == {"data_missing": 1}


def test_llm_timeout():
    types = self_check._failed_run_error_types(["Skill 深审超时 (>300s)"])
    assert types == {"llm_timeout": 1}


def test_process_restart():
    types = self_check._failed_run_error_types(["服务重启或进程退出，后台任务已中断"])
    assert types == {"process": 1}


def test_pipeline_contract_errors_are_actionable():
    types = self_check._failed_run_error_types([
        "策略/预设注册诊断失败：preset 引用了不可执行策略",
        "reconstruction worker revision does not match its batch manifest",
    ])
    assert types == {"pipeline_contract": 2}


def test_other_unclassified():
    types = self_check._failed_run_error_types(["未知错误XYZ"])
    assert types == {"other": 1}


def test_mixed_types_count():
    msgs = [
        "type NoneType doesn't define __round__",
        "Skill 深审超时",
        "Skill 深审超时",
        "服务重启",
        "未知",
    ]
    types = self_check._failed_run_error_types(msgs)
    assert types == {"code_bug": 1, "llm_timeout": 2, "process": 1, "other": 1}


def test_empty_returns_empty():
    assert self_check._failed_run_error_types([]) == {}


def test_none_message_other():
    types = self_check._failed_run_error_types([None])
    assert types == {"other": 1}
