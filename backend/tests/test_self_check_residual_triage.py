"""residual triage：污染/伪活跃/单日簇/稀疏分层（T34）。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "self_check.py"
_spec = importlib.util.spec_from_file_location("self_check_triage", _SCRIPTS)
sc = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(sc)


def _u(rid, td, code="600000", ret=-5.0):
    return {
        "rid": rid,
        "trade_date": td,
        "executed_return": ret,
        "fs": {"code": code, "market_regime": "弱势", "market_sentiment_phase": "活跃"},
    }


def test_triage_priority_pollution_over_pseudo():
    """污染键优先于伪活跃 rid。"""
    unc = [
        _u("rec_20260716_a", "2026-07-16", "000001"),
        _u("rec_20260716_b", "2026-07-16", "000002"),
    ]
    pure = {("000001", "2026-07-16")}
    pseudo = {"rec_20260716_a", "rec_20260716_b"}
    t = sc._triage_residual_poison(
        unc, pure_pollution_keys=pure, pseudo_active_rids=pseudo,
    )
    assert t["counts"]["pure_pollution"] == 1
    assert t["counts"]["pseudo_active"] == 1
    assert t["counts"]["sparse"] == 0


def test_triage_day_cluster_when_share_high():
    """单日占比≥40% 且 n≥4 → day_clustered。"""
    unc = [
        _u(f"rec_20260706_{i}", "2026-07-06", f"60000{i}") for i in range(5)
    ] + [
        _u("rec_20260709_x", "2026-07-09", "601000"),
        _u("rec_20260710_y", "2026-07-10", "601001"),
    ]
    # 5/7≈71% on 07-06
    t = sc._triage_residual_poison(unc, pure_pollution_keys=set(), pseudo_active_rids=set())
    assert t["counts"]["day_clustered"] == 5
    assert "2026-07-06" in t["clustered_days"]
    assert t["counts"]["sparse"] == 2


def test_triage_sparse_when_no_explanations():
    unc = [
        _u("rec_20260610_a", "2026-06-10", "688322"),
        _u("rec_20260709_b", "2026-07-09", "002371"),
        _u("rec_20260716_c", "2026-07-16", "000739"),
    ]
    t = sc._triage_residual_poison(unc)
    assert t["counts"]["sparse"] == 3
    assert t["counts"]["day_clustered"] == 0
    assert "禁止加 entry" in t["note"]


def test_pseudo_cf_returns_pseudo_rids():
    snaps = {
        "2026-07-07": {
            "sample_count": 4948,
            "up_count": 615,
            "limit_up_count": 44,
            "limit_down_count": 54,
        },
        "2026-06-10": {
            "sample_count": 4950,
            "up_count": 1478,
            "limit_up_count": 90,
            "limit_down_count": 51,
        },
    }
    unc = [
        {
            "rid": "rec_20260707_a",
            "trade_date": "2026-07-07",
            "final_score": 79.0,
            "fs": {"market_regime": "弱势", "market_sentiment_phase": "活跃"},
        },
        {
            "rid": "rec_20260610_a",
            "trade_date": "2026-06-10",
            "final_score": 70.0,
            "fs": {"market_regime": "弱势", "market_sentiment_phase": "活跃"},
        },
    ]
    cf = sc._pseudo_active_sentiment_counterfactual(unc, snaps)
    assert cf is not None
    assert "rec_20260707_a" in cf["pseudo_rids"]
    assert "rec_20260610_a" not in cf["pseudo_rids"]


def test_sparse_residual_items_notes_soft_and_modes():
    unc = [
        {
            "rid": "rec_20260610_a",
            "trade_date": "2026-06-10",
            "executed_return": -5.69,
            "final_score": 69.9,
            "exit_reason": "stop_loss_hit",
            "max_return": 1.5,
            "fs": {
                "code": "688322",
                "market_regime": "弱势",
                "market_sentiment_phase": "活跃",
                "chg20": 53.0,
                "pos60": 80.0,
                "score_line_meta": {"dynamic_soft_floor_applied": True},
            },
        },
        {
            "rid": "rec_20260709_b",
            "trade_date": "2026-07-09",
            "executed_return": -5.69,
            "exit_reason": "stop_loss_hit",
            "max_return": 3.02,
            "fs": {
                "code": "002371",
                "market_regime": "震荡",
                "market_sentiment_phase": "高潮",
                "chg20": 41.0,
                "pos60": 82.0,
            },
        },
    ]
    items = sc._sparse_residual_items(unc, ["rec_20260610_a", "rec_20260709_b"])
    assert len(items) == 2
    a = items[0]
    assert a["code"] == "688322"
    assert a["mode"] == "extension_chase"
    assert a["soft_applied"] is True
    assert "soft@" in a["note"]
    b = items[1]
    assert b["mode"] == "gap_roundtrip"
    assert "跳空" in b["note"] or "往返" in b["note"]


def test_soft_allowed_not_sparse():
    """soft@弱势 失败应进 soft_allowed，不进 sparse 可攻池。"""
    unc = [
        {
            "rid": "rec_20260610_a",
            "trade_date": "2026-06-10",
            "executed_return": -5.69,
            "fs": {
                "code": "688322",
                "market_regime": "弱势",
                "market_sentiment_phase": "活跃",
                "score_line_meta": {"dynamic_soft_floor_applied": True},
            },
        },
        {
            "rid": "rec_20260709_b",
            "trade_date": "2026-07-09",
            "executed_return": -5.69,
            "fs": {
                "code": "002371",
                "market_regime": "震荡",
                "market_sentiment_phase": "高潮",
            },
        },
    ]
    t = sc._triage_residual_poison(unc, pure_pollution_keys=set(), pseudo_active_rids=set())
    assert t["counts"]["soft_allowed"] == 1
    assert t["counts"]["sparse"] == 1
    assert "rec_20260610_a" in t["soft_allowed_rids"]
    assert "rec_20260709_b" in t["sparse_rids"]
