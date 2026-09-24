"""worker 启动时收尾遗留 RUNNING flow run 的测试（L-145）。

背景数据墙：`launchctl kickstart -k` 重启 worker 会 SIGTERM 杀掉正在执行的 flow
run 子进程。DB 侧有 stale-run-recovery 兜底把 RecommendationRun 标 interrupted，
但 Prefect 侧的 flow run 无人收尾、永久停在 RUNNING。work queue `default` 的
concurrency_limit=2，攒够 2 个僵尸就把并发槽永久占满——worker 显示 ONLINE、心跳
正常，却再也取不到活，之后所有 run 全停在 queued。2026-09-24 实测：两个僵尸
（06:51、06:55）堵死队列，39 个 flow run 积压，页面点「运行推荐」全部无响应。

本测试锁定时间判据：只收尾「本进程启动之前就已在跑」的，绝不误杀活任务。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import run_prefect_worker as worker  # noqa: E402


def test_orphan_from_previous_worker_is_reaped():
    """上次 worker 残留的（start_time 早于本进程启动）必须被判为僵尸。"""
    old = worker._PROCESS_START - timedelta(minutes=10)
    assert worker._started_before_this_process({"start_time": old.isoformat()}) is True


def test_live_flow_run_started_after_process_is_not_reaped():
    """本 worker 启动后自己提交的是活任务，绝不能收尾。

    回归自一次真实误伤：在 worker 运行中手工调用收尾函数，把正在跑的验证 run
    （flow run cobalt-porpoise）直接 CRASHED 掉了。
    """
    fresh = worker._PROCESS_START + timedelta(seconds=30)
    assert worker._started_before_this_process({"start_time": fresh.isoformat()}) is False


def test_zulu_suffix_and_naive_timestamps_are_handled():
    """Prefect 返回的是 ...Z 后缀；naive 时间戳按 UTC 解释，不能因解析失败漏收。"""
    old = (worker._PROCESS_START - timedelta(hours=1)).replace(tzinfo=None)
    assert worker._started_before_this_process({"start_time": old.isoformat() + "Z"}) is True
    assert worker._started_before_this_process({"start_time": old.isoformat()}) is True


def test_missing_or_bad_timestamp_is_skipped_conservatively():
    """取不到 / 解析不了时间时保守跳过——宁可漏收一个僵尸，也不能误杀活任务。"""
    assert worker._started_before_this_process({}) is False
    assert worker._started_before_this_process({"start_time": None}) is False
    assert worker._started_before_this_process({"start_time": "not-a-timestamp"}) is False


def test_falls_back_to_expected_start_time():
    """flow run 被 kill 时可能没写 start_time，退回 expected_start_time。"""
    old = worker._PROCESS_START - timedelta(minutes=5)
    assert worker._started_before_this_process({"expected_start_time": old.isoformat()}) is True


def test_reap_never_raises_when_prefect_unreachable(monkeypatch):
    """Prefect 不可达时只记日志、不得阻塞 worker 启动。"""
    def boom(*_args, **_kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(worker, "_post_json", boom)
    worker._reap_orphan_running_flow_runs()  # 不抛异常即通过
