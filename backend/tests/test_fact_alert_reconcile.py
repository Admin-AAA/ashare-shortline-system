"""回归：事实型告警（盘后出票失败 / 晚间补跑失败）在条件恢复后立即解除。

2026-11-15 实况：11-13 的漏票已由自愈补跑补齐 5/5、daily_output_status=ok，
但 alerts_active.json 里 daily_output_failed|2026-11-13、late_repick_failed|2026-11-13
仍挂着——因为解除只挂在「下一次复核作业也跑到」这条链上，补票发生在复核之后。
这类告警要等 24h TTL 才消失，期间面板显示着不成立的红项，还可能被
alerts:stale_active 当成「放着不管的告警」再扣一个黄项。
"""
from __future__ import annotations

from app.services import data_quality_monitor as dq


class _Alerts:
    def __init__(self, records):
        self.records = records
        self.resolved = []
        self.kinds_asked = []

    def active(self, kind=None):
        self.kinds_asked.append(kind)
        return [r for r in self.records if kind is None or r["kind"] == kind]

    def resolve(self, kind, key, message=""):
        self.resolved.append((kind, key, message))
        return True


def _patch(monkeypatch, records, status):
    fake = _Alerts(records)
    monkeypatch.setattr("app.core.alerts.active_alert_records", fake.active)
    monkeypatch.setattr("app.core.alerts.resolve_alert", fake.resolve)
    calls = []

    def _status(db, td):
        calls.append(td)
        if isinstance(status, Exception):
            raise status
        return status

    monkeypatch.setattr("app.services.daily_output_check.daily_output_status", _status)
    return fake, calls


def test_recovered_day_resolves_both_kinds(monkeypatch):
    fake, calls = _patch(monkeypatch, [
        {"kind": "daily_output_failed", "key": "2026-11-13"},
        {"kind": "late_repick_failed", "key": "2026-11-13"},
    ], {"ok": True, "short_done": 5, "short_expected": 5})
    out = dq.reconcile_fact_alerts(object())
    assert out == ["daily_output_failed|2026-11-13", "late_repick_failed|2026-11-13"]
    assert calls == ["2026-11-13", "2026-11-13"]
    assert all(m and "恢复" in m for _, _, m in fake.resolved)
    assert set(fake.kinds_asked) == {"daily_output_failed", "late_repick_failed"}


def test_still_failed_or_pending_day_is_not_resolved(monkeypatch):
    fake, _ = _patch(monkeypatch, [
        {"kind": "daily_output_failed", "key": "2026-11-13"},
        {"kind": "late_repick_failed", "key": "2026-11-13"},
    ], {"ok": False, "status": "failed", "short_done": 0, "short_expected": 5})
    assert dq.reconcile_fact_alerts(object()) == []
    assert fake.resolved == []


def test_malformed_keys_are_skipped(monkeypatch):
    fake, calls = _patch(monkeypatch, [
        {"kind": "late_repick_failed", "key": ""},
        {"kind": "late_repick_failed", "key": "taskrun_b4"},
        {"kind": "daily_output_failed", "key": "2026-11-13"},
    ], {"ok": True, "short_done": 5, "short_expected": 5})
    assert dq.reconcile_fact_alerts(object()) == ["daily_output_failed|2026-11-13"]
    assert calls == ["2026-11-13"]


def test_unreadable_status_does_not_break_reconcile(monkeypatch):
    fake, _ = _patch(monkeypatch, [
        {"kind": "daily_output_failed", "key": "2026-11-13"},
    ], RuntimeError("db down"))
    assert dq.reconcile_fact_alerts(object()) == []
    assert fake.resolved == []


def test_run_check_reconciles_only_when_alerting(monkeypatch):
    monkeypatch.setattr(dq, "run_checks", lambda *a, **kw: {
        "items": [], "summary": {}, "generated_at": "2026-11-15 00:44:00", "overall": "ok"})
    monkeypatch.setattr(dq, "write_report", lambda *a, **kw: None)
    monkeypatch.setattr(dq, "sync_alerts", lambda rep: {"emitted": [], "resolved": []})
    seen = []
    monkeypatch.setattr(dq, "reconcile_fact_alerts", lambda db: seen.append(db) or ["daily_output_failed|2026-11-13"])

    rep = dq.run_data_quality_check(object(), alert=True)
    assert rep["reconciled_alerts"] == ["daily_output_failed|2026-11-13"] and len(seen) == 1

    plain = dq.run_data_quality_check(object(), alert=False)
    assert "reconciled_alerts" not in plain and len(seen) == 1
