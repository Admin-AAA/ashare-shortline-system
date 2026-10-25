"""中长线只读分析：交易明细平铺、诊断指标、运行记录、来源分类（不连库、不联网）。"""
import datetime as dt
import json

from app.services import horizon_analytics as ha
from app.services import midterm_service as ms


def _write(d, name, payload):
    (d / name).write_text(json.dumps(payload, ensure_ascii=False))


def _setup(tmp_path, monkeypatch, realized):
    picks = tmp_path / "picks"
    picks.mkdir()
    monkeypatch.setattr(ms, "OUT_DIR", picks)
    _write(picks, "2026-09-01_mid-repurchase-plan.json", {"strategy_id": "mid-repurchase-plan", "signal_date": "2026-09-01",
           "origin": "replay", "picks": [{"code": "A", "ratio": 0.01}, {"code": "B", "ratio": 0.01}]})
    _write(picks, "2026-09-04_mid-small-cap-weekly.json", {"strategy_id": "mid-small-cap-weekly", "signal_date": "2026-09-04",
           "picks": [{"code": "C", "total_mv": 1e5}]})
    _write(picks, "2026-10-16_mid-repurchase-plan.json", {"strategy_id": "mid-repurchase-plan", "signal_date": "2026-10-16",
           "picks": [{"code": "D", "ratio": 0.01}, {"code": "E", "ratio": 0.01}]})
    _write(picks, "2026-10-16_long-lowturn-lowvol.json", {"strategy_id": "long-lowturn-lowvol", "signal_date": "2026-10-16",
           "picks": [{"code": "F", "ratio": 0.02}]})
    status = tmp_path / "status"
    status.mkdir()
    _write(status, "2026-10-16.json", {
        "mid-repurchase-plan": {"status": "ok", "raw": 2, "recorded_at": "2026-10-16T10:00:00Z"},
        "mid-forecast-beat": {"status": "slots_full", "raw": {"skipped": "slots_full", "held": 25}},
        "long-lowturn-lowvol": {"status": "ok", "raw": 1},
    })
    monkeypatch.setattr(ms, "_realize", lambda db, c, d, h: dict(realized[c]))


REALIZED = {
    "A": {"status": "closed", "ret": 10.0, "days": 20},
    "B": {"status": "closed", "ret": -5.0, "days": 21, "exit_delayed_days": 1},
    "C": {"status": "closed", "ret": 4.0, "days": 20},
    "D": {"status": "holding", "ret": 3.0, "days": 3},
    "E": {"status": "skipped", "cancel_code": "limit_up_open", "reason": "开盘涨停买不进"},
    "F": {"status": "pending"},
}


def test_trades_flatten_and_origin(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, REALIZED)
    rows = ha.trades(None, "mid")
    assert [r["code"] for r in rows] == ["D", "E", "C", "A", "B"]
    origin = {r["code"]: r["origin"] for r in rows}
    # 有 origin=replay → 回放；无 origin 但 status 有当天运行记录 → 实盘；都没有 → 旧文件，不当实盘
    assert origin == {"A": "replay", "B": "replay", "C": "legacy", "D": "live", "E": "live"}
    assert rows[0]["strategy_name"] and rows[0]["hold_days"] == 20 and rows[0]["selection_rank"] == 1
    assert [r["code"] for r in ha.trades(None, "mid", origin="replay")] == ["A", "B"]
    assert [r["code"] for r in ha.trades(None, "long")] == ["F"]


def test_stats_definitions():
    s = ha.stats([{"status": "closed", "ret": 10.0, "days": 20}, {"status": "closed", "ret": 6.0, "days": 20},
                  {"status": "closed", "ret": -4.0, "days": 22, "exit_delayed_days": 2},
                  {"status": "holding", "ret": 50.0}, {"status": "skipped"}, {"status": "pending"}])
    assert s["n"] == 3 and s["win_rate"] == 66.7 and s["avg_ret"] == 4.0 and s["median_ret"] == 6.0
    assert s["pl_ratio"] == 2.0          # 平均盈利 8 / 平均亏损 4
    assert s["profit_factor"] == 4.0     # 盈利总和 16 / 亏损总和 4
    assert s["max_loss"] == -4.0 and s["delayed_exit"] == 1
    assert (s["holding"], s["skipped"], s["pending"], s["total_picks"]) == (1, 1, 1, 6)
    empty = ha.stats([])
    assert empty["n"] == 0 and empty["win_rate"] is None and empty["profit_factor"] is None


def test_metrics_breakdowns(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, REALIZED)
    m = ha.metrics(None, "mid")
    assert m["overview"]["n"] == 3 and m["overview"]["holding"] == 1
    assert m["by_origin"]["replay"]["n"] == 2 and m["by_origin"]["legacy"]["n"] == 1 and m["by_origin"]["live"]["n"] == 0
    by = {s["strategy_id"]: s for s in m["by_strategy"]}
    assert set(by) == {k for k, v in ms.STRATEGIES.items() if v["horizon"] == "mid"}
    assert by["mid-repurchase-plan"]["n"] == 2 and by["mid-earnings-accel"]["n"] == 0
    top = {x["k"]: x for x in m["top_k"]}
    assert top[1]["n"] == 2 and top[10]["n"] == 3    # A(排名1) C(排名1)；B 排名2
    assert [x["month"] for x in m["by_month"]] == ["2026-10", "2026-09"]
    assert ha.metrics(None, "mid", origin="replay")["overview"]["n"] == 2
    w = ha.metrics(None, "mid", window_days=30, today=dt.date(2026, 10, 21))
    assert w["window_start"] == "2026-09-21" and w["overview"]["n"] == 0 and w["overview"]["holding"] == 1


def test_runs(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, REALIZED)
    days = ha.runs("mid")
    assert len(days) == 1 and days[0]["date"] == "2026-10-16"
    items = {i["strategy_id"]: i for i in days[0]["items"]}
    assert items["mid-repurchase-plan"]["picks"] == 2 and items["mid-forecast-beat"]["detail"] == "slots_full"
    assert "long-lowturn-lowvol" not in items
    assert [i["strategy_id"] for i in ha.runs("long")[0]["items"]] == ["long-lowturn-lowvol"]
