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


def test_routing_table_drift_detects_unsupported_provider_still_in_order():
    from app.services.datasource.capability import routing_table_drift

    matrix = {"rows": [
        {"provider": "promax", "api": "hsgt_top10", "status": "no_permission", "rate_limit_per_minute": 100},
        {"provider": "quantdata", "api": "hsgt_top10", "status": "ok", "rate_limit_per_minute": 100, "field_completeness": 1},
    ]}
    stale = {"apis": {"hsgt_top10": {"order": ["promax", "quantdata"], "unsupported": []}}}
    fixed = {"apis": {"hsgt_top10": {"order": ["quantdata"], "unsupported": ["promax"]}}}
    assert any("hsgt_top10" in d and "promax" in d for d in routing_table_drift(matrix, stale))
    assert routing_table_drift(matrix, fixed) == []


def test_committed_routing_table_matches_known_permissions():
    import json
    from app.services.datasource.capability import ROUTING_TABLE

    apis = json.loads(ROUTING_TABLE.read_text())["apis"]
    for api in ("hsgt_top10", "moneyflow_hsgt"):
        assert "promax" in apis[api]["unsupported"] and "promax" not in apis[api]["order"]
    assert apis["stk_surv"]["order"]


def test_hsgt_all_zero_row_is_treated_as_missing():
    import pandas as pd
    from app.services.auxiliary import state_adapters as sa

    class _Client:
        def moneyflow_hsgt(self, trade_date):
            return pd.DataFrame([{"trade_date": trade_date, "north_money": None, "south_money": None, "hgt": None, "sgt": None}])

    provider = sa.TushareStateProvider(_Client(), None)
    req = type("R", (), {"trade_date": __import__("datetime").date(2026, 10, 19)})()
    assert provider.hsgt_flow(req) == ()

    class _Real(_Client):
        def moneyflow_hsgt(self, trade_date):
            return pd.DataFrame([{"trade_date": trade_date, "north_money": "277155.11", "south_money": "55441.78",
                                  "hgt": "134535.72", "sgt": "142619.39"}])

    rows = sa.TushareStateProvider(_Real(), None).hsgt_flow(req)
    assert len(rows) == 1 and abs(rows[0]["north_money"] - 27.715511) < 1e-6


def test_portfolio_calibre_is_same_below_and_above_200_trades():
    """同一批「每天 2 笔」的交易，100 笔和 300 笔都按日等权口径，并带口径标签。"""
    from app.services.evaluation_metrics import PORTFOLIO_CALIBRE_DAILY, compute_full_metrics

    def trades(n_days):
        out = []
        for d in range(n_days):
            day = f"2026-01-{(d % 28) + 1:02d}" if n_days <= 28 else f"2025-{(d // 28) % 12 + 1:02d}-{(d % 28) + 1:02d}"
            for r in (4.0, -2.0):
                out.append({"trade_date": day, "code": f"{600000 + d % 90}", "net_return_pct": r, "gross_return_pct": r,
                            "benchmark_return_pct": 0.0, "resolved": True, "holding_days": 1, "executed": True})
        return out

    small = compute_full_metrics(trades(20))      # 40 笔
    large = compute_full_metrics(trades(150))     # 300 笔
    for m in (small, large):
        assert m["portfolio_calibre"] == PORTFOLIO_CALIBRE_DAILY and m["portfolio_calibre_label"]
    # 每日组合收益都是 +1%（(4-2)/2）：小样本 20 天复利 (1.01^20-1)=22.019%，与大样本口径同一公式
    assert abs(small["strategy_compound_return"] - ((1.01 ** 20 - 1) * 100)) < 0.01
    assert small["max_drawdown"] == 0.0


def test_strategy_profile_refresh_only_runs_pending_and_caps_runs(monkeypatch):
    import app.services.backtest.strategy_profile as sp

    class T:
        def __init__(self, sid):
            self.strategy_id, self.strategy_version = sid, "v2"

    class FakeProfiles:
        refreshed = []

        def __init__(self, db):
            pass

        def current_targets(self):
            return [T("a"), T("b"), T("c")]

        def read_rolled(self, t):
            return {"ok": 1} if t.strategy_id == "a" else None  # a 已有画像

        def refresh(self, t):
            FakeProfiles.refreshed.append(t.strategy_id)
            return {"status": "completed", "reused": False}

    monkeypatch.setattr(sp, "StrategyBacktestProfiles", FakeProfiles)
    out = scheduler_service._run_strategy_profile_refresh(None, {"max_runs": 1})
    assert FakeProfiles.refreshed == ["b"]  # 每次最多 1 个，已有画像的 a 不跑
    assert out["pending_before"] == 2 and out["deferred"] == 1 and "status" not in out
