"""滚动近12个月重算的抗故障：单个策略抛异常只跳过它、本地财报缺列不悄悄返回残表、失败进数据健康红项。纯逻辑，不连库。"""
from __future__ import annotations

import contextlib as cl
import datetime as dt
import json

import pandas as pd

from app.services import data_quality_monitor as dq
from app.services import midterm_service as ms
from app.services import rolling_r12 as rr


# ---------------------------------------------------------------- generate_picks 单策略隔离
def _fake_loop(boom: set[str]):
    def loop(db, as_of, d, force, horizon, strategy_ids, result, apply_daily_cap=False, gate=None):
        for sid in ["a", "b", "c"]:
            if strategy_ids and sid not in strategy_ids:
                continue
            result["__current__"] = sid
            if sid in boom:
                raise KeyError("top_n")
            result[sid] = 3
    return loop


def test_generate_picks_isolate_errors_skips_only_the_bad_strategy(monkeypatch, tmp_path):
    monkeypatch.setattr(ms, "OUT_DIR", tmp_path)
    monkeypatch.setattr(ms, "STRATEGIES", {"a": {}, "b": {}, "c": {}})
    monkeypatch.setattr(ms, "_generate_picks_loop", _fake_loop({"b"}))
    out = ms.generate_picks(None, "2026-11-02", isolate_errors=True)
    assert out["a"] == 3 and out["c"] == 3  # 出错策略后面的照算
    assert out["b"]["exception"] is True and "top_n" in out["b"]["error"]
    assert "__current__" not in out


def test_generate_picks_default_still_raises(monkeypatch, tmp_path):
    """实盘入口不开隔离：出错照旧抛，避免悄悄少出票。"""
    monkeypatch.setattr(ms, "OUT_DIR", tmp_path)
    monkeypatch.setattr(ms, "STRATEGIES", {"a": {}, "b": {}, "c": {}})
    monkeypatch.setattr(ms, "_generate_picks_loop", _fake_loop({"b"}))
    try:
        ms.generate_picks(None, "2026-11-02")
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError")


# ---------------------------------------------------------------- replay_midlong 记 crashed、不重试
def test_replay_midlong_records_crashed_without_retry(monkeypatch, tmp_path):
    from scripts import rebuild_replay_picks as rpk

    monkeypatch.setattr(ms, "OUT_DIR", tmp_path)
    monkeypatch.setattr(rpk, "_trade_dates", lambda db, s, e: ["2026-10-15", "2026-10-16"])
    monkeypatch.setattr(rpk, "_local_forecast_reader", lambda api: api)
    monkeypatch.setattr(rpk.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("should not retry")))
    calls = []

    def fake_gen(db, as_of, **k):
        calls.append(k)
        return {"a": 1, "b": {"error": "KeyError: 'top_n'", "exception": True}}

    monkeypatch.setattr(ms, "generate_picks", fake_gen)
    stats = rpk.replay_midlong(None, "2026-10-15", "2026-10-16", None, tmp_path / "stg" / "picks",
                               retry_waits=(60,), isolate_errors=True)
    assert all(k.get("isolate_errors") is True for k in calls) and len(calls) == 2  # 每天只算一次
    assert stats["crashed"]["b"] == {"first_day": "2026-10-15", "error": "KeyError: 'top_n'", "days": 2}
    assert stats["errors"]["b"] == ["2026-10-15", "2026-10-16"]


# ---------------------------------------------------------------- 本地财报缺列回落网关
def test_local_first_reads_extra_fina_columns_and_falls_back_when_missing(monkeypatch, tmp_path):
    (tmp_path / "fina").mkdir()
    (tmp_path / "dividend").mkdir()
    (tmp_path / "events").mkdir()
    row = {c: None for c in rr._LOCAL_FIELDS} | {"saleexp_to_gr": 8.5}
    row.update(ts_code="000001.SZ", ann_date="20260825", end_date="20260630")
    pd.DataFrame([row]).to_parquet(tmp_path / "fina" / "000001.parquet")
    upstream = []
    monkeypatch.setattr(ms, "_pro_fetch_with_wait", lambda api, label, alt_params=None, **p: upstream.append(api))
    monkeypatch.setattr(ms, "generate_picks", lambda db, as_of, *a, **k: None)
    with rr.local_first(tmp_path):
        ms.generate_picks(None, "2026-08-20")  # 本地财报覆盖到 08-25，重放日在其内 -> 读本地
        f = ms._pro_fetch_with_wait("fina_indicator_vip", "x", period="20260630", fields="ts_code,saleexp_to_gr")
        assert list(f.columns) == ["ts_code", "saleexp_to_gr"] and f["saleexp_to_gr"].iloc[0] == 8.5
        # 本地文件里没有的列：不能返回少列的表，要走网关
        ms._pro_fetch_with_wait("fina_indicator_vip", "x", period="20260630", fields="ts_code,no_such_col")
    assert upstream == ["fina_indicator_vip"]


