"""策略综合分：分数、收缩、沉底、仓位折算、稳定排序（纯函数，不连库、不联网）。"""
import datetime as dt

import pytest

from app.api import midterm as midterm_api
from app.services import strategy_rank as sr

TODAY = dt.date(2026, 10, 25)


def _strat(sid, n=100, avg=2.0, tot=10.0, mdd=-10.0, phases=None, window="2025-10-01~2026-10-16", n3=None):
    ph = phases if phases is not None else {"P19 2019": {"avg": 1.0}, "R12 2025-10~2026-10": {"avg": avg}}
    if n3 is not None:
        ph = {**ph, "N3 2024-01~2026-10": {"avg": n3}}
    return {"id": sid, "metrics": {"recent_12m": {"n": n, "avg": avg, "tot": tot, "mdd": mdd, "win": 50, "pf": 1.5},
                                  "phases": ph, "window": window}}


def test_percentile_ranks_ties_and_single():
    assert sr.percentile_ranks([]) == []
    assert sr.percentile_ranks([5.0]) == [0.5]
    assert sr.percentile_ranks([1.0, 2.0, 3.0]) == [0.0, 0.5, 1.0]
    assert sr.percentile_ranks([1.0, 1.0, 3.0]) == [0.25, 0.25, 1.0]


def test_shrinkage_penalises_small_sample():
    assert sr.shrink_weight(30) == pytest.approx(0.5)
    assert sr.shrink_weight(0) == 0
    out = sr.rank_horizon_strategies([_strat("a", n=40, avg=5.0), _strat("b", n=400, avg=5.0)], TODAY)
    c = {s["id"]: s["rank_components"] for s in out}
    assert c["a"]["c1"] == pytest.approx(5.0 * 40 / 70)
    assert c["b"]["c1"] == pytest.approx(5.0 * 400 / 430)
    assert c["b"]["c1"] > c["a"]["c1"]


def test_score_weights_and_order():
    best = _strat("best", n=200, avg=6.0, tot=30.0, mdd=-5.0, n3=8.0,
                  phases={"P": {"avg": 1.0}, "Q": {"avg": 1.0}})
    mid = _strat("mid", n=200, avg=3.0, tot=15.0, mdd=-10.0, n3=4.0,
                 phases={"P": {"avg": 1.0}, "Q": {"avg": -1.0}})
    worst = _strat("worst", n=200, avg=1.0, tot=2.0, mdd=-20.0, n3=1.0,
                   phases={"P": {"avg": -1.0}, "Q": {"avg": -1.0}})
    out = sr.rank_horizon_strategies([worst, best, mid], TODAY)
    assert [s["id"] for s in out] == ["best", "mid", "worst"]
    assert [s["rank_score"] for s in out] == [100.0, 50.0, 0.0]
    assert [s["rank"] for s in out] == [1, 2, 3]
    assert abs(sum(sr.HORIZON_WEIGHTS.values()) - 1.0) < 1e-9


def test_n3_fallback_to_r12_and_phase_ratio_excludes_n3():
    s = sr.rank_horizon_strategies([_strat("x", avg=2.5)], TODAY)[0]
    assert s["rank_components"]["c3"] == 2.5 and s["rank_components"]["c3_source"] == "R12"
    s = sr.rank_horizon_strategies([_strat("y", avg=2.5, n3=7.0, phases={"P": {"avg": 1.0}, "Q": {"avg": -1.0}})], TODAY)[0]
    assert s["rank_components"]["c3"] == 7.0 and s["rank_components"]["c3_source"] == "N3"
    assert s["rank_components"]["c4"] == 0.5


