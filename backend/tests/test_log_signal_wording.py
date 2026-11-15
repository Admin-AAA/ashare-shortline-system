"""回归：ERROR 日志口径（告警通知不计入错误条数）。

背景（2026-11-15）：alerts.emit_alert 用 logger.error 落盘，scan_log_signals 曾把所有
ERROR 行计入 error_count。近 24h 35 条 ERROR 里 31 条是 [ALERT:*] 告警通知，真实应用异常只有 4 条
（11-13 机器离线留下的 Prefect 崩溃）。结果 log:error_volume 实际在数告警条数：告警越多越容易
顶破 >30 阈值变黄，黄项又发一轮告警，形成自激，同时把真实异常淹没。

小石分钟线巡检已随该源退役删除（2026-11-15），本文件只保留日志口径回归。
"""
from __future__ import annotations

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
