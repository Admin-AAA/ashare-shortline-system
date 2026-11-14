"""回归：ERROR 日志口径 + 小石上游失败时的巡检文案。

背景（2026-11-15）：alerts.emit_alert 用 logger.error 落盘，scan_log_signals 曾把所有
ERROR 行计入 error_count。近 24h 35 条 ERROR 里 31 条是 [ALERT:*] 告警通知，真实应用异常只有 4 条
（11-13 机器离线留下的 Prefect 崩溃）。结果 log:error_volume 实际在数告警条数：告警越多越容易
顶破 >30 阈值变黄，黄项又发一轮告警，形成自激，同时把真实异常淹没。

另一处：小石分钟线巡检在已确认「上游 catalog 不可达」时仍固定追加「另一补数任务建设中」，
两条互斥的处置路径写在同一条 detail 里，排查时会被带偏。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.services import data_quality_monitor as dq

CST = timezone(timedelta(hours=8))
_NOW = datetime(2026, 11, 15, 0, 44, tzinfo=CST)
_TS = "2026-11-15 00:30:00"


def _alert_line(i: int) -> str:
    return f"{_TS} [ERROR] [pid 1] ashare.alerts: [ALERT:data_quality] 第 {i} 条巡检告警 | key=k{i}"


def _real_line(i: int, msg: str = "Engine execution exited with unexpected exception") -> str:
    return f"{_TS} [ERROR] [pid 1] prefect.flow_runs: {msg} #{i}"


def test_alert_notifications_excluded_from_error_count():
    blob = "\n".join([*(_alert_line(i) for i in range(31)), *(_real_line(i) for i in range(4))])
    sig = dq.scan_log_signals(blob, _NOW)
    assert sig["error_count"] == 4
    assert sig["alert_count"] == 31
    assert sig["top_errors"] and "prefect.flow_runs" in sig["top_errors"][0][0]
    assert all("ALERT" not in s for s, _ in sig["top_errors"])


def test_real_errors_still_surface_in_top_signatures():
    blob = "\n".join([*(_alert_line(i) for i in range(20)), *(_real_line(i) for i in range(2))])
    sig = dq.scan_log_signals(blob, _NOW)
    assert sig["error_count"] == 2
    assert sig["top_errors"][0][1] == 2


def test_threshold_only_counts_real_errors(tmp_path):
    f = tmp_path / "app.log"
    f.write_text("\n".join([*(_alert_line(i) for i in range(40)), *(_real_line(i) for i in range(4))]))
    items = {i["key"]: i for i in dq.check_log_signals(_NOW, f)}
    it = items["log:error_volume"]
    assert it["severity"] == dq.GREEN
    assert "4 条应用异常 ERROR" in it["detail"]
    assert "40 条告警通知" in it["detail"]


def test_real_error_volume_still_flags_yellow(tmp_path):
    f = tmp_path / "app.log"
    f.write_text("\n".join(_real_line(i) for i in range(31)))
    items = {i["key"]: i for i in dq.check_log_signals(_NOW, f)}
    assert items["log:error_volume"]["severity"] == dq.YELLOW


def _xiaoshi_state(tmp_path, sync_log: str | None):
    d = tmp_path / "xiaoshi_min_all"
    d.mkdir()
    (d / "state.json").write_text(json.dumps({
        "files": {"a": {"last_date": "2026-11-13"}},
        "last_run": {"ts": "2026-11-09 20:43:09", "downloaded": 30, "errors": 4},
    }), encoding="utf-8")
    if sync_log is not None:
        (d / "sync.log").write_text(sync_log, encoding="utf-8")
    return d / "state.json"


def test_xiaoshi_upstream_failure_uses_unambiguous_wording(tmp_path):
    st = _xiaoshi_state(tmp_path, json.dumps({
        "ts": "2026-11-14 20:40:09", "fatal": "Xiaoshi 网络请求失败: /api/v3/history/catalog"}) + "\n")
    item = dq.check_xiaoshi_min_all(["2026-11-13"], "2026-11-13", now=_NOW, state_path=st)
    assert item["upstream_error"]
    assert "临时阈值" not in item["detail"]
    assert "另一补数任务" not in item["detail"]
    assert "本地补数任务无法回补" in item["detail"]
    assert "上游同步失败" in item["detail"]
    assert "最近一次成功同步" in item["detail"]


def test_xiaoshi_without_upstream_failure_keeps_provisional_wording(tmp_path):
    st = _xiaoshi_state(tmp_path, json.dumps({
        "ts": "2026-11-13 20:40:09", "downloaded": 5, "errors": 0}) + "\n")
    item = dq.check_xiaoshi_min_all(["2026-11-13"], "2026-11-13", now=_NOW, state_path=st)
    assert item.get("upstream_error") is None
    assert "临时阈值" in item["detail"]
    assert "本地补数任务无法回补" not in item["detail"]