def test_sinks_with_flags_and_stable_order():
    items = [
        _strat("small", n=29, avg=9.0),
        _strat("ok1", n=100, avg=4.0, tot=20, mdd=-8),
        {"id": "nodata", "metrics": {}},
        _strat("stale", window="2025-01-01~2026-09-01"),
        _strat("ok2", n=100, avg=2.0, tot=8, mdd=-12),
        _strat("nophase", phases={}),
    ]
    out = sr.rank_horizon_strategies(items, TODAY)
    ids = [s["id"] for s in out]
    assert ids[:2] == ["ok1", "ok2"]
    assert ids[2:] == ["small", "nodata", "stale", "nophase"]
    by = {s["id"]: s for s in out}
    assert by["small"]["rank_flags"] == ["insufficient_sample"] and by["small"]["rank_score"] is None
    assert by["nodata"]["rank_flags"] == ["missing_metrics"]
    assert by["stale"]["rank_flags"] == ["stale_data"]
    assert by["nophase"]["rank_flags"] == ["missing_metrics"]
    assert by["small"]["rank"] is None and by["ok1"]["rank"] == 1
    assert by["small"]["rank_flag_labels"] == ["样本不足（n<30）"]
    # 两个可排序策略阶段为正占比并列（各 0.5 百分位 → 各得 7.5 分），其余三项 ok1 全胜
    assert (by["ok1"]["rank_score"], by["ok2"]["rank_score"]) == (92.5, 7.5)


def test_ties_keep_input_order():
    a, b = _strat("a"), _strat("b")
    assert [s["id"] for s in sr.rank_horizon_strategies([a, b], TODAY)] == ["a", "b"]
    assert [s["id"] for s in sr.rank_horizon_strategies([b, a], TODAY)] == ["b", "a"]


def test_does_not_mutate_input():
    src = _strat("a")
    sr.rank_horizon_strategies([src], TODAY)
    assert "rank_score" not in src


def test_notional_conversion_1_30_to_1_20():
    assert sr.notional_scale("long-lowturn-lowvol") == 1.5
    assert sr.notional_scale("long-dividend-lowvol") == 1.5
    assert sr.notional_scale("long-undist-profit-growth") == 1.0
    s = _strat("long-lowturn-lowvol", n=64, avg=1.57, tot=3.4, mdd=-6.7)
    out = sr.rank_horizon_strategies([s], TODAY)[0]["rank_components"]
    assert out["tot_20"] == pytest.approx(5.1) and out["mdd_20"] == pytest.approx(-10.05)
    assert "仓位口径折算" in out["note"]
    assert out["c2"] == pytest.approx(3.4 / 6.7)
    other = sr.rank_horizon_strategies([_strat("z", n=64, avg=1.57, tot=5.1, mdd=-10.05)], TODAY)[0]["rank_components"]
    assert other["tot_20"] == out["tot_20"] and other["mdd_20"] == out["mdd_20"] and "note" not in other


def test_mdd_floor_avoids_blowup():
    out = sr.rank_horizon_strategies([_strat("z", tot=10.0, mdd=0.0)], TODAY)[0]
    assert out["rank_components"]["c2"] == pytest.approx(10.0 / sr.MDD_FLOOR)


def test_window_end_parser():
    assert sr.window_end("2025-10-01~2026-10-13") == dt.date(2026, 10, 13)
    assert sr.window_end("2018-01~2026-10-16") == dt.date(2026, 10, 16)
    assert sr.window_end("分阶段") is None and sr.window_end(None) is None


def _row(sid, n, win, avg, pf, mdd, n2=None, win2=None, avg2=None, pf2=None, mdd2=None, status="positive"):
    r = {"strategy_id": sid, "status": status, "last_12m": {"n": n, "win_rate": win, "avg_return_pct": avg, "pf": pf, "max_drawdown_pct": mdd}}
    if n2 is not None:
        r["recent"] = {"n": n2, "win_rate": win2, "avg_return_pct": avg2, "pf": pf2, "max_drawdown_pct": mdd2}
    return r


def test_short_rank_two_windows_each_half():
    rows = [
        _row("lo", 100, 55, 0.2, 1.1, -30, 50, 50, 0.1, 1.05, -30),
        _row("hi", 100, 70, 3.0, 2.8, -10, 50, 70, 4.0, 3.5, -10),
        _row("md", 100, 65, 1.5, 2.0, -15, 50, 62, 1.0, 1.8, -15),
    ]
    out = sr.rank_short_strategies(rows, "2026-10-13", TODAY)
    assert [s["strategy_id"] for s in out] == ["hi", "md", "lo"]
    assert [s["rank_score"] for s in out] == [100.0, 50.0, 0.0]
    assert out[0]["rank_components"]["window_scores"] == {"last_12m": 100.0, "recent": 100.0}


