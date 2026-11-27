"""长远目标三指标：去重、滚动超额、逐月计数、独有信号判定。"""
import datetime as dt

from app.services import goal_metrics as gm
from app.services.data_quality_monitor import GREEN, YELLOW, goal_metrics_item


def _row(sid, sd, code, ret, bench, status="closed"):
    return {"strategy_id": sid, "signal_date": sd, "code": code, "ret": ret, "bench_ret": bench, "status": status}


def test_dedup_rolling_and_unique_verdicts():
    rows = [
        _row("a", "2026-03-02", "000001", 3.0, 1.0),
        _row("b", "2026-03-02", "000001", 3.0, 1.0),       # 同日同票：去重后一份，a/b 都不算独有
        _row("a", "2026-04-01", "000002", -1.0, 0.0),       # a 独有
        _row("b", "2026-04-01", "000003", 2.0, 0.5),        # b 独有
        _row("a", "2026-05-05", "000004", 1.0, 2.0, status="holding"),  # 持有中不计
        _row("a", "2024-01-01", "000005", 9.0, 0.0),        # 窗口外
    ]
    g = gm.compute(rows, ["a", "b"], today=dt.date(2026, 11, 27))
    assert g["dedup_n"] == 3 and g["raw_n"] == 4
    assert g["rolling_excess"] == round(((3 - 1) + (-1 - 0) + (2 - 0.5)) / 3, 2)
    assert [(m["month"], m["n"]) for m in g["months"]] == [("2026-03", 1), ("2026-04", 2)]
    assert g["positive_months"] == 2 and not g["positive_months_pass"]
    per = {p["strategy_id"]: p for p in g["per_strategy"]}
    assert per["a"]["unique_n"] == 1 and per["a"]["unique_excess"] == -1.0 and per["a"]["verdict"] == "insufficient"
    assert per["b"]["unique_n"] == 1 and per["b"]["unique_excess"] == 1.5
    assert per["a"]["n"] == 2 and per["a"]["excess"] == 0.5


def test_item_turns_yellow_on_any_failure(monkeypatch):
    monkeypatch.setattr(gm, "GOAL_MIN_UNIQUE_TRADES", 1)
    good = [_row("a", f"2026-{m:02d}-10", f"{i:06d}", 2.0, 0.0) for m in range(1, 11) for i in range(3)]
    g = gm.compute(good, ["a"], today=dt.date(2026, 11, 27))
    assert g["rolling_excess_pass"] and g["positive_months_pass"] and not g["strategies_failing"]
    assert goal_metrics_item(g)["severity"] == GREEN
    bad = good + [_row("b", "2026-06-10", "999999", -5.0, 0.0)]
    g2 = gm.compute(bad, ["a", "b"], today=dt.date(2026, 11, 27))
    assert g2["strategies_failing"] == ["b"]
    item = goal_metrics_item(g2)
    assert item["severity"] == YELLOW and "独有信号超额为负" in item["detail"]
