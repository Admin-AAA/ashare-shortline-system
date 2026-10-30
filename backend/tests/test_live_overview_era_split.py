"""实盘总览不再混入已删除的旧组合方案；两栏（确认买入 / 开盘模拟）与 live-breakdown 同源。"""
from __future__ import annotations

import json
from types import SimpleNamespace

from app.services import live_trade_breakdown as ltb
from app.services import outcome_service as svc

SINGLE = json.dumps([{"skill_id": "s1"}])
COMBO = json.dumps([{"skill_id": "s1"}, {"skill_id": "s2"}])


class _FakeDB:
    """run_ids_for_era 只查 (run_id, preset_id, selected_skills_json)。"""

    def __init__(self, runs):
        self.runs = runs

    def execute(self, stmt):
        rows = [(rid, preset, skills) for rid, (preset, skills) in self.runs.items()]
        return SimpleNamespace(all=lambda: rows)


def test_run_ids_for_era_drops_combo_and_keeps_single():
    db = _FakeDB({
        "r_combo": (ltb.COMBO_PRESET_ID, COMBO),
        "r_single_old_preset": (ltb.COMBO_PRESET_ID, SINGLE),  # 10-14 单策略补跑也带旧 preset_id，仍算单策略
        "r_single": (None, SINGLE),
    })
    db.runs["r_manual_other"] = (None, None)  # 非旧组合、无技能：不是被删的方案，留在现行时代
    ids = ("r_combo", "r_single_old_preset", "r_single", "r_manual_other")
    assert set(ltb.run_ids_for_era(db, ids, "single")) == {"r_single_old_preset", "r_single", "r_manual_other"}
    assert ltb.run_ids_for_era(db, ids, "combo") == ("r_combo",)
    assert ltb.run_ids_for_era(db, ids, None) == ids
    assert ltb.run_ids_for_era(db, ids, "all") == ids


def test_scoped_rows_live_scope_filters_by_era(monkeypatch):
    """overview / by-strategy 等实盘统计共用 _scoped_recommendation_rows：默认只给单策略 run。"""
    seen = {}
    monkeypatch.setattr(svc, "canonical_live_forward_run_ids", lambda db, **k: ("r_combo", "r_single"))

    def fake_filter(db, ids, era):
        seen["era"] = era
        return () if era == "single" and "r_single" not in ids else tuple(i for i in ids if i == "r_single")

    monkeypatch.setattr(ltb, "run_ids_for_era", fake_filter)

    class _DB:
        def execute(self, stmt):
            seen["sql"] = str(stmt)
            return SimpleNamespace(all=lambda: [])

    assert svc._scoped_recommendation_rows(_DB(), sample_scope=svc.LIVE_FORWARD_SCOPE) == []
    assert seen["era"] == "single"


def test_overview_attaches_breakdown_columns_unchanged(monkeypatch):
    breakdown = {"eras": {
        "single": {"settled_count": 14, "confirmed": {"n": 0, "avg_net_return": None, "median_net_return": None, "win_rate": None},
                   "open_simulated": {"n": 14, "avg_net_return": -2.933, "median_net_return": -4.0, "win_rate": 35.7},
                   "confirmed_not_filled_count": 0, "confirmed_not_filled": [], "not_filled_count": 0, "by_strategy": {}},
        "combo": {"settled_count": 16, "confirmed": {"n": 0, "avg_net_return": None, "median_net_return": None, "win_rate": None},
                  "open_simulated": {"n": 9, "avg_net_return": -5.133, "median_net_return": -5.76, "win_rate": 11.1},
                  "confirmed_not_filled_count": 0, "confirmed_not_filled": [], "not_filled_count": 7, "by_strategy": {}},
    }}
    monkeypatch.setattr(ltb, "live_trade_breakdown", lambda db, start_date=None, end_date=None: breakdown)
    seen = {}

    def fake_overview(db, *a, live_era="single", **k):
        seen["live_era"] = live_era
        return {"total_recommendations": 0}

    monkeypatch.setattr(svc, "_overview_metrics", fake_overview)
    monkeypatch.setattr(svc, "_cached", lambda key, factory: factory())
    monkeypatch.setattr(svc, "_historical_cohort_for_metrics", lambda db, **k: None)
    out = svc.overview_metrics(None, sample_scope=svc.LIVE_FORWARD_SCOPE)
    assert seen["live_era"] == "single" and out["era"] == "single"
    single = breakdown["eras"]["single"]
    assert out["fill_split"]["confirmed"] == single["confirmed"]
    assert out["fill_split"]["open_simulated"] == single["open_simulated"]
    assert out["combo_reference"]["open_simulated"]["n"] == 9
    assert out["combo_reference"]["label"] == "已删除的旧组合方案，只作历史参考"
    # era=all 是显式的旧口径：不过滤、不附两栏
    svc.overview_metrics(None, sample_scope=svc.LIVE_FORWARD_SCOPE, era="all")
    assert seen["live_era"] is None
    # 历史回放口径不受时代过滤影响、也不附两栏
    rep = svc.overview_metrics(None, sample_scope=svc.HISTORICAL_REPLAY_SCOPE)
    assert "fill_split" not in rep
