"""长远目标三指标：去重、滚动超额、逐月计数、独有信号判定。"""
import datetime as dt

from app.services import goal_metrics as gm
from app.services.data_quality_monitor import GREEN, YELLOW, goal_metrics_item


def _row(sid, sd, code, ret, bench, status="closed", origin="live_forward"):
    return {"strategy_id": sid, "signal_date": sd, "code": code, "ret": ret, "bench_ret": bench, "status": status, "origin": origin}


def _hist(sid, sd, code, net, bench):
    return {"strategy_id": sid, "signal_date": sd, "code": code, "net": net, "bench": bench}


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
    # 第三项看全期：a 独有 = 04-01 一笔；2024 那笔早于生产段起点（研究段只从 research_rows 来）不算；同日同票的 03-02 不算独有
    assert per["a"]["unique_n"] == 1 and per["a"]["unique_net_sum"] == -1.0 and per["a"]["verdict"] == "insufficient"
    assert per["b"]["unique_n"] == 1 and per["b"]["unique_excess_sum"] == 1.5
    assert per["a"]["n"] == 2


def test_fullhist_unique_uses_research_and_prod_and_skips_historical_replay():
    research = [_hist("a", "2023-05-10", f"{i:06d}", 2.0, 3.0) for i in range(20)]       # 研究段 20 笔，净正、超额负
    research += [_hist("b", "2021-01-10", "900000", 50.0, 0.0)]                           # 2022-04 之前不算
    prod = [_row("a", "2026-02-10", f"{100 + i:06d}", 1.0, 0.0) for i in range(12)]        # 生产段 12 笔
    prod += [_row("a", "2026-02-11", "777777", 99.0, 0.0, origin="historical_replay")]     # 与研究段重叠的口径，不算
    prod += [_row("c", "2026-02-10", "000100", 1.0, 0.0)]                                  # 与 a 同日同票 → a 该笔不独有
    prod += [_row("b", "2026-03-01", f"{200 + i:06d}", -1.0, -2.0) for i in range(30)]     # b 独有 30 笔，净负、超额正 → fail
    g = gm.compute(prod, ["a", "b", "c"], today=dt.date(2026, 11, 27), research_rows=research)
    per = {p["strategy_id"]: p for p in g["per_strategy"]}
    assert per["a"]["unique_n"] == 31 and per["a"]["unique_hist_n"] == 20 and per["a"]["unique_prod_n"] == 11
    assert per["a"]["unique_net_sum"] == 51.0 and per["a"]["unique_excess_sum"] == -9.0 and per["a"]["verdict"] == "watch"
    assert per["b"]["unique_n"] == 30 and per["b"]["unique_net_sum"] == -30.0 and per["b"]["verdict"] == "fail"
    assert g["strategies_failing"] == ["b"] and g["strategies_watch"] == ["a"] and g["strategies_insufficient"] == ["c"]
    assert "beta" in gm.summary_text(g)


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
    assert item["severity"] == YELLOW and "净合计为负" in item["detail"]


def _short(sid, sd, code, net, bench, scope="historical_replay"):
    return {"strategy_id": sid, "signal_date": sd, "code": code, "net": net, "bench": bench, "scope": scope}


def test_compute_short_dedups_recent_and_judges_fullperiod_net_sum():
    rows = [_short("s1", f"2026-{m:02d}-10", f"{i:06d}", 2.0, 1.0) for m in range(1, 11) for i in range(3)]   # 30 笔，净正超额正
    rows += [_short("s2", "2026-06-10", "000000", 2.0, 1.0, scope="live_forward")]                           # 与 s1 同日同票：近 12 月去重一份
    rows += [_short("s2", f"2026-0{m}-15", f"{900 + i:06d}", -0.5, -1.0) for m in range(1, 7) for i in range(5)]  # 30 笔净负超额正 → fail
    rows += [_short("s3", "2024-01-05", "111111", 9.0, 0.0)]                                                    # 全期算、近 12 月不算
    g = gm.compute_short(rows, today=dt.date(2026, 11, 27))
    assert g["dedup_n"] == 60
    per = {p["strategy_id"]: p for p in g["per_strategy"]}
    assert per["s1"]["verdict"] == "pass" and per["s1"]["n"] == 30 and per["s1"]["signal_days"] == 10
    assert per["s2"]["verdict"] == "fail" and per["s2"]["live_n"] == 1 and per["s2"]["net_sum"] == -13.0
    assert per["s3"]["verdict"] == "insufficient" and per["s3"]["net_sum"] == 9.0
    assert g["strategies_failing"] == ["s2"]
    from app.services.data_quality_monitor import goal_metrics_short_item
    item = goal_metrics_short_item(g)
    assert item["severity"] == YELLOW and "s2" in item["detail"]
