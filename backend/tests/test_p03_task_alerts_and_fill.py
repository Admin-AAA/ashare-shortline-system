"""P0-3：任务状态一等告警 / 摘要不写“失败 0” / KDJ·BOLL·CCI·vol_ratio 本地复算。"""
import numpy as np
import pandas as pd

from app.core import alerts
from app.services import scheduler_service
from app.services.kdj_boll_cci_fill import compute_kdj_boll_cci
from app.services.scheduler_pure import _data_health_remediation_summary


def test_task_status_alert_emit_and_resolve():
    alerts.reset_alerts()
    scheduler_service._sync_task_status_alert("t_x", "r1", "partial_failed", "x 缺失")
    assert [r["key"] for r in alerts.active_alert_records("task_status")] == ["t_x"]
    scheduler_service._sync_task_status_alert("t_x", "r2", "skipped", "")
    assert alerts.active_alert_records("task_status")  # skipped 不改变告警
    scheduler_service._sync_task_status_alert("t_x", "r3", "completed", "")
    assert alerts.active_alert_records("task_status") == []


def test_summary_counts_partial_failed_without_actions():
    s = _data_health_remediation_summary({"status": "partial_failed", "actions": [], "action_summary": {}})
    assert "失败 1" in s and "失败 0" not in s
    assert "失败 0" in _data_health_remediation_summary({"status": "completed", "actions": [], "action_summary": {}})


def _bars(n=120, seed=1):
    rng = np.random.default_rng(seed)
    close = 10 + np.cumsum(rng.normal(0, 0.2, n))
    high = close + rng.uniform(0.05, 0.3, n)
    low = close - rng.uniform(0.05, 0.3, n)
    return pd.DataFrame({"high": high, "low": low, "close": close,
                         "volume": rng.uniform(1e5, 5e5, n), "adj_factor": 1.0})


def test_kdj_boll_cci_vol_ratio_match_definitions():
    g = _bars()
    r = compute_kdj_boll_cci(g)
    # BOLL(20,2) 总体标准差
    w = g["close"].iloc[-20:]
    assert abs(r["boll_mid"].iloc[-1] - w.mean()) < 1e-3
    assert abs(r["boll_upper"].iloc[-1] - (w.mean() + 2 * w.std(ddof=0))) < 1e-3
    assert np.isnan(r["boll_mid"].iloc[18]) and not np.isnan(r["boll_mid"].iloc[19])
    # KDJ：J = 3K - 2D，范围合理
    assert abs(r["kdj_j"].iloc[-1] - (3 * r["kdj_k"].iloc[-1] - 2 * r["kdj_d"].iloc[-1])) < 1e-3
    # CCI(14)
    tp = (g["high"] + g["low"] + g["close"]) / 3
    t = tp.iloc[-14:]
    expect = (t.iloc[-1] - t.mean()) / (0.015 * np.abs(t - t.mean()).mean())
    assert abs(r["cci"].iloc[-1] - expect) < 1e-3
    # vol_ratio：含当日的均量
    assert abs(r["vol_ratio_5"].iloc[-1] - g["volume"].iloc[-1] / g["volume"].iloc[-5:].mean()) < 1e-3
    assert abs(r["vol_ratio_20"].iloc[-1] - g["volume"].iloc[-1] / g["volume"].iloc[-20:].mean()) < 1e-3
    assert np.isnan(r["vol_ratio_20"].iloc[18])  # 窗口不足 → NULL 而不是 0


def test_flat_series_has_no_inf_or_zero_division():
    g = _bars(40)
    g["high"] = g["low"] = g["close"] = 10.0
    r = compute_kdj_boll_cci(g)
    assert not np.isinf(r.to_numpy(dtype=float)).any()
    assert r["kdj_k"].iloc[-1] == 50.0  # 无波动时 RSV 取 50
    assert np.isnan(r["cci"].iloc[-1])  # AVEDEV=0 → NULL


class _Err:
    def __init__(self, msg, code="fetch_failed"):
        self.message = msg
        self.exception_type = "RuntimeError"
        self.stage = type("S", (), {"value": "fetch"})()
        self.code = type("C", (), {"value": code})()


class _Item:
    def __init__(self, key, errors=(), count=0):
        self.dataset_key = key
        self.errors = tuple(errors)
        self.coverage = type("Cov", (), {"record_count": count})()


def _aux(items):
    return type("A", (), {"orchestrated": type("O", (), {"results": tuple(items)})()})()


def test_retry_only_rate_limited_datasets(monkeypatch):
    import app.services.auxiliary.application as app_mod
    import time as time_mod

    calls = []
    monkeypatch.setattr(time_mod, "sleep", lambda s: calls.append(("sleep", s)))
    monkeypatch.setattr(app_mod, "run_auxiliary_application",
                        lambda db, **kw: calls.append(("run", kw["datasets"])) or _aux([_Item("margin_detail", (), 100)]))
    rl = _Item("margin_detail", [_Err("providers=promax:http_rate_limit")])
    quota = _Item("holder_number", [_Err("quantdata:quota_exhausted(skipped: daily quota exhausted today)")])
    result = {"batch_sync": {"margin_detail": {"status": "failed"}, "holder_number": {"status": "failed"}}}
    scheduler_service._retry_rate_limited_aux_datasets(None, _aux([rl, quota]), result, "2026-10-23", {})
    assert ("run", ("margin_detail",)) in calls  # 配额耗尽的 holder_number 不重试
    assert result["batch_sync"]["margin_detail"]["status"] == "success"
    assert result["batch_sync"]["margin_detail"]["retried"] is True
    assert result["batch_sync"]["holder_number"] == {"status": "failed"}
