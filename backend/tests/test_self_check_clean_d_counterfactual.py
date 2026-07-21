"""D 项去污染反事实（低线 + soft@震荡 合并）单测。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "self_check.py"
_spec = importlib.util.spec_from_file_location("self_check_clean_cf", _SCRIPTS)
sc = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(sc)


def test_pure_pollution_keys_union_orthogonal():
    # hard + pure low + pure soft + soft with hard twin
    rows = [
        ("A", "2026-07-08", "success", 5.0, 72.0, 0, "弱势"),
        ("B", "2026-07-16", "failed", -5.0, 55.0, 0, "弱势"),  # pure low
        ("C", "2026-07-09", "failed", -5.0, 72.0, 1, "震荡"),  # pure soft
        ("D", "2026-07-10", "failed", -5.0, 55.0, 1, "震荡"),  # pure low + soft same key
        ("D", "2026-07-10", "success", 3.0, 72.0, 0, "震荡"),  # hard twin → D not pure
        ("E", "2026-07-09", "failed", -5.0, 72.0, 1, "震荡"),  # soft twin of? no hard
    ]
    keys = sc._pure_pollution_keys(rows)
    assert ("B", "2026-07-16") in keys["pure_low"]
    assert ("C", "2026-07-09") in keys["pure_soft"]
    assert ("E", "2026-07-09") in keys["pure_soft"]
    assert ("D", "2026-07-10") not in keys["union"]  # hard twin
    assert ("A", "2026-07-08") not in keys["union"]


def test_clean_d_counterfactual_wr_up_pl_flat():
    """污染伤 WR 不伤 PL 时，诊断字段应反映 wr_delta 大、pl_delta≈0。"""
    # 构造：raw 含 2 污染失败 + 4 干净（2胜2负），PL 由干净主导
    dedup = [
        ("c1", "2026-07-01", "success", 5.0),
        ("c2", "2026-07-01", "success", 5.0),
        ("c3", "2026-07-02", "failed", -5.0),
        ("c4", "2026-07-02", "failed", -5.0),
        ("p1", "2026-07-09", "failed", -5.0),  # pollution
        ("p2", "2026-07-16", "failed", -5.0),  # pollution
    ]
    poll = {("p1", "2026-07-09"), ("p2", "2026-07-16")}
    last10 = {str(r[1]) for r in dedup}
    cf = sc._clean_d_counterfactual(dedup, poll, last10_dates=last10)
    assert cf is not None
    assert cf["pollution_n"] == 2
    assert cf["full_raw"]["n"] == 6
    assert cf["full_clean"]["n"] == 4
    # raw WR = 2/6 ≈ 33.3；clean WR = 2/4 = 50
    assert cf["full_raw"]["net_wr"] == 33.3
    assert cf["full_clean"]["net_wr"] == 50.0
    assert cf["wr_delta_full"] == pytest.approx(16.7, abs=0.1)
    # PL: raw 盈利 10 / 亏损 20 = 0.5；clean 10/10 = 1.0 → pl 也会升
    # 用更对称的污染使 PL 不变：污染也是 1 胜 1 负
    dedup2 = [
        ("c1", "2026-07-01", "success", 4.0),
        ("c2", "2026-07-01", "failed", -4.0),
        ("p1", "2026-07-09", "success", 4.0),
        ("p2", "2026-07-16", "failed", -4.0),
    ]
    poll2 = {("p1", "2026-07-09"), ("p2", "2026-07-16")}
    cf2 = sc._clean_d_counterfactual(dedup2, poll2, last10_dates={str(r[1]) for r in dedup2})
    assert cf2["full_raw"]["net_wr"] == 50.0
    assert cf2["full_clean"]["net_wr"] == 50.0
    assert cf2["wr_delta_full"] == 0.0
    # 毛 PL 都是 1.0（扣费后略不同但差值应很小）
    assert abs(cf2["pl_delta_full"]) < 0.05


def test_clean_d_counterfactual_empty():
    assert sc._clean_d_counterfactual([], set()) is None


def test_pure_pollution_empty():
    rows = [("A", "2026-07-08", "success", 5.0, 72.0, 0, "弱势")]
    keys = sc._pure_pollution_keys(rows)
    assert keys["union"] == set()
