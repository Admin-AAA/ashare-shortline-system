"""2026-10-27 诚实口径修复：实盘封签、未成交不摊 0%、出票成功判定、健康快照、滚动 R12 统计。纯逻辑测试，不连库。"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from app.services import midterm_live_seal as seal

CN = seal.CN_TZ


def _next_weekday(signal_date: str) -> str:
    d = dt.date.fromisoformat(signal_date) + dt.timedelta(days=1)
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d.isoformat()


@pytest.fixture(autouse=True)
def _weekday_calendar(monkeypatch):
    # 不查库：模拟"交易日历查得到"，下一个开市日 = 下一个工作日
    monkeypatch.setattr(seal, "_local_next_open", _next_weekday)


def test_entry_deadline_is_next_weekday_0915():
    assert seal.entry_deadline("2026-10-23").isoformat() == "2026-10-26T09:15:00+08:00"  # 周五 -> 周一
    assert seal.entry_deadline("2026-10-26").isoformat() == "2026-10-27T09:15:00+08:00"


def test_is_late_and_legacy():
    on_time = {"signal_date": "2026-10-26", **seal.stamp("2026-10-26", dt.datetime(2026, 10, 26, 20, 0, tzinfo=CN))}
    late = {"signal_date": "2026-10-26", **seal.stamp("2026-10-26", dt.datetime(2026, 10, 27, 10, 0, tzinfo=CN))}
    assert seal.is_late(on_time) is False
    assert seal.is_late(late) is True
    assert seal.is_late({"signal_date": "2026-10-16"}) is None  # 旧文件没有封签：不改判


def test_origin_of_marks_late_batches():
    from app.services.horizon_analytics import origin_of

    live = {("2026-10-26", "mid-x")}
    base = {"signal_date": "2026-10-26", "strategy_id": "mid-x"}
    assert origin_of({**base, "origin": "replay"}, live) == "replay"
    assert origin_of({**base, "signal_date": "2026-10-23"}, live) == "legacy"
    assert origin_of(base, live) == "live"  # 旧文件沿用旧规则
    assert origin_of({**base, **seal.stamp("2026-10-26", dt.datetime(2026, 10, 26, 18, 0, tzinfo=CN))}, live) == "live"
    assert origin_of({**base, **seal.stamp("2026-10-26", dt.datetime(2026, 10, 27, 9, 40, tzinfo=CN))}, live) == "late"


def _save(ms, monkeypatch, tmp_path, now, picks, cap=True):
    monkeypatch.setattr(ms, "OUT_DIR", tmp_path)
    monkeypatch.setattr(seal, "now_cn", lambda: now)
    monkeypatch.setattr("app.services.daily_pick_cap.cap_ordered_picks", lambda p: (p, None))
    return ms._save_picks("mid-x", "2026-10-26", picks, apply_daily_cap=cap)


def test_live_batch_not_overwritten_after_entry(monkeypatch, tmp_path):
    from app.services import midterm_service as ms

    evening = dt.datetime(2026, 10, 26, 18, 0, tzinfo=CN)
    assert _save(ms, monkeypatch, tmp_path, evening, [{"code": "000001"}]) == 1
    doc = json.loads((tmp_path / "2026-10-26_mid-x.json").read_text())
    assert doc["generated_at"].startswith("2026-10-26T18:00") and doc["entry_deadline"] == "2026-10-27T09:15:00+08:00"
    # 同一晚重跑（开盘前）允许覆盖
    assert _save(ms, monkeypatch, tmp_path, evening.replace(hour=21), [{"code": "000002"}, {"code": "000003"}]) == 2
    # 开盘后重跑：实盘路径不覆盖，返回已生效只数
    after_open = dt.datetime(2026, 10, 27, 10, 0, tzinfo=CN)
    assert _save(ms, monkeypatch, tmp_path, after_open, [{"code": "600000"}]) == 2
    doc = json.loads((tmp_path / "2026-10-26_mid-x.json").read_text())
    assert [p["code"] for p in doc["picks"]] == ["000002", "000003"]


def test_backfill_after_entry_is_written_but_late(monkeypatch, tmp_path):
    from app.services import midterm_service as ms

    after_open = dt.datetime(2026, 10, 28, 20, 0, tzinfo=CN)
    assert _save(ms, monkeypatch, tmp_path, after_open, [{"code": "000001"}]) == 1
    doc = json.loads((tmp_path / "2026-10-26_mid-x.json").read_text())
    assert seal.is_late(doc) is True


def test_replay_path_still_overwrites(monkeypatch, tmp_path):
    from app.services import midterm_service as ms

    after_open = dt.datetime(2026, 10, 28, 20, 0, tzinfo=CN)
    _save(ms, monkeypatch, tmp_path, after_open, [{"code": "000001"}], cap=False)
    assert _save(ms, monkeypatch, tmp_path, after_open, [{"code": "000002"}], cap=False) == 1
    assert json.loads((tmp_path / "2026-10-26_mid-x.json").read_text())["picks"][0]["code"] == "000002"


def test_run_summary_does_not_spread_unexecuted_as_zero():
    from app.services.workbench_pure import _summarize_replay_analysis_rows

    rows = [
        {"net_return_pct": -5.0, "executed_settled": True, "executed_net_return_pct": -5.07},
        {"net_return_pct": 0.0, "executed_settled": False, "executed_net_return_pct": None},
        {"net_return_pct": 0.0, "executed_settled": False, "executed_net_return_pct": None},
        {"net_return_pct": 1.0, "executed_settled": True, "executed_net_return_pct": 0.93},
    ]
    s = _summarize_replay_analysis_rows(rows)
    assert s["executed_settled_count"] == 2
    assert s["execution_rate"] == 0.5
    assert s["average_executed_net_return"] == pytest.approx(-2.07)
    assert s["average_net_return"] == -1.0  # 旧口径保留（被冲淡）


def test_output_target_date():
    from app.services.daily_output_check import output_target_date

    days = ["2026-10-22", "2026-10-23", "2026-10-26", "2026-10-27"]
    assert output_target_date(days, dt.datetime(2026, 10, 26, 18, 30)) == "2026-10-23"
    assert output_target_date(days, dt.datetime(2026, 10, 26, 18, 40)) == "2026-10-26"
    assert output_target_date(days, dt.datetime(2026, 10, 25, 12, 0)) == "2026-10-23"  # 周末


def test_daily_output_red_when_short_strategy_missing(monkeypatch):
    from app.services import daily_output_check as doc
    from app.services import data_quality_monitor as dqm

    from app.services import midterm_service as ms

    monkeypatch.setattr(doc, "expected_short_strategies", lambda db: ["a", "b"])
    monkeypatch.setattr(doc, "_live_short_runs", lambda db, d: {"a": {"run_id": "r1", "recommended_count": 0}})
    all_ok = {sid: {"status": "no_signal"} for sid in ms.STRATEGIES}
    monkeypatch.setattr(doc, "_midlong_status", lambda d: {"status_file": True, "stored": all_ok})
    item = dqm.check_daily_output(None, ["2026-10-23", "2026-10-26"], dt.datetime(2026, 10, 26, 19, 0, tzinfo=dqm._CST))
    assert item["severity"] == dqm.RED and "b" in item["detail"]
    monkeypatch.setattr(doc, "_live_short_runs", lambda db, d: {"a": {"run_id": "r1", "recommended_count": 0},
                                                               "b": {"run_id": "r2", "recommended_count": 1}})
    item = dqm.check_daily_output(None, ["2026-10-23", "2026-10-26"], dt.datetime(2026, 10, 26, 19, 0, tzinfo=dqm._CST))
    assert item["severity"] == dqm.GREEN  # 0 只推荐也算出票成功


OPEN_DAYS = ["2026-10-21", "2026-10-22", "2026-10-23", "2026-10-26", "2026-10-27"]


def _patch_snapshot(monkeypatch, samples):
    """take_snapshot 依赖的巡检/日历换成假数据：samples 按调用顺序给出 measure() 的结果。"""
    from app.services import data_health_snapshot as hs
    from app.services import data_quality_monitor as dqm

    it = iter(samples)
    monkeypatch.setattr(dqm, "load_open_days", lambda db: OPEN_DAYS)
    monkeypatch.setattr(hs, "measure", lambda db, now, trade_date: {"taken_at": now.isoformat(), **next(it)})
    return hs


def _sample(score, status="ok", quality_ok=True, red=()):
    return {"quality_ok": quality_ok, "score": score if quality_ok else None, "score_db": None, "penalty": None,
            "red": [{"key": k, "detail": ""} for k in red], "yellow": [], "output_status": status,
            "output_ok": status != "failed", "output": {"reason": "x"}}


def test_snapshot_original_is_immutable_and_rechecks_append(monkeypatch, tmp_path):
    p = tmp_path / "snap.jsonl"
    hs = _patch_snapshot(monkeypatch, [_sample(90), _sample(60, "failed"), _sample(85)])
    d = dt.datetime(2026, 10, 26, 18, 40, tzinfo=CN)
    assert hs.take_snapshot(None, mode="original", now=d, path=p)["written"] == "original"
    # 18:40 之后同一天再跑一次 original 模式：不覆盖，只记复核
    assert hs.take_snapshot(None, mode="original", now=d.replace(hour=21), path=p)["written"] == "recheck"
    # 次日 08:55 复核：目标仍是 10-26（10-27 还没过 18:35），只写复核
    r = hs.take_snapshot(None, mode="recheck", now=dt.datetime(2026, 10, 27, 8, 55, tzinfo=CN), path=p)
    assert r["trade_date"] == "2026-10-26" and r["written"] == "recheck"
    rec = hs.load_snapshots(p)[0]
    assert rec["original"]["score"] == 90 and [c["score"] for c in rec["rechecks"]] == [60, 85]
    s = hs.summarize(20, p)
    assert s["avg_score"] == 90 and s["output_success_rate"] == 1.0  # 只用原始记录
    assert s["recheck_failed_dates"] == []  # 最后一次复核已恢复


def test_snapshot_before_1840_is_not_original(monkeypatch, tmp_path):
    p = tmp_path / "snap.jsonl"
    hs = _patch_snapshot(monkeypatch, [_sample(70)])
    r = hs.take_snapshot(None, mode="original", now=dt.datetime(2026, 10, 27, 17, 0, tzinfo=CN), path=p)
    assert r["trade_date"] == "2026-10-26" and r["written"] == "recheck"  # 手动早跑不能占用 18:40 原始记录


def test_snapshot_quality_failure_excluded_from_average(monkeypatch, tmp_path):
    p = tmp_path / "snap.jsonl"
    hs = _patch_snapshot(monkeypatch, [_sample(80), _sample(None, quality_ok=False), _sample(70, "pending"),
                                       _sample(60, "failed", red=["formal:daily_output"])])
    for day in ("2026-10-21", "2026-10-22", "2026-10-23", "2026-10-26"):
        y, m, dd = map(int, day.split("-"))
        hs.take_snapshot(None, mode="original", now=dt.datetime(y, m, dd, 18, 40, tzinfo=CN), path=p)
    s = hs.summarize(20, p)
    assert s["quality_failed_days"] == 1
    assert s["avg_score"] == pytest.approx(70.0)  # (80+70+60)/3，巡检失败那天不计入
    assert s["output_success_rate"] == 0.75  # 待数据不算失败
    assert s["output_failed_dates"] == ["2026-10-26"] and s["output_pending_dates"] == ["2026-10-23"]
    assert s["red_days"] == 1


def test_measure_does_not_use_stale_report_when_quality_fails(monkeypatch):
    from app.services import daily_output_check as doc
    from app.services import data_health_snapshot as hs
    from app.services import data_quality_monitor as dqm

    monkeypatch.setattr(dqm, "run_data_quality_check", lambda db, now=None: {"overall": "error", "error": "boom"})
    monkeypatch.setattr(dqm, "load_report", lambda: pytest.fail("不能回退旧报告"))
    monkeypatch.setattr(hs, "combined_health", lambda *a, **k: pytest.fail("巡检失败时不算分"))
    monkeypatch.setattr(doc, "daily_output_status", lambda db, d, now=None: {"status": "ok", "ok": True, "reason": ""})
    s = hs.measure(None, now=dt.datetime(2026, 10, 26, 18, 40, tzinfo=CN), trade_date="2026-10-26")
    assert s["quality_ok"] is False and s["score"] is None and s["output_status"] == "ok"


def test_snapshot_task_is_completed_even_when_output_fails(monkeypatch):
    from app.services import data_health_snapshot as hs
    from app.services import scheduler_service as ss

    alerts = []
    monkeypatch.setattr("app.core.alerts.emit_alert", lambda kind, key, msg, **k: alerts.append(kind))
    monkeypatch.setattr(hs, "take_snapshot", lambda db, mode: {"trade_date": "2026-10-26", "written": "original",
                                                              "sample": _sample(60, "failed", red=["x"])})
    monkeypatch.setattr(hs, "summarize", lambda days: {"days_with_original": 1, "avg_score": 60, "output_success_rate": 0.0,
                                                       "red_days": 1, "quality_failed_days": 0})
    out = ss._run_data_health_snapshot(None, {"mode": "original"})
    assert out["status"] == "completed" and "盘后出票失败" in out["summary_text"]
    assert alerts == ["daily_output_failed"]


def test_block_trade_pending_until_next_morning():
    from app.services.daily_output_check import classify_midlong

    stored = {"mid-a": {"status": "ok"}}  # 大宗溢价还没有记录
    sids = ["mid-a", "mid-block-trade-premium"]
    at_1840 = classify_midlong(stored, "2026-10-26", dt.datetime(2026, 10, 26, 18, 40), sids)
    assert at_1840 == {"missing": [], "data_error": [], "pending": ["mid-block-trade-premium"]}
    nodata = {**stored, "mid-block-trade-premium": {"status": "data_error"}}
    assert classify_midlong(nodata, "2026-10-26", dt.datetime(2026, 10, 26, 21, 40), sids)["pending"] == \
        ["mid-block-trade-premium"]
    at_0855 = classify_midlong(nodata, "2026-10-26", dt.datetime(2026, 10, 27, 8, 55), sids)
    assert at_0855["data_error"] == ["mid-block-trade-premium"] and not at_0855["pending"]
    # 其它策略缺记录照常算失败
    assert classify_midlong({}, "2026-10-26", dt.datetime(2026, 10, 26, 18, 40), sids)["missing"] == ["mid-a"]


def test_seal_fallback_not_cached_alerts_and_does_not_lock(monkeypatch):
    monkeypatch.undo()  # 去掉 autouse 的假日历，测真实的查表/兜底逻辑
    calls, alerts = [], []
    monkeypatch.setattr(seal, "_found", {})
    monkeypatch.setattr(seal, "_alerted", set())
    monkeypatch.setattr(seal, "_query_next_open", lambda d: calls.append(d) or None)
    monkeypatch.setattr("app.core.alerts.emit_alert", lambda kind, key, msg, **k: alerts.append((kind, key)))
    monkeypatch.setattr(seal, "now_cn", lambda: dt.datetime(2026, 12, 31, 18, 0, tzinfo=CN))
    seal.deadline_info("2025-08-01")  # 历史回放日期查不到日历：不告警
    assert alerts == []
    ddl, src = seal.deadline_info("2026-12-31")
    assert src == seal.SOURCE_FALLBACK and ddl.date().isoformat() == "2027-01-01"
    seal.deadline_info("2026-12-31")
    assert calls.count("2026-12-31") == 2  # 查不到不缓存，日历补上后立即生效
    assert alerts == [("trade_calendar", "seal:2026-12-31")]  # 同一天只告警一次
    late_now = dt.datetime(2027, 1, 5, 10, 0, tzinfo=CN)
    assert seal.past_deadline("2026-12-31", now=late_now) is False  # 兜底截止时间不锁批次
    st = seal.stamp("2026-12-31", now=late_now)
    assert st["deadline_source"] == seal.SOURCE_FALLBACK
    assert seal.is_late({"signal_date": "2026-12-31", **st}) is None  # 兜底截止时间不判补跑
    monkeypatch.setattr(seal, "_query_next_open", lambda d: "2027-01-04")
    assert seal.deadline_info("2026-12-31")[1] == seal.SOURCE_CALENDAR
    assert seal.past_deadline("2026-12-31", now=late_now) is True


def test_short_segments_split_at_replay_coverage_end():
    from app.services.rolling_r12 import SHORT_REPLAY_COVERAGE_END, short_segments

    seg = short_segments("2025-11-03", "2026-11-03")
    assert seg["replay"] == {"scope": "historical_replay", "start": "2025-11-03", "end": SHORT_REPLAY_COVERAGE_END}
    assert seg["live"]["scope"] == "live_forward" and seg["live"]["start"] > SHORT_REPLAY_COVERAGE_END
    assert seg["live"]["end"] == "2026-11-03"
    later = short_segments("2027-11-01", "2028-11-01")
    assert later["replay"] is None and later["live"]["start"] == "2027-11-01"


def test_short_window_stats_lists_segments_separately(monkeypatch):
    from app.services import daily_output_check as doc
    from app.services import outcome_service as os_
    from app.services import rolling_r12 as rr

    monkeypatch.setattr(doc, "expected_short_strategies", lambda db: ["s1"])
    seen = []

    def fake(db, start_date, end_date, sample_scope):
        seen.append((sample_scope, start_date, end_date))
        return [{"strategy_id": "s1", "triggered_count": 90 if sample_scope == "historical_replay" else 6,
                 "avg_return": 1.8 if sample_scope == "historical_replay" else -4.6}]

    monkeypatch.setattr(os_, "metrics_by_strategy", fake)

    class _DB:
        def execute(self, *a, **k):
            class R:
                def scalar(self_inner):
                    return "2026-10-16"
            return R()

    out = rr.short_window_stats(_DB(), "2025-10-26", "2026-10-26")["strategies"]["s1"]
    assert seen == [("historical_replay", "2025-10-26", "2026-10-16"), ("live_forward", "2026-10-17", "2026-10-26")]
    assert out["replay_segment"]["n"] == 90 and out["live_segment"] == {
        "scope": "live_forward", "start": "2026-10-17", "end": "2026-10-26", "n": 6, "avg": -4.6}
    assert out["n"] == 96 and out["avg"] == pytest.approx((90 * 1.8 - 6 * 4.6) / 96, abs=0.01)


def test_month_staging_and_manifest_reused(tmp_path):
    from app.services.rolling_r12 import month_dir, month_manifest

    m1 = month_manifest(tmp_path, "2026-11", "2026-11-02")
    m2 = month_manifest(tmp_path, "2026-11", "2026-11-03")  # 次日续跑：截止日不变
    assert m1 == m2 and m2["cutoff"] == "2026-11-02"
    assert month_dir(tmp_path, "2026-11") == tmp_path / "rolling_r12" / "2026-11"


def test_run_monthly_stop_is_completed_and_resumes(monkeypatch, tmp_path):
    import contextlib as cl

    from app.services import rolling_r12 as rr

    monkeypatch.setattr(rr, "_latest_trade_date", lambda db: "2026-11-02")
    monkeypatch.setattr(rr, "local_first", lambda: cl.nullcontext({"calls": {}, "local": {}, "covered": {}}))
    seen = {}

    def fake_replay(db, start, end, strategies, staging, stop_at=None, **k):
        seen.update(start=start, end=end, staging=staging, stop_at=stop_at)
        return {"stopped": True, "done_through": "2026-03-01", "days": 80}

    monkeypatch.setattr("scripts.rebuild_replay_picks.replay_midlong", fake_replay)
    out = rr.run_monthly(None, now=dt.datetime(2026, 11, 2, 22, 30), out_root=tmp_path)
    assert out["status"] == "completed" and out["finished"] is False
    assert seen["staging"] == tmp_path / "rolling_r12" / "2026-11" / "picks" and seen["stop_at"] == dt.time(0, 0)
    assert seen["end"] == "2026-11-02"


def test_local_first_reads_local_when_covered(monkeypatch, tmp_path):
    import pandas as pd

    from app.services import midterm_service as ms
    from app.services import rolling_r12 as rr

    (tmp_path / "fina").mkdir()
    (tmp_path / "dividend").mkdir()
    (tmp_path / "events").mkdir()
    fina = pd.DataFrame([{c: None for c in rr._LOCAL_FIELDS}])
    fina.loc[0, ["ts_code", "ann_date", "end_date", "or_yoy"]] = ["000001.SZ", "20260825", "20260630", 12.0]
    fina.to_parquet(tmp_path / "fina" / "000001.parquet")
    pd.DataFrame([{"ts_code": "000001.SZ", "end_date": "20251231", "ann_date": "20260801", "div_proc": "实施",
                   "cash_div_tax": 0.3}]).to_parquet(tmp_path / "dividend" / "000001.parquet")
    pd.DataFrame([{"ts_code": "000001.SZ", "ann_date": "20260810", "end_date": None, "proc": "预案", "exp_date": None,
                   "vol": None, "amount": 1.0, "high_limit": None, "low_limit": None}]).to_parquet(
        tmp_path / "events" / "repurchase_2026.parquet")
    upstream = []
    monkeypatch.setattr(ms, "_pro_fetch_with_wait", lambda api, label, alt_params=None, **p: upstream.append(api))
    monkeypatch.setattr(ms, "generate_picks", lambda db, as_of, *a, **k: None)
    with rr.local_first(tmp_path) as st:
        ms.generate_picks(None, "2026-08-20")  # 本地财报覆盖到 08-25（读本地）；分红只到 08-01（走网关）
        f = ms._pro_fetch_with_wait("fina_indicator_vip", "x", period="20260630", fields="ts_code,or_yoy")
        assert list(f.columns) == ["ts_code", "or_yoy"] and len(f) == 1
        ms._pro_fetch_with_wait("dividend", "x", ann_date="20260820")  # 本地只到 08-01 -> 走网关
        ms.generate_picks(None, "2026-08-01")  # 分红本地覆盖到 08-01：读本地
        d = ms._pro_fetch_with_wait("dividend", "x", ann_date="20260801")
        assert len(d) == 1
        r = ms._pro_fetch_with_wait("repurchase", "x", start_date="20260801", end_date="20260805")
        assert len(r) == 0  # 本地覆盖但当天没有公告
    assert upstream == ["dividend"]
    assert st["local"] == {"fina_indicator_vip": 1, "dividend": 1, "repurchase": 1}


def test_rolling_trade_stats_and_blackout():
    from app.services.rolling_r12 import in_blackout, trade_stats

    st = trade_stats([1.0] * 20 + [-1.0] * 10)
    assert st["n"] == 30 and st["avg"] == pytest.approx(0.33, abs=0.01) and st["verdict"] == "positive"
    assert trade_stats([1.0] * 5)["verdict"] == "insufficient_sample"
    assert trade_stats([-1.0] * 40)["verdict"] == "negative"
    assert in_blackout(dt.datetime(2026, 10, 27, 10, 0)) and in_blackout(dt.datetime(2026, 10, 27, 17, 0))
    assert not in_blackout(dt.datetime(2026, 10, 27, 22, 30))