def test_short_shrinkage_and_missing_recent():
    rows = [_row("few", 35, 80, 5.0, 3.0, -5), _row("many", 400, 80, 5.0, 3.0, -5)]
    out = {s["strategy_id"]: s for s in sr.rank_short_strategies(rows, "2026-10-13", TODAY)}
    assert out["many"]["rank_score"] > out["few"]["rank_score"]
    assert out["few"]["rank_components"]["recent_used"] is False


def test_short_sink_flags():
    rows = [
        _row("small", 20, 90, 9.0, 5.0, -3, 20, 90, 9.0, 5.0, -3),
        _row("a", 100, 60, 1.0, 1.5, -10, 40, 60, 1.0, 1.5, -10),
        _row("pending", 0, 0, 0, None, 0, status="pending"),
        {"strategy_id": "raw", "status": "pending"},
        _row("b", 100, 62, 2.0, 1.7, -12, 40, 62, 2.0, 1.7, -12),
    ]
    out = sr.rank_short_strategies(rows, "2026-10-13", TODAY)
    assert [s["strategy_id"] for s in out] == ["b", "a", "small", "pending", "raw"]
    by = {s["strategy_id"]: s for s in out}
    assert by["small"]["rank_flags"] == ["insufficient_sample"]
    assert by["pending"]["rank_flags"] == ["missing_metrics"] and by["raw"]["rank_flags"] == ["missing_metrics"]
    assert by["small"]["rank_score"] is None and by["b"]["rank"] == 1
    stale = sr.rank_short_strategies(rows[1:2] + rows[4:], "2026-09-01", TODAY)
    assert [s["strategy_id"] for s in stale] == ["a", "b"] and all(s["rank_flags"] == ["stale_data"] for s in stale)


def test_midterm_route_returns_ranked(monkeypatch):
    fake = [_strat("lo", avg=1.0, tot=2, mdd=-20), _strat("hi", avg=5.0, tot=30, mdd=-5)]
    monkeypatch.setattr(midterm_api.svc, "strategies_overview", lambda h, db=None: fake)
    res = midterm_api.mid_strategies(db=None)
    assert [s["id"] for s in res["strategies"]] == ["hi", "lo"]
    assert res["strategies"][0]["rank_score"] == 92.5 and "rank_components" in res["strategies"][0]
    assert [s["id"] for s in midterm_api.long_strategies(db=None)["strategies"]] == ["hi", "lo"]


def test_standalone_metrics_endpoint_ranks_rows(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    from app.api import strategies as strategies_api
    from app.services import standalone_strategies as ss
    from app.services import workbench_service as wb

    path = tmp_path / "standalone_metrics.json"
    path.write_text(json.dumps({
        "generated_at": "2026-10-23 20:33:54", "window": {"start": "2025-10-09", "end": (dt.date.today() - dt.timedelta(days=3)).isoformat(), "recent_start": "2026-05-01"},
        "method": "m", "strategies": [
            _row("lo", 100, 55, 0.2, 1.1, -30, 50, 50, 0.1, 1.05, -30),
            _row("hi", 100, 70, 3.0, 2.8, -10, 50, 70, 4.0, 3.5, -10)]}))
    monkeypatch.setattr(strategies_api, "STANDALONE_METRICS_PATH", path)
    skills = [SimpleNamespace(skill_id=i, name=i.upper()) for i in ("lo", "hi", "new")]
    monkeypatch.setattr(wb, "sync_skills", lambda db: skills)
    monkeypatch.setattr(ss, "standalone_strategy_ids", lambda sk: [s.skill_id for s in sk])
    res = strategies_api.standalone_metrics(db=None)
    ids = [r["strategy_id"] for r in res["strategies"]]
    assert ids[:2] == ["hi", "lo"] and ids[2] == "new"  # 没有指标的 pending 策略沉底
    assert res["strategies"][0]["rank_score"] == 100.0 and res["strategies"][2]["rank_flags"] == ["missing_metrics"]
