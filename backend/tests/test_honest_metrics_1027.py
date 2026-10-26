"""2026-10-27 诚实口径修复：实盘封签、未成交不摊 0%、出票成功判定、健康快照、滚动 R12 统计。纯逻辑测试，不连库。"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from app.services import midterm_live_seal as seal

CN = seal.CN_TZ


@pytest.fixture(autouse=True)
def _weekday_calendar(monkeypatch):
    # 不查库：下一个交易日按工作日规则
    monkeypatch.setattr(seal, "_local_next_open", lambda signal_date: None)


def test_entry_deadline_is_next_weekday_0925():
    assert seal.entry_deadline("2026-10-23").isoformat() == "2026-10-26T09:25:00+08:00"  # 周五 -> 周一
    assert seal.entry_deadline("2026-10-26").isoformat() == "2026-10-27T09:25:00+08:00"


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
    assert doc["generated_at"].startswith("2026-10-26T18:00") and doc["entry_deadline"] == "2026-10-27T09:25:00+08:00"
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

    monkeypatch.setattr(doc, "expected_short_strategies", lambda db: ["a", "b"])
    monkeypatch.setattr(doc, "_live_short_runs", lambda db, d: {"a": {"run_id": "r1", "recommended_count": 0}})
    monkeypatch.setattr(doc, "_midlong_status", lambda d: {"status_file": True, "missing": [], "data_error": []})
    item = dqm.check_daily_output(None, ["2026-10-23", "2026-10-26"], dt.datetime(2026, 10, 26, 19, 0, tzinfo=dqm._CST))
    assert item["severity"] == dqm.RED and "b" in item["detail"]
    monkeypatch.setattr(doc, "_live_short_runs", lambda db, d: {"a": {"run_id": "r1", "recommended_count": 0},
                                                               "b": {"run_id": "r2", "recommended_count": 1}})
    item = dqm.check_daily_output(None, ["2026-10-23", "2026-10-26"], dt.datetime(2026, 10, 26, 19, 0, tzinfo=dqm._CST))
    assert item["severity"] == dqm.GREEN  # 0 只推荐也算出票成功


def test_health_snapshot_write_and_summary(tmp_path):
    from app.services import data_health_snapshot as hs

    p = tmp_path / "snap.jsonl"
    for i, (d, score, ok) in enumerate([("2026-10-22", 90, True), ("2026-10-23", 80, True), ("2026-10-26", 70, False)]):
        hs._write({"trade_date": d, "taken_at": f"{d}T18:40:00+08:00", "score": score, "score_db": score + 5,
                   "penalty": 5, "red": [] if ok else [{"key": "formal:daily_output", "detail": ""}],
                   "yellow": [], "output_ok": ok, "output": {}}, p)
    hs._write({"trade_date": "2026-10-26", "taken_at": "2026-10-26T18:41:00+08:00", "score": 72, "score_db": 77,
               "penalty": 5, "red": [{"key": "formal:daily_output", "detail": ""}], "yellow": [], "output_ok": False,
               "output": {}}, p)  # 同日重跑覆盖
    s = hs.summarize(20, p)
    assert s["days_with_snapshot"] == 3
    assert s["avg_score"] == pytest.approx((90 + 80 + 72) / 3, abs=0.05)
    assert s["output_success_rate"] == pytest.approx(0.667, abs=0.001)
    assert s["output_failed_dates"] == ["2026-10-26"] and s["red_days"] == 1


def test_rolling_trade_stats_and_blackout():
    from app.services.rolling_r12 import in_blackout, trade_stats

    st = trade_stats([1.0] * 20 + [-1.0] * 10)
    assert st["n"] == 30 and st["avg"] == pytest.approx(0.33, abs=0.01) and st["verdict"] == "positive"
    assert trade_stats([1.0] * 5)["verdict"] == "insufficient_sample"
    assert trade_stats([-1.0] * 40)["verdict"] == "negative"
    assert in_blackout(dt.datetime(2026, 10, 27, 10, 0)) and in_blackout(dt.datetime(2026, 10, 27, 17, 0))
    assert not in_blackout(dt.datetime(2026, 10, 27, 22, 30))