# ---------------------------------------------------------------- run_monthly：有策略崩了，其余照算、文件照写
def test_run_monthly_crashed_strategy_marked_error_others_kept(monkeypatch, tmp_path):
    monkeypatch.setattr(rr, "_latest_trade_date", lambda db: "2026-11-02")
    monkeypatch.setattr(rr, "local_first", lambda: cl.nullcontext({"calls": {}, "local": {}, "covered": {}}))
    monkeypatch.setattr("scripts.rebuild_replay_picks.replay_midlong", lambda *a, **k: {
        "days": 10, "seconds": 1, "errors": {"mid-b": ["2026-10-15"]}, "no_data": {},
        "crashed": {"mid-b": {"first_day": "2025-07-25", "error": "KeyError: 'top_n'", "days": 10}}})
    ok = {"name": "x", "horizon": "mid", "hold_days": 20, "n": 40, "avg": 1.0, "verdict": "positive"}
    monkeypatch.setattr(rr, "midlong_window_stats", lambda *a: {
        "mid-a": dict(ok), "mid-b": {**ok, "n": 3, "verdict": "insufficient_sample"}})
    monkeypatch.setattr(rr, "short_window_stats", lambda *a: {
        "strategies": {}, "replay_coverage_end": "2026-10-16", "replay_coverage_end_source": "constant"})
    alerts = []
    monkeypatch.setattr("app.core.alerts.emit_alert", lambda kind, key, msg, **k: alerts.append((kind, key, msg)))
    out = rr.run_monthly(None, now=dt.datetime(2026, 11, 2, 22, 30), out_root=tmp_path, force=True)
    assert out["status"] == "completed" and out["finished"] is True
    doc = json.loads((tmp_path / "rolling_r12_2026-11-02.json").read_text())
    assert doc["midlong"]["mid-a"]["verdict"] == "positive"
    assert doc["midlong"]["mid-b"]["verdict"] == "error" and "top_n" in doc["midlong"]["mid-b"]["error"]
    assert doc["counts"]["error"] == 1 and doc["positive_ratio"] == 1.0  # 崩的不进分母
    assert doc["crashed"]["mid-b"]["days"] == 10
    assert any(k == "rolling_r12_incomplete" and "mid-b" in m for k, _, m in alerts)
    assert "mid-b" in out["summary_text"]


# ---------------------------------------------------------------- 数据健康红项
def test_rolling_r12_health_item_levels():
    done = {"month": "2026-11", "status": "completed", "cutoff": "2026-11-02", "crashed": {}, "replay": {}}
    assert dq.rolling_r12_item(done, "2026-11", 3, 0)["severity"] == dq.GREEN
    crashed = {**done, "crashed": {"mid-b": {"error": "KeyError: 'top_n'"}}}
    red = dq.rolling_r12_item(crashed, "2026-11", 3, 0)
    assert red["severity"] == dq.RED and "mid-b" in red["detail"]
    inc = {**done, "replay": {"incomplete_days": {"mid-c": 2}}}
    assert dq.rolling_r12_item(inc, "2026-11", 3, 0)["severity"] == dq.YELLOW
    # 本月没结果：月初在跑 = 绿；失败 1 次 = 黄；连续失败 2 次 或 过了 6 个交易日 = 红
    old = {**done, "month": "2026-10"}
    assert dq.rolling_r12_item(old, "2026-11", 0, 0)["severity"] == dq.GREEN
    assert dq.rolling_r12_item(None, "2026-11", 1, 1, "KeyError: 'top_n'")["severity"] == dq.YELLOW
    r2 = dq.rolling_r12_item(None, "2026-11", 2, 2, "KeyError: 'top_n'")
    assert r2["severity"] == dq.RED and "top_n" in r2["detail"]
    assert dq.rolling_r12_item(None, "2026-11", 6, 0)["severity"] == dq.RED


def test_check_rolling_r12_reads_file_and_task_runs(tmp_path):
    class _R:
        def __init__(self, rows):
            self.rows = rows

        def all(self):
            return self.rows

    class _DB:
        def execute(self, stmt, params=None):
            return _R([("failed", "'top_n'"), ("failed", "'top_n'")])

    now = dt.datetime(2026, 11, 4, 23, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))
    open_days = ["2026-11-02", "2026-11-03", "2026-11-04"]
    it = dq.check_rolling_r12(_DB(), open_days, now, path=tmp_path / "missing.json")
    assert it["severity"] == dq.RED and "top_n" in it["detail"] and it["key"] == dq.ROLLING_R12_KEY
